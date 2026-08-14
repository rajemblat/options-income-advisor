from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from options_advisor.broker import get_broker_client
from options_advisor.config import load_settings
from options_advisor.dashboard.components import (
    ACCENT,
    BORDER,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    cached_quotes,
    cached_option_chains,
    get_connection,
    icon,
    inject_theme,
    render_header,
    render_notification_bell,
)
from options_advisor.dashboard.simulator_table import (
    build_broker_open_position_rows,
    build_broker_order_rows,
    build_closed_position_rows,
    build_decision_report_rows,
    build_equity_curve_rows,
    build_open_position_rows,
)
from options_advisor.scheduler.market_calendar import market_session
from options_advisor.simulator import learning, rules
from options_advisor.storage import repository as repo

CONTRACT_MULTIPLIER = 100
_ACTION_LABELS = {"open": "🟢 Abrió", "close": "🔵 Cerró", "skip": "⚪ Salteó", "skip_risk": "🟠 Salteó (riesgo)", "watch": "👁️ Vigilando"}


def _fmt_money(v) -> str:
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def _fmt_pct(v) -> str:
    return f"{v:+.2f}%" if isinstance(v, (int, float)) else "—"


def _fmt_plain(v) -> str:
    return "—" if v is None else str(v)


def _render_symbol_tooltip_table(rows: list[dict], col_specs: list[tuple], bg_fn) -> None:
    """Renderiza una tabla HTML donde, al pasar el MOUSE por el símbolo, aparece un tooltip con el
    precio actual y el % del día del subyacente (pedido usuario 2026-08-04 — la tabla estándar de
    Streamlit no permite tooltip por celda). `col_specs` = lista de (encabezado, key, formateador).
    Cada fila espera 'Precio ahora' y '% día' para armar el tooltip del símbolo."""
    thead = "".join(f"<th style='text-align:left;padding:6px 10px;border-bottom:1px solid {BORDER};"
                    f"color:{TEXT_MUTED};font-weight:600;white-space:nowrap'>{h}</th>" for h, _, _ in col_specs)
    body = []
    for r in rows:
        bg = bg_fn(r)
        price_now = r.get("Precio ahora")
        pct_now = r.get("% día")
        pct_color = "#3fb950" if isinstance(pct_now, (int, float)) and pct_now > 0 else ("#f85149" if isinstance(pct_now, (int, float)) and pct_now < 0 else TEXT_MUTED)
        tds = []
        for _h, key, fmt in col_specs:
            val = fmt(r.get(key))
            if key == "Symbol":
                # Pop-up al pasar el mouse por el ticker: precio actual + % del día del subyacente,
                # EN VIVO (usuario 2026-08-04). Tooltip por clase CSS (.oia-tt), que sí sobrevive al
                # sanitizador de Streamlit — el atributo `title` nativo lo bloquea.
                box = (
                    f"<span class='oia-tt-box'>Precio ahora: {_fmt_money(price_now)} · "
                    f"Día: <span style='color:{pct_color};font-weight:700'>{_fmt_pct(pct_now)}</span></span>"
                )
                tds.append(
                    f"<td style='padding:6px 10px;white-space:nowrap'>"
                    f"<span class='oia-tt' style='font-weight:600'>{val}{box}</span></td>"
                )
            else:
                tds.append(f"<td style='padding:6px 10px;white-space:nowrap'>{val}</td>")
        body.append(f"<tr style='background:{bg}'>{''.join(tds)}</tr>")
    html = (
        f"<table style='border-collapse:collapse;width:100%;font-size:0.86rem;color:{TEXT_PRIMARY}'>"
        f"<thead><tr>{thead}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        f"<div style='color:{TEXT_MUTED};font-size:0.78rem;margin-top:6px'>"
        f"💡 Pasá el mouse por el símbolo (subrayado punteado) y aparece su precio y % del día en vivo.</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def _ctx_of(decision_row) -> dict:
    try:
        return json.loads(decision_row["context_json"]) if decision_row and decision_row["context_json"] else {}
    except (ValueError, TypeError):
        return {}


def _why_prose(ctx: dict) -> str:
    """En qué se basó la IA para abrir, en palabras."""
    m = []
    ivr = ctx.get("iv_rank")
    if isinstance(ivr, (int, float)):
        m.append(f"IV Rank {ivr:.0f} ({'alta' if ivr >= 50 else 'media/baja'})")
    dl = ctx.get("chosen_delta")
    if isinstance(dl, (int, float)):
        m.append(f"delta {dl:.2f}")
    cov = ctx.get("chosen_coverage_pct")
    if isinstance(cov, (int, float)):
        m.append(f"cobertura {cov*100:.1f}%")
    su = ctx.get("support_used")
    if isinstance(su, (int, float)):
        m.append(f"apoyada en el soporte fuerte de ${su:,.2f}")
    elif ctx.get("near_support"):
        m.append("cerca de un soporte fuerte")
    dc = ctx.get("day_change_pct")
    if isinstance(dc, (int, float)):
        m.append(f"la acción venía {'cayendo' if dc < 0 else 'subiendo'} {dc:+.1f}% ese día")
    if ctx.get("volatile"):
        m.append("acción volátil")
    ann = ctx.get("chosen_annualized_return")
    if isinstance(ann, (int, float)):
        m.append(f"anualizado {ann*100:.0f}%")
    if ctx.get("has_event"):
        m.append("evento Fed/earnings cerca")
    return " · ".join(m) if m else "(datos de la decisión no disponibles)"


def _data_grid_rows(ctx: dict, pos_row):
    """TODOS los datos de la posición como lista de (param_key, etiqueta, valor) — base común para
    la tabla de solo lectura y para el voto por casillero (usuario 2026-08)."""
    def f(v, fmt="{}", pct=False, money=False):
        if not isinstance(v, (int, float)):
            return "—"
        if pct:
            return f"{v*100:.1f}%"
        if money:
            return f"${v:,.2f}"
        return fmt.format(v)

    # Derivar lo que es 100% calculable con lo guardado, para que NUNCA salga vacío aunque la
    # posición se haya abierto con el código viejo (usuario 2026-08-05):
    #  · POP ≈ 1 − |delta|
    #  · precio del subyacente al abrir = strike / (1 − cobertura)   [cobertura = (precio−strike)/precio]
    pop = ctx.get("chosen_pop")
    if not isinstance(pop, (int, float)):
        dl = ctx.get("chosen_delta")
        if isinstance(dl, (int, float)):
            pop = round(1 - abs(dl), 4)
    underlying = ctx.get("underlying_price")
    if not isinstance(underlying, (int, float)):
        cov = ctx.get("chosen_coverage_pct")
        if isinstance(cov, (int, float)) and cov < 1:
            underlying = round(pos_row["strike"] / (1 - cov), 2)

    # Desviación estándar (usuario 2026-08-05): movimiento esperado de 1σ hasta el vencimiento
    # (= precio · IV · √(DTE/365)) y a cuántas σ OTM quedó el strike. Cuanto más σ, más colchón.
    iv = ctx.get("chosen_iv")
    dte = ctx.get("chosen_dte")
    sigma_move = None
    strike_sd = None
    if (isinstance(underlying, (int, float)) and underlying > 0 and isinstance(iv, (int, float))
            and isinstance(dte, (int, float)) and dte >= 0):
        sigma_move = underlying * iv * math.sqrt(max(dte, 0.0) / 365.0)
        if sigma_move > 0:
            strike_sd = (underlying - pos_row["strike"]) / sigma_move

    # Colateral y anualizado CALIBRADOS al broker (usuario 2026-08-06): se recalculan con el factor
    # vigente (aprendido del margen real de tus posiciones) para que coincidan con tu cuenta — así
    # también las posiciones ya abiertas muestran el número bueno, no el Reg-T viejo.
    factor = globals().get("_EFFECTIVE_MARGIN_FACTOR", 1.0) or 1.0
    margin_mode = globals().get("_MARGIN_MODE", "naked")
    premium_used = ctx.get("chosen_credit")
    if not isinstance(premium_used, (int, float)):
        premium_used = pos_row["entry_premium"]
    margin_calc = None
    ann_calc = None
    if (isinstance(underlying, (int, float)) and underlying > 0 and isinstance(dte, (int, float)) and dte > 0
            and isinstance(premium_used, (int, float))):
        if margin_mode == "naked":
            margin_calc = round(rules.naked_put_margin(underlying, pos_row["strike"], premium_used) * factor, 2)
        else:
            margin_calc = round(pos_row["strike"] * 100.0, 2)
        if margin_calc and margin_calc > 0:
            ann_calc = (premium_used * 100.0 / margin_calc) * (365.0 / dte)

    # (param_key, etiqueta, valor) — el param_key es estable y se usa para guardar tu voto por
    # casillero (usuario 2026-08-06). El aprendizaje sabe qué keys mapean a un peso del cerebro.
    rows = [
        ("underlying", "Precio subyacente", f(underlying, money=True)),
        ("strike", "Strike", f"${pos_row['strike']:.2f}"),
        ("dte", "DTE", f(ctx.get("chosen_dte"))),
        ("contratos", "Contratos", str(pos_row["quantity"])),
        ("delta", "Delta", f(ctx.get("chosen_delta"), "{:.2f}")),
        ("pop", "POP (prob. OTM)", f(pop, pct=True)),
        ("theta", "Theta", f(ctx.get("chosen_theta"), "{:.3f}")),
        ("iv_rank", "IV Rank", f(ctx.get("iv_rank"), "{:.0f}")),
        ("iv_hv", "IV / HV", f"{f(ctx.get('chosen_iv'), pct=True)} / {f(ctx.get('hv_20d'), pct=True)}"),
        ("sigma", "Desv. estándar (1σ)", f(sigma_move, money=True)),
        ("strike_sigma", "Strike (σ OTM)", f"{strike_sd:.2f}σ" if isinstance(strike_sd, (int, float)) else "—"),
        ("cobertura", "Cobertura", f(ctx.get("chosen_coverage_pct"), pct=True)),
        ("volatil", "Volátil", "sí" if ctx.get("volatile") else ("no" if "volatile" in ctx else "—")),
        ("soporte", "Soporte fuerte usado", f(ctx.get("support_used"), money=True)),
        ("otros_soportes", "Otros soportes", ", ".join(f"${s:,.0f}" for s in ctx["supports_daily"][:4])
         if isinstance(ctx.get("supports_daily"), list) and ctx.get("supports_daily") else "—"),
        ("bid_ask", "Bid / Ask", f"{f(ctx.get('chosen_bid'), money=True)} / {f(ctx.get('chosen_ask'), money=True)}"),
        ("spread", "Spread", f(ctx.get("chosen_spread_pct"), pct=True)),
        ("open_interest", "Open Interest", f(ctx.get("chosen_open_interest"), "{:,}")),
        ("volumen", "Volumen", f(ctx.get("chosen_volume"), "{:,}")),
        ("dia_pct", "% del día", f(ctx.get("day_change_pct"), "{:+.1f}%") if isinstance(ctx.get("day_change_pct"), (int, float)) else "—"),
        ("rsi", "RSI", f(ctx.get("rsi_14"), "{:.0f}")),
        ("prima", "Prima cobrada", f"${pos_row['entry_premium']:.2f}"),
        ("anualizado", "Anualizado", f(ann_calc, pct=True) if ann_calc is not None else f(ctx.get("chosen_annualized_return"), pct=True)),
        ("margen", "Margen (calibrado al broker)", f(margin_calc, money=True) if margin_calc is not None else f(ctx.get("chosen_margin"), money=True)),
    ]
    return rows


def _data_grid_md(ctx: dict, pos_row) -> str:
    """Tabla markdown (solo lectura) con TODOS los datos de la posición (usuario 2026-08)."""
    rows = _data_grid_rows(ctx, pos_row)
    # dos columnas de "Dato: valor"
    half = (len(rows) + 1) // 2
    left, right = rows[:half], rows[half:]
    lines = ["| Dato | Valor | Dato | Valor |", "|---|---|---|---|"]
    for i in range(half):
        l = left[i]
        r = right[i] if i < len(right) else ("", "", "")
        lines.append(f"| {l[1]} | **{l[2]}** | {r[1]} | **{r[2] if r[1] else ''}** |")
    return "\n".join(lines)


_VOTE_OPTS = ["—", "👍", "😐", "👎"]
_VOTE_TO_FB = {"👍": "good", "😐": "normal", "👎": "bad", "—": None}
_FB_TO_IDX = {"good": 1, "normal": 2, "bad": 3}


@st.fragment
def _render_rating(conn, decision_row, key_suffix: str, question: str, param_rows=None) -> None:
    """Bloque de puntuación 👍/😐/👎 + nota. Es un `st.fragment` + `st.form`: al Guardar recarga SOLO
    este bloque, no toda la página — antes recargaba todo (y volvía a pedir datos a Schwab) y la
    pantalla quedaba oscura varios segundos (usuario 2026-08-06). Ahora el guardado es instantáneo;
    la operación desaparece de la lista de pendientes cuando refrescás. "Normal" = neutral (indeciso):
    el aprendizaje lo trata como señal neutra, ni suma ni resta.

    Si se pasa `param_rows` (lista de (param_key, etiqueta, valor)), se muestra además un voto
    👍/😐/👎 POR CADA casillero (usuario 2026-08-06: "votar cada parámetro"). Todo se guarda con el
    mismo botón. El aprendizaje cruza esos votos con los pesos del cerebro."""
    if decision_row is None:
        st.caption("No se encontró la decisión de apertura para puntuar esta operación.")
        return
    did = decision_row["id"]
    cur_idx = _FB_TO_IDX.get(decision_row["user_feedback"], 0)
    stored_params = repo.get_decision_param_feedback(conn, did) if param_rows else {}
    with st.form(key=f"ratingform_{key_suffix}_{did}"):
        rc1, rc2 = st.columns([1, 2])
        with rc1:
            choice = st.radio(question, ["Sin marcar", "👍 Bien", "😐 Normal", "👎 Mal"], index=cur_idx, key=f"fb_{key_suffix}_{did}")
        with rc2:
            note = st.text_area("📝 Tu nota (enseñale con tus palabras)", value=decision_row["user_note"] or "",
                                key=f"note_{key_suffix}_{did}", height=90,
                                placeholder="Ej: buena entrada, venía cayendo y con IV alta / se cerró tarde, debió esperar…")

        param_widgets = {}
        if param_rows:
            with st.expander("🗳️ Votar cada parámetro (opcional) — enseñale qué estuvo bien/mal casillero por casillero"):
                st.caption("Dejá en **—** los que no quieras opinar. Los que el robot usa para decidir "
                           "(delta, cobertura, anualizado, POP, IV rank, día, liquidez, theta) ajustan cómo elige; "
                           "el resto queda guardado como tu historial detallado.")
                for pkey, plabel, pval in param_rows:
                    pc1, pc2 = st.columns([3, 2])
                    with pc1:
                        st.markdown(f"**{plabel}:** {pval}")
                    with pc2:
                        idx = _FB_TO_IDX.get(stored_params.get(pkey), 0)
                        param_widgets[pkey] = st.radio(
                            plabel, _VOTE_OPTS, index=idx, horizontal=True,
                            key=f"pv_{key_suffix}_{did}_{pkey}", label_visibility="collapsed")

        if st.form_submit_button("💾 Guardar puntuación"):
            fb = {"👍 Bien": "good", "😐 Normal": "normal", "👎 Mal": "bad"}.get(choice)
            repo.set_decision_feedback(conn, did, fb)
            repo.set_decision_note(conn, did, note)
            if param_rows:
                votes = {k: _VOTE_TO_FB[v] for k, v in param_widgets.items() if _VOTE_TO_FB.get(v)}
                repo.set_decision_param_feedback(conn, did, votes)
            marca = {"good": "👍 Bien", "normal": "😐 Normal", "bad": "👎 Mal"}.get(fb, "sin marcar")
            extra = f" · {sum(1 for v in param_widgets.values() if _VOTE_TO_FB.get(v))} parámetro(s) votado(s)" if param_rows else ""
            st.success(f"Guardado ✅ ({marca}{extra}). Pasa a **Puntuadas** al refrescar la página.")


def _render_intraday_ratings(conn, strategy: str, open_rows, closed_rows, label_fn, key_prefix: str) -> None:
    """Puntuación 👍/👎 + nota para las operaciones intradía (iron butterfly / iron condor), una por
    posición dentro de un expander. Enlaza cada posición con su decisión de apertura por el
    position_id del contexto (usuario 2026-08-05: "en los iron poner para puntuar")."""
    st.markdown("#### ⭐ Puntuá estas operaciones")
    st.caption("Decile 👍/👎 con una nota — así el robot aprende tu criterio también en el iron. "
               "Al guardar, la operación se va a **Puntuadas ✅** y desaparece de acá cuando refrescás.")
    shown = 0
    had_any = False
    for r in list(open_rows) + list(closed_rows)[:15]:
        dec = repo.get_intraday_open_decision(conn, strategy, r["id"])
        if dec is None:
            continue
        had_any = True
        # Ya puntuada → se oculta de la lista de pendientes (igual que los puts, usuario 2026-08-06).
        if dec["user_feedback"]:
            continue
        with st.expander(f"· sin puntuar  {label_fn(r)}"):
            _render_rating(conn, dec, key_prefix, "¿La IA operó BIEN esta operación (entrada y salida)?")
        shown += 1
    if shown == 0:
        if had_any:
            st.success("¡Listo! No te queda ninguna operación del iron por puntuar (para el período elegido). "
                       "Las que puntuaste están en **Puntuadas ✅**.")
        else:
            st.caption("Todavía no hay operaciones para puntuar (aparecen acá cuando el iron abra alguna).")


st.set_page_config(page_title="Lokshn · Robot", page_icon="🤖", layout="wide", initial_sidebar_state="expanded")
inject_theme()
render_header(
    icon("trending-up", size=24, color=ACCENT),
    "Robot de Trading Automático (paper)",
    "Cuenta simulada de $100,000 con datos REALES de mercado — nunca opera con dinero real. "
    "Vende puts (CSP) con criterios combinados y elige el vencimiento de mejor retorno anualizado. "
    "Cada decisión queda registrada con sus griegos — la base para la capa de IA.",
)

conn = get_connection()
render_notification_bell(conn)

settings = load_settings()
sim = settings.simulator
# Factor de margen vigente (calibrado al broker) + modo — para recalcular colateral/anualizado en la
# tabla de datos y que las posiciones ya abiertas muestren el número bueno (usuario 2026-08-06).
try:
    _EFFECTIVE_MARGIN_FACTOR = learning.effective_broker_margin_factor(conn, sim)
except Exception:
    _EFFECTIVE_MARGIN_FACTOR = getattr(sim, "broker_margin_factor", 1.0)
_MARGIN_MODE = sim.margin_mode
account = repo.get_simulated_account(conn)
if account is None:
    st.info(
        "El robot todavía no corrió ninguna vez — se inicializa automáticamente en la próxima "
        "corrida del scheduler (o desde 'Correr análisis ahora' en la página General)."
    )
    st.stop()

open_rows = repo.get_open_simulated_positions(conn)
closed_rows = repo.get_closed_simulated_positions(conn)
equity_history = repo.get_simulated_equity_history(conn)
stats = repo.get_simulated_performance_stats(conn)
dstats = repo.get_robot_decision_stats(conn)

committed = sum(r["collateral"] for r in open_rows)
# Equity = valor REAL de la cuenta paper: capital inicial + utilidad REALIZADA + utilidad ABIERTA (no
# realizada) de las TRES estrategias (puts + iron condor + butterfly). Bug corregido (usuario 2026-08-13:
# "dice 140K, no coincide con la utilidad real"): la fórmula anterior sumaba el COLATERAL de las abiertas
# como si fuera ganancia y por eso inflaba el equity ~$40K de más.


def _sum_paper(table: str, col: str, status: str) -> float:
    try:
        return conn.execute(f"SELECT COALESCE(SUM({col}), 0) FROM {table} WHERE status = ?", (status,)).fetchone()[0] or 0.0
    except Exception:
        return 0.0


_realized_all = (_sum_paper("simulated_positions", "realized_pnl", "closed")
                 + _sum_paper("iron_condor_positions", "realized_pnl", "closed")
                 + _sum_paper("butterfly_positions", "realized_pnl", "closed"))
_unreal_all = (_sum_paper("simulated_positions", "last_unrealized_pnl", "open")
               + _sum_paper("iron_condor_positions", "last_unrealized_pnl", "open")
               + _sum_paper("butterfly_positions", "last_unrealized_pnl", "open"))
_utilidad_total = _realized_all + _unreal_all
equity = sim.initial_capital + _utilidad_total
total_return_pct = round(_utilidad_total / sim.initial_capital * 100, 2) if sim.initial_capital else 0.0

evaluated_today = dstats["last_date"] == date.today().isoformat()
agent_state = "🟢 ACTIVO" if (sim.enabled and evaluated_today) else ("🟡 EN ESPERA" if sim.enabled else "⏸️ PAUSADO")

# --- Barra de modo + refresh ---
mode_col, refresh_col = st.columns([3, 1])
mode_col.markdown(
    f"<div style='padding:8px 14px;border:1px solid {BORDER};border-radius:10px;color:{TEXT_MUTED}'>"
    f"<b style='color:{ACCENT}'>Modo: Market Paper</b> &nbsp;·&nbsp; paper trading con datos reales de "
    f"Schwab (sin órdenes reales)</div>",
    unsafe_allow_html=True,
)
with refresh_col:
    if st.button("🔄 Actualizar página", use_container_width=True, help="Vuelve a leer la base y actualiza todos los datos de la página."):
        st.rerun()

# --- Tarjetas de estado ---
c1, c2, c3, c4 = st.columns(4)
c1.metric("Equity", f"${equity:,.0f}", f"{total_return_pct:+.2f}%")
c2.metric("Buying power", f"${account['cash']:,.0f}")
c3.metric("Capital utilizado", f"${committed:,.0f}")
c4.metric("Agente", agent_state)

# Utilidad total del paper: $ y % sobre el capital inicial, y en cuántas semanas (desde el día 0 = creación
# de la cuenta simulada) — usuario 2026-08-13.
_paper_plazo = ""
_acc_created = account["created_at"] if (account is not None and "created_at" in account.keys()) else None
if _acc_created:
    try:
        _d0p = date.fromisoformat(str(_acc_created)[:10])
        _diasp = max(0, (date.today() - _d0p).days)
        _paper_plazo = f" · en {_diasp} día(s) = {_diasp / 7.0:.1f} semana(s) desde el día 0 ({_d0p.strftime('%d/%m/%Y')})"
    except (ValueError, TypeError):
        _paper_plazo = ""
st.caption(
    f"💰 **Utilidad total (paper): ${_utilidad_total:+,.2f} = {total_return_pct:+.2f}% sobre "
    f"${sim.initial_capital:,.0f}** — realizada ${_realized_all:+,.2f} (puts + iron condor + butterfly) "
    f"+ abierta ${_unreal_all:+,.2f}{_paper_plazo}."
)

# --- Interruptor MAESTRO: pausar / reanudar TODAS las operaciones (usuario 2026-08) ---
all_paused = repo.is_all_paused(conn)
master_col, master_txt_col = st.columns([1, 4])
with master_col:
    if all_paused:
        if st.button("▶️ Reanudar TODO", type="primary", use_container_width=True,
                     help="Vuelve a encender puts, Iron Butterfly e Iron Condor (limpia todas las pausas)."):
            repo.resume_all(conn)
            st.rerun()
    else:
        if st.button("🛑 Pausar TODO", use_container_width=True,
                     help="Frena TODAS las aperturas nuevas (puts + los dos irons). Lo ya abierto se sigue cerrando normal."):
            repo.set_all_paused(conn, True)
            st.rerun()
with master_txt_col:
    if all_paused:
        st.markdown("🛑 **TODO PAUSADO** — el robot no abre operaciones nuevas de ningún tipo. "
                    "Las posiciones abiertas se siguen manejando y cerrando normal.")
    else:
        st.markdown("🟢 **Robot activo** — usá los botones de cada estrategia (abajo) para pausar solo una, "
                    "o *Pausar TODO* para frenarlas todas de una vez.")

# --- Pausa / reanudación de la venta de puts + tope diario (usuario 2026-08) ---
paused = repo.is_puts_paused(conn)
opens_today = repo.count_puts_opens_today(conn, date.today())
limite = repo.get_max_puts_per_day(conn, sim.max_opens_per_day)
pause_col, count_col, adj_col = st.columns([1.2, 1.8, 2])
with pause_col:
    if paused:
        if st.button("▶️ Reanudar venta de puts", type="primary", use_container_width=True):
            repo.set_puts_paused(conn, False)
            st.rerun()
    else:
        if st.button("⏸️ Pausar venta de puts", use_container_width=True, disabled=all_paused,
                     help="Ya está todo pausado por el interruptor maestro." if all_paused else None):
            repo.set_puts_paused(conn, True)
            st.rerun()
with count_col:
    if all_paused:
        estado_txt = "🛑 **PAUSADO por 'Pausar TODO'**"
    elif paused:
        estado_txt = "⏸️ **PAUSADO** — no abre puts nuevos"
    else:
        estado_txt = "🟢 Vendiendo puts"
    cupo = f"{opens_today}/{limite} tickets hoy" if limite > 0 else f"{opens_today} tickets hoy (sin tope)"
    tope_txt = " · 🚫 tope diario alcanzado" if (limite > 0 and opens_today >= limite) else ""
    st.markdown(f"{estado_txt} &nbsp;·&nbsp; {cupo}{tope_txt}")
with adj_col:
    # Tope diario AJUSTABLE de naked puts (usuario 2026-08-07, trading real): ponelo ANTES de que abra
    # el mercado según cuánta oportunidad veas. Default 5, de 1 a 50. El robot igual solo abre si hay
    # oportunidad — este número es el TECHO, no una obligación.
    st.markdown("**Trades de puts por día (techo)**")
    _adj1, _adj2 = st.columns([2, 1])
    with _adj1:
        _nnew = st.number_input(
            "Máx. por día", min_value=1, max_value=50, value=int(limite), step=1,
            label_visibility="collapsed", key="max_puts_day",
            help="Cuántos naked puts como MÁXIMO abre el robot por día (solo si hay oportunidad). "
                 "Ajustalo antes de que abra el mercado. Default 5.",
        )
    with _adj2:
        if st.button("Guardar", use_container_width=True, key="save_max_puts"):
            repo.set_max_puts_per_day(conn, int(_nnew))
            st.toast(f"✅ Tope diario de naked puts: {int(_nnew)}")
            st.rerun()

# Contadores por pestaña (usuario 2026-08-10: "un número si hay algo sin revisar / actividad de hoy").
# Posiciones/Cerradas = SIN PUNTUAR (lo que te falta revisar); Órdenes/Irons = actividad de HOY.
_hoy_iso = date.today().isoformat()


def _badge_count(sql, args=()):
    try:
        return conn.execute(sql, args).fetchone()[0] or 0
    except Exception:
        return 0


_n_pos = _badge_count(
    "SELECT COUNT(*) FROM simulated_positions p WHERE p.status='open' AND NOT EXISTS "
    "(SELECT 1 FROM robot_decisions d WHERE d.position_id=p.id AND d.user_feedback IS NOT NULL)")
_n_cer = _badge_count(
    "SELECT COUNT(*) FROM simulated_positions p WHERE p.status='closed' AND p.close_date=? AND NOT EXISTS "
    "(SELECT 1 FROM robot_decisions d WHERE d.position_id=p.id AND d.user_feedback IS NOT NULL)", (_hoy_iso,))
_n_ord = _badge_count("SELECT COUNT(*) FROM real_trade_alerts WHERE trade_date=?", (_hoy_iso,))
_n_bf = _badge_count("SELECT COUNT(*) FROM butterfly_positions WHERE entry_date=? OR close_date=?", (_hoy_iso, _hoy_iso))
_n_ic = _badge_count("SELECT COUNT(*) FROM iron_condor_positions WHERE entry_date=? OR close_date=?", (_hoy_iso, _hoy_iso))


def _tab_lbl(base, n):
    return f"{base}  ·  {n}" if n and n > 0 else base


(tab_resumen, tab_decisiones, tab_ordenes, tab_posiciones, tab_cerradas, tab_puntuadas,
 tab_butterfly, tab_condor, tab_aprendizaje, tab_config) = st.tabs(
    ["Resumen", "Decisiones", _tab_lbl("Órdenes", _n_ord), _tab_lbl("Posiciones ⭐", _n_pos),
     _tab_lbl("Cerradas ⭐", _n_cer), "Puntuadas ✅",
     _tab_lbl("Iron Butterfly", _n_bf), _tab_lbl("Iron Condor", _n_ic), "Aprendizaje", "Configuración"]
)

# Buscador de la decisión de apertura de cada posición (para mostrar sus datos y guardar tu
# puntuación). Match por position_id (aperturas nuevas) o por símbolo (las de antes).
_open_decisions = repo.get_robot_decisions(conn, limit=500, action="open")
_dec_by_posid: dict = {}
_dec_by_symbol: dict = {}
for _d in _open_decisions:
    if _ctx_of(_d).get("strategy") == "iron_butterfly":
        continue
    if _d["position_id"] is not None:
        _dec_by_posid.setdefault(_d["position_id"], _d)
    _dec_by_symbol.setdefault(_d["symbol"], _d)  # get_robot_decisions viene DESC → la primera es la más nueva


def _decision_for(pos_row):
    return _dec_by_posid.get(pos_row["id"]) or _dec_by_symbol.get(pos_row["symbol"])


def _is_rated(pos_row) -> bool:
    """¿Ya puntuaste esta operación? (tiene 👍/👎). Las puntuadas se ocultan de las listas de
    pendientes y pasan a la pestaña Puntuadas ✅ (usuario 2026-08-05)."""
    d = _decision_for(pos_row)
    return bool(d and d["user_feedback"])


def _day_dot(ctx: dict) -> str:
    """Círculo del sentido del día en que se abrió: 🔴 la acción bajó (preferido) · 🟢 subió ·
    ⚪ sin dato (usuario 2026-08-05)."""
    dc = ctx.get("day_change_pct")
    if not isinstance(dc, (int, float)):
        return "⚪"
    return "🔴" if dc < 0 else ("🟢" if dc > 0 else "⚪")


def _filter_by_entry_day(rows, key_suffix: str):
    """Filtro por día de APERTURA para las listas de puntuar — así ves cuáles abrió cada día y las
    puntuás por tanda (usuario 2026-08-05)."""
    days = sorted({r["entry_date"] for r in rows}, reverse=True)
    if len(days) <= 1:
        return rows
    sel = st.selectbox("📅 Filtrá por día de apertura", ["Todas"] + days, key=f"dayf_{key_suffix}")
    return rows if sel == "Todas" else [r for r in rows if r["entry_date"] == sel]


def _row_val(r, key):
    """Lee un campo tanto de un sqlite3.Row como de un dict, sin romper si no está."""
    try:
        return r[key]
    except (KeyError, IndexError, TypeError):
        pass
    try:
        return r.get(key)
    except AttributeError:
        return None


def _as_date(v):
    """Convierte una fecha/timestamp ISO ('2026-08-06' o '2026-08-06T13:...') a date, o None."""
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def _exec_time_date(r):
    """Fecha de una fila de ORDEN, cuyo 'Exec Time' viene como '8/3/26 15:41:55' (M/D/YY)."""
    v = _row_val(r, "Exec Time")
    if not v:
        return None
    part = str(v).split(" ")[0]
    try:
        m, d, y = part.split("/")
        return date(2000 + int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def _period_filter(rows, date_key, key_suffix: str, label: str = "📅 Buscar por período"):
    """Filtro por período REUTILIZABLE para todas las pestañas (usuario 2026-08-06: lo quiere en
    órdenes, posiciones, cerradas y los dos irons). Opciones: Todo · Hoy · Últimos 7 días · Este mes
    · y cada día puntual que tenga registros. `date_key` puede ser el nombre del campo (string) o una
    función fila→fecha (para 'Exec Time' con formato de broker)."""
    if not rows:
        return rows
    getter = date_key if callable(date_key) else (lambda r: _as_date(_row_val(r, date_key)))
    dias = sorted({d.isoformat() for r in rows if (d := getter(r)) is not None}, reverse=True)
    opciones = ["Todo", "Hoy", "Últimos 7 días", "Este mes"] + dias
    sel = st.selectbox(label, opciones, key=f"pf_{key_suffix}")
    if sel == "Todo":
        return rows
    hoy = date.today()

    def keep(r):
        d = getter(r)
        if d is None:
            return False
        if sel == "Hoy":
            return d == hoy
        if sel == "Últimos 7 días":
            return d >= hoy - timedelta(days=7)
        if sel == "Este mes":
            return d.year == hoy.year and d.month == hoy.month
        return d.isoformat() == sel  # un día puntual elegido de la lista
    return [r for r in rows if keep(r)]

# ============================ RESUMEN ============================
with tab_resumen:
    left, right = st.columns(2)
    with left:
        st.subheader("Estado de la siguiente evaluación")
        ultima_accion = (
            f"{_ACTION_LABELS.get(dstats['last_action'], dstats['last_action'])} — {dstats['last_reason']}"
            if dstats["last_action"] else "Listo para evaluar"
        )
        st.markdown(f"**Última acción:** {ultima_accion}")
        st.markdown(f"**Última evaluación:** {dstats['last_date'] or 'Sin evaluar todavía'}")
        st.markdown(f"**Fuente:** {'Datos reales de Schwab' if settings.broker.mode == 'schwab' else settings.broker.mode}")
    with right:
        st.subheader("Resumen operativo")
        st.markdown(f"**Rondas ejecutadas:** {dstats['rounds']}")
        st.markdown(f"**Oportunidades rechazadas:** {dstats['skipped']}")
        st.markdown(f"**Aperturas / cierres:** {dstats['opened']} / {dstats['closed']}")
        st.markdown(f"**Posiciones abiertas:** {len(open_rows)}")

    st.subheader("Curva de equity")
    if equity_history:
        df = pd.DataFrame(build_equity_curve_rows(equity_history))
        df["Fecha"] = pd.to_datetime(df["Fecha"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df["Fecha"], y=df["Equity"], name="Equity", mode="lines", line=dict(color=ACCENT, width=2)))
        fig.add_hline(y=sim.initial_capital, line_dash="dot", line_color=TEXT_MUTED, annotation_text="Capital inicial", annotation_position="top left")
        fig.update_layout(
            height=320, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
            font=dict(color=TEXT_PRIMARY, family="system-ui, -apple-system, 'Segoe UI', sans-serif"),
            margin=dict(t=20, b=20), showlegend=False,
        )
        fig.update_xaxes(gridcolor=BORDER, zerolinecolor=BORDER)
        fig.update_yaxes(gridcolor=BORDER, zerolinecolor=BORDER, tickprefix="$")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("Todavía no hay historial de equity — se registra en la primera corrida del scheduler.")

    # Stats y cerradas de las 3 estrategias (se usan para el total combinado, el win rate combinado
    # y el filtro por período — usuario 2026-08-07: el header antes solo mostraba naked puts).
    _bf_stats = repo.get_butterfly_performance_stats(conn)
    _ic_stats = repo.get_condor_performance_stats(conn)
    _closed_condor = repo.get_closed_condor_positions(conn)
    _closed_butterfly = repo.get_closed_butterfly_positions(conn)
    _all_closed = (
        [{"d": r["close_date"], "pnl": r["realized_pnl"] or 0.0, "s": "Naked put"} for r in closed_rows]
        + [{"d": r["close_date"], "pnl": r["realized_pnl"] or 0.0, "s": "Iron Condor"} for r in _closed_condor]
        + [{"d": r["close_date"], "pnl": r["realized_pnl"] or 0.0, "s": "Iron Butterfly"} for r in _closed_butterfly]
    )
    _all_pnls = [x["pnl"] for x in _all_closed]
    _grand_real = round(sum(_all_pnls), 2)
    _grand_wins = sum(1 for p in _all_pnls if p > 0)
    _grand_wr = round(_grand_wins / len(_all_pnls) * 100, 1) if _all_pnls else None

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Win rate (todas)", f"{_grand_wr:.1f}%" if _grand_wr is not None else "N/D",
                 help="Sobre las operaciones CERRADAS de las 3 estrategias juntas (naked + condor + butterfly).")
    col_b.metric("P&L realizado total", f"${_grand_real:,.2f}",
                 help="Suma de las 3 estrategias ya cerradas. Antes acá salía SOLO naked puts (por eso no coincidía con el total de abajo).")
    # Exposición de lo que está ABIERTO ahora: naked = notional de asignación (strike×100×contratos);
    # irons = pérdida máxima definida (usuario 2026-08-07: "saber la exposición de todas las abiertas").
    _open_condor = repo.get_open_condor_positions(conn)
    _open_butterfly = repo.get_open_butterfly_positions(conn)
    _exp_naked = sum((r["strike"] or 0.0) * CONTRACT_MULTIPLIER * (r["quantity"] or 0) for r in open_rows)
    _exp_irons = sum((r["max_loss"] or 0.0) for r in _open_condor) + sum((r["max_loss"] or 0.0) for r in _open_butterfly)
    _exp_total = _exp_naked + _exp_irons
    col_c.metric("Exposición abierta", f"${_exp_total:,.0f}",
                 help="Riesgo de lo ABIERTO ahora. Naked puts: strike×100×contratos (lo que comprarías si te asignan). Irons: pérdida máxima definida.")
    st.caption(f"🎯 Exposición abierta · naked puts (notional): **${_exp_naked:,.0f}** · irons (riesgo definido): **${_exp_irons:,.0f}** · total: **${_exp_total:,.0f}**")

    # --- Filtro de ganancia por período (usuario 2026-08-07: diaria/semanal/mensual/anual) ---
    st.markdown("**📅 Ganancia realizada por período**")
    _per = st.radio("Período", ["Hoy", "Esta semana", "Este mes", "Este año", "Todo"], horizontal=True,
                    index=4, label_visibility="collapsed", key="resumen_periodo")
    _hoy = date.today()

    def _in_period(dstr):
        if not dstr:
            return False
        try:
            dd = date.fromisoformat(dstr)
        except (ValueError, TypeError):
            return False
        if _per == "Hoy":
            return dd == _hoy
        if _per == "Esta semana":
            return dd >= _hoy - timedelta(days=_hoy.weekday())   # desde el lunes de esta semana
        if _per == "Este mes":
            return dd.year == _hoy.year and dd.month == _hoy.month
        if _per == "Este año":
            return dd.year == _hoy.year
        return True   # Todo

    _sel = [x for x in _all_closed if _in_period(x["d"])]
    _sel_pnl = round(sum(x["pnl"] for x in _sel), 2)
    _sel_np = round(sum(x["pnl"] for x in _sel if x["s"] == "Naked put"), 2)
    _sel_ic = round(sum(x["pnl"] for x in _sel if x["s"] == "Iron Condor"), 2)
    _sel_bf = round(sum(x["pnl"] for x in _sel if x["s"] == "Iron Butterfly"), 2)
    pc0, pc1, pc2, pc3 = st.columns(4)
    pc0.metric(f"Total ({_per.lower()})", f"${_sel_pnl:,.2f}", f"{len(_sel)} cerrada(s)", delta_color="off")
    pc1.metric("Naked puts", f"${_sel_np:,.2f}")
    pc2.metric("Iron Condor", f"${_sel_ic:,.2f}")
    pc3.metric("Iron Butterfly", f"${_sel_bf:,.2f}")

    # --- Resultado por estrategia: REALIZADO (cerradas) y NO realizado (abiertas) — usuario 2026-08-06 ---
    st.divider()
    st.markdown("### 💰 Resultado por estrategia")
    _naked_real = sum((r["realized_pnl"] or 0.0) for r in closed_rows)
    _naked_unreal = sum((r["last_unrealized_pnl"] or 0.0) for r in open_rows)
    _total_real = _naked_real + _bf_stats["total_realized_pnl"] + _ic_stats["total_realized_pnl"]
    _total_unreal = _naked_unreal + _bf_stats["open_unrealized_pnl"] + _ic_stats["open_unrealized_pnl"]

    st.caption("**Realizado** = ganancias − pérdidas de las operaciones YA CERRADAS (definitivo). **No realizado** = P&L de las abiertas ahora (todavía se mueve).")
    st.markdown("**✅ Realizado (cerradas):**")
    rr1, rr2, rr3, rr4 = st.columns(4)
    rr1.metric("Naked puts", f"${_naked_real:,.2f}")
    rr2.metric("Iron Butterfly", f"${_bf_stats['total_realized_pnl']:,.2f}")
    rr3.metric("Iron Condor", f"${_ic_stats['total_realized_pnl']:,.2f}")
    rr4.metric("TOTAL realizado", f"${_total_real:,.2f}")
    st.markdown("**⏳ No realizado (abiertas):**")
    ru1, ru2, ru3, ru4 = st.columns(4)
    ru1.metric("Naked puts", f"${_naked_unreal:,.2f}")
    ru2.metric("Iron Butterfly", f"${_bf_stats['open_unrealized_pnl']:,.2f}")
    ru3.metric("Iron Condor", f"${_ic_stats['open_unrealized_pnl']:,.2f}")
    ru4.metric("TOTAL no realizado", f"${_total_unreal:,.2f}")

# ============================ DECISIONES ============================
with tab_decisiones:
    st.info("⭐ Para **puntuar tus operaciones** (👍/👎 + nota) andá a **Posiciones ⭐** (las abiertas) y **Cerradas ⭐** (las que ya cerraron). Esta pestaña es solo el registro de todo lo que el robot fue decidiendo.")
    all_decisions = repo.get_robot_decisions(conn, limit=400)
    # El feedback vive en las aperturas (que pueden estar viejas): contamos sobre _open_decisions.
    n_good = sum(1 for d in _open_decisions if d["user_feedback"] == "good")
    n_bad = sum(1 for d in _open_decisions if d["user_feedback"] == "bad")
    st.markdown(f"**Tu feedback hasta ahora:** 👍 {n_good} · 👎 {n_bad}")
    hist = [{
        "Fecha": d["decision_date"],
        "Símbolo": d["symbol"],
        "Acción": _ACTION_LABELS.get(d["action"], d["action"]),
        "Tu voto": {"good": "👍", "normal": "😐", "bad": "👎"}.get(d["user_feedback"], "—"),
        "Motivo": (d["reason"] or "")[:100],
    } for d in all_decisions]
    if hist:
        st.dataframe(pd.DataFrame(hist), use_container_width=True, hide_index=True)
    else:
        st.caption("El robot todavía no registró decisiones — aparecen en la primera corrida del scheduler.")

# ============================ ÓRDENES (estilo broker) ============================
with tab_ordenes:
    st.caption(
        "Órdenes del robot como en tu broker: 🔴 rojo = venta (abre) · 🟢 verde = compra (cierra). "
        "Órdenes límite (LMT). **Precio ahora** y **% día** = cotización EN VIVO del subyacente."
    )
    # Cotizaciones en vivo (precio + % del día) del subyacente para cada orden (pedido 2026-08-04).
    order_symbols = tuple({r["symbol"] for r in list(open_rows) + list(closed_rows)})
    live_quotes = {}
    if order_symbols:
        try:
            quotes = cached_quotes(order_symbols)
            live_quotes = {s: (q.last_price, q.net_change_pct) for s, q in quotes.items() if q is not None}
        except Exception:
            live_quotes = {}
    order_rows = build_broker_order_rows(open_rows, closed_rows, live_quotes)
    order_rows = _period_filter(order_rows, _exec_time_date, "ordenes", "📅 Buscar órdenes por período")
    if order_rows:
        order_cols = [
            ("Exec Time", "Exec Time", _fmt_plain), ("Side", "Side", _fmt_plain),
            ("Qty", "Qty", _fmt_plain), ("Pos Effect", "Pos Effect", _fmt_plain),
            ("Symbol", "Symbol", _fmt_plain), ("Exp", "Exp", _fmt_plain),
            ("Strike", "Strike", _fmt_money), ("Type", "Type", _fmt_plain),
            ("Price", "Price", _fmt_money), ("C/D", "C/D", _fmt_plain), ("Order Type", "Order Type", _fmt_plain),
        ]
        _render_symbol_tooltip_table(
            order_rows, order_cols,
            bg_fn=lambda r: "rgba(248,81,73,0.14)" if r["Side"] == "SELL" else "rgba(63,185,80,0.14)",
        )
        if closed_rows:
            s1, s2 = st.columns(2)
            s1.metric("Ganancia promedio", f"${stats['avg_win']:,.2f}" if stats["avg_win"] is not None else "N/D")
            s2.metric("Pérdida promedio", f"${stats['avg_loss']:,.2f}" if stats["avg_loss"] is not None else "N/D")
    else:
        st.caption("El robot todavía no ejecutó ninguna orden — aparecen acá cuando abra/cierre posiciones.")

# ============================ POSICIONES (abiertas, P/L en vivo) ============================
with tab_posiciones:
    market_open = market_session() == "abierto"
    if market_open:
        st.caption(
            "Posiciones abiertas con P/L EN VIVO (se actualiza cada vez que refrescás la página; "
            "mirar acá nunca cierra una posición). 🟢 = ganando · 🔴 = perdiendo."
        )
    else:
        st.caption(
            "🌙 **Mercado cerrado** — se muestra el P/L de la **última marca del cierre** (las opciones "
            "no se mueven fuera de horario, así que este es el precio bueno). Vuelve a vivo en la apertura."
        )
    if open_rows:
        _disp_open = _period_filter(open_rows, "entry_date", "posic", "📅 Buscar posiciones por período (día de apertura)")
        live_data: dict = {}
        if market_open:
            # Solo con el mercado abierto se pide la cadena en vivo; cerrado, se usa la última marca
            # guardada por el scheduler (usuario 2026-08-04) — evita el after-hours poco confiable.
            # Quotes Y cadenas CACHEADAS 60s (usuario 2026-08-06): antes cada refresh/guardar-puntuación
            # volvía a pedirle a Schwab la cadena de cada posición y la recarga tardaba varios segundos.
            _psyms = tuple(sorted({row["symbol"] for row in _disp_open}))
            _q = cached_quotes(_psyms)
            _ch = cached_option_chains(_psyms)
            for symbol in _psyms:
                quote = _q.get(symbol)
                chain = _ch.get(symbol)
                if quote is not None and chain is not None:
                    live_data[symbol] = (quote.last_price, quote.net_change_pct, chain)
                else:
                    live_data[symbol] = (None, None, None)

        pos_rows = build_broker_open_position_rows(_disp_open, live_data, date.today())
        pos_cols = [
            ("Symbol", "Symbol", _fmt_plain), ("Qty", "Qty", _fmt_plain),
            ("Bid", "Bid", _fmt_money), ("Days", "Days", _fmt_plain),
            ("Trade Price", "Trade Price", _fmt_money), ("Mark", "Mark", _fmt_money),
            ("P/L %", "P/L %", _fmt_pct), ("P/L Open", "P/L Open", _fmt_money),
            ("BP Effect", "BP Effect", _fmt_money), ("Estado", "Estado", _fmt_plain),
        ]
        _render_symbol_tooltip_table(
            pos_rows, pos_cols,
            bg_fn=lambda r: "rgba(63,185,80,0.14)" if (r["P/L Open"] or 0) > 0 else ("rgba(248,81,73,0.14)" if (r["P/L Open"] or 0) < 0 else "transparent"),
        )
        if market_open and st.button("🔄 Actualizar P/L en vivo"):
            # Este botón SÍ fuerza datos frescos (limpia el cache de 60s); el refresh normal usa lo
            # cacheado y es instantáneo.
            cached_quotes.clear()
            cached_option_chains.clear()
            st.rerun()

        # --- Puntuá cada operación abierta (usuario 2026-08): datos completos + por qué + 👍/👎 + nota ---
        # Una vez que la puntuás, DESAPARECE de acá y pasa a la pestaña "Puntuadas ✅" — así solo
        # ves lo que te falta puntuar y no te confundís (usuario 2026-08-05).
        st.markdown("### ⭐ Puntuá tus operaciones abiertas")
        st.caption("Mirá todos los datos y por qué la IA la abrió, y decile 👍 (bien) o 👎 (mal) con una nota. Al guardar, se va a la pestaña **Puntuadas ✅**. El círculo 🔴/🟢 al lado del símbolo indica si la acción **bajó** (🔴, preferido) o **subió** (🟢) el día que se abrió.")
        _pend_open = [r for r in _disp_open if not _is_rated(r)]
        if not _pend_open:
            st.success("¡Listo! No te queda ninguna operación abierta por puntuar (para el período elegido). Las que puntuaste están en **Puntuadas ✅**.")
        for row in _pend_open:
            d = _decision_for(row)
            ctx = _ctx_of(d)
            dte = ctx.get("chosen_dte", "?")
            with st.container(border=True):
                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                    f"<span style='font-size:1.05rem;font-weight:700'>{_day_dot(ctx)} {row['symbol']} · put ${row['strike']:.0f} · {dte} DTE · x{row['quantity']} · <span style='color:#94a3b8;font-weight:400'>abierta {row['entry_date']}</span></span>"
                    f"<span class='oia-badge neutral'>Sin puntuar</span></div>", unsafe_allow_html=True)
                st.markdown(f"<span class='oia-kicker'>Por qué la abrió</span><br>{_why_prose(ctx)}", unsafe_allow_html=True)
                st.markdown(_data_grid_md(ctx, row))
                _render_rating(conn, d, "open", "¿La IA abrió BIEN esta operación?", param_rows=_data_grid_rows(ctx, row))
    else:
        st.caption("Sin posiciones abiertas.")

# ============================ CERRADAS (puntuar la salida) ============================
with tab_cerradas:
    st.markdown("### ⭐ Puntuá tus operaciones cerradas")
    # Filtro por período (usuario 2026-08-06): por día de CIERRE. Los KPI y la lista de abajo reflejan
    # lo que elijas (ej. "Hoy" = utilidad y operaciones cerradas SOLO hoy).
    _disp_closed = _period_filter(closed_rows, "close_date", "cerradas", "📅 Buscar cerradas por período (día de cierre)")
    # Resumen de la UTILIDAD de las cerradas del período elegido (usuario 2026-08-05).
    if _disp_closed:
        _tot = sum((r["realized_pnl"] or 0.0) for r in _disp_closed)
        _wins = [r for r in _disp_closed if (r["realized_pnl"] or 0) > 0]
        _sc1, _sc2, _sc3, _sc4 = st.columns(4)
        _sc1.metric("Utilidad total (cerradas)", f"${_tot:,.2f}")
        _sc2.metric("Operaciones cerradas", len(_disp_closed))
        _sc3.metric("Ganadoras", f"{len(_wins)}/{len(_disp_closed)}")
        _sc4.metric("Win rate", f"{len(_wins) / len(_disp_closed) * 100:.0f}%")
    st.caption(
        "Las que ya se cerraron (ganadas o perdidas). Mirá cómo salió y decile si estuvo bien: si el "
        "**timing** fue bueno, si debió esperar más, si el cierre fue correcto. Con eso aprende también a SALIR mejor."
    )
    _CLOSE_LBL = {"profit_target": "🎯 objetivo de ganancia", "stop_loss": "🛑 stop-loss",
                  "expired": "⏳ vencimiento", "news_close": "📰 noticia", "dte_close": "📅 cierre por DTE"}
    # Listado COMPLETO de las cerradas del período (usuario 2026-08-06: "en cerradas deben aparecer
    # las operaciones cerradas según el rango de fecha"). Muestra TODAS —ya puntuadas o no—, a
    # diferencia de las tarjetas de abajo que son solo las que faltan puntuar.
    if _disp_closed:
        st.markdown("#### 📋 Cerradas del período")
        _voto = {"good": "👍 Bien", "normal": "😐 Normal", "bad": "👎 Mal"}
        _rows_cerr = []
        for r in sorted(_disp_closed, key=lambda x: (x["close_date"] or ""), reverse=True):
            dd = _decision_for(r)
            fb = dd["user_feedback"] if dd else None
            try:
                _dias = (date.fromisoformat(r["close_date"]) - date.fromisoformat(r["entry_date"])).days
            except (ValueError, TypeError):
                _dias = None
            _rows_cerr.append({
                "Símbolo": r["symbol"],
                "Put": f"${r['strike']:.0f}",
                "Cont.": r["quantity"],
                "Abierta": r["entry_date"],
                "Cerrada": r["close_date"],
                "Días": _dias,
                "Prima ent.": r["entry_premium"],
                "Prima cierre": r["close_premium"],
                "P&L": r["realized_pnl"] or 0.0,
                "Motivo": _CLOSE_LBL.get(r["close_reason"], r["close_reason"]),
                "Puntuada": _voto.get(fb, "· pendiente"),
            })
        st.dataframe(
            pd.DataFrame(_rows_cerr), use_container_width=True, hide_index=True,
            column_config={
                "Prima ent.": st.column_config.NumberColumn(format="$%.2f"),
                "Prima cierre": st.column_config.NumberColumn(format="$%.2f"),
                "P&L": st.column_config.NumberColumn(format="$%.2f"),
            },
        )

    st.caption("Abajo podés **puntuar** las que todavía no calificaste. Al puntuar una, se va a la pestaña **Puntuadas ✅** y desaparece de la lista de pendientes.")
    if not _disp_closed:
        st.info("No hay operaciones cerradas para el período elegido. Cuando una posición llegue al 30% de ganancia (o al stop), se cierra y aparece acá para puntuarla.")
    _pend_closed = [r for r in _disp_closed if not _is_rated(r)]
    if _disp_closed and not _pend_closed:
        st.success("¡Listo! No te queda ninguna operación cerrada por puntuar (para el período elegido). Las que puntuaste están en **Puntuadas ✅**.")
    for row in _pend_closed:
        d = _decision_for(row)
        ctx = _ctx_of(d)
        pnl = row["realized_pnl"] or 0.0
        res_badge = (f"<span class='oia-badge success'>🟢 +${pnl:,.2f}</span>" if pnl > 0
                     else (f"<span class='oia-badge danger'>🔴 ${pnl:,.2f}</span>" if pnl < 0
                           else "<span class='oia-badge neutral'>$0.00</span>"))
        try:
            dias = (date.fromisoformat(row["close_date"]) - date.fromisoformat(row["entry_date"])).days
        except (ValueError, TypeError):
            dias = "?"
        with st.container(border=True):
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                f"<span style='font-size:1.05rem;font-weight:700'>{_day_dot(ctx)} {row['symbol']} · put ${row['strike']:.0f} · <span style='color:#94a3b8;font-weight:400'>abierta {row['entry_date']}</span></span>"
                f"{res_badge}</div>", unsafe_allow_html=True)
            st.markdown(
                f"**Cómo salió:** {_CLOSE_LBL.get(row['close_reason'], row['close_reason'])} · "
                f"estuvo **{dias} día(s)** abierta (del {row['entry_date']} al {row['close_date']}) · "
                f"prima entrada ${row['entry_premium']:.2f} → cierre ${(row['close_premium'] or 0):.2f}"
            )
            st.markdown(f"<span class='oia-kicker'>Por qué la había abierto</span><br>{_why_prose(ctx)}", unsafe_allow_html=True)
            with st.expander("Ver todos los datos de la operación"):
                st.markdown(_data_grid_md(ctx, row))
            _render_rating(conn, d, "closed", "¿Estuvo BIEN cómo salió (timing y cierre)?", param_rows=_data_grid_rows(ctx, row))

# ============================ PUNTUADAS (historial de tu feedback, filtrable) ============================
with tab_puntuadas:
    st.markdown("### ✅ Operaciones que ya puntuaste")
    st.caption(
        "Acá van a parar las que sacaste de las listas de pendientes cuando las puntuaste. El robot "
        "ya tiene tu 👍/👎 y tu nota para aprender — esto es tu historial, filtrable por día y semana. "
        "Columna **Día**: 🔴 la acción bajó ese día (preferido) · 🟢 subió · ⚪ sin dato."
    )
    _PERIODOS = ["Hoy", "Esta semana", "Últimos 30 días", "Todo"]
    periodo = st.radio("Filtrar por", _PERIODOS, index=1, horizontal=True, key="punt_filtro")
    hoy = date.today()
    if periodo == "Hoy":
        since = hoy.isoformat()
    elif periodo == "Esta semana":
        since = (hoy - timedelta(days=hoy.weekday())).isoformat()  # lunes de esta semana
    elif periodo == "Últimos 30 días":
        since = (hoy - timedelta(days=30)).isoformat()
    else:
        since = None
    rated = repo.get_rated_decisions(conn, since_iso=since)
    n_good = sum(1 for r in rated if r["user_feedback"] == "good")
    n_bad = sum(1 for r in rated if r["user_feedback"] == "bad")
    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("Puntuadas", len(rated))
    mc2.metric("👍 Bien", n_good)
    mc3.metric("👎 Mal", n_bad)
    if not rated:
        st.info("No hay operaciones puntuadas en este período. Puntuá alguna en **Posiciones ⭐** o **Cerradas ⭐** y aparece acá.")
    else:
        filas = []
        for r in rated:
            when = (r["feedback_at"] or "")[:16].replace("T", " ")
            estado = "abierta" if r["position_status"] == "open" else ("cerrada" if r["position_status"] == "closed" else "—")
            pnl = r["realized_pnl"]
            filas.append({
                "Día": _day_dot(_ctx_of(r)),
                "Fecha operación": r["decision_date"] or "—",
                "Símbolo": r["symbol"],
                "Strike": f"${r['strike']:.0f}" if r["strike"] is not None else "—",
                "Tu voto": {"good": "👍 Bien", "normal": "😐 Normal", "bad": "👎 Mal"}.get(r["user_feedback"], "—"),
                "Estado": estado,
                "P&L": (f"${pnl:,.2f}" if isinstance(pnl, (int, float)) else "—"),
                "Puntuada": when,
                "Tu nota": (r["user_note"] or ""),
            })
        st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)
        with st.expander("↩️ ¿Te equivocaste? Sacar una puntuación (vuelve a la lista de pendientes)"):
            opciones = {f"{r['symbol']} ${r['strike']:.0f} — {('👍' if r['user_feedback']=='good' else '👎')} ({(r['feedback_at'] or '')[:10]})" if r["strike"] is not None
                        else f"{r['symbol']} — {('👍' if r['user_feedback']=='good' else '👎')}": r["id"] for r in rated}
            sel = st.selectbox("Elegí cuál corregir", list(opciones.keys()), key="punt_undo_sel")
            if st.button("Sacar puntuación", key="punt_undo_btn"):
                repo.set_decision_feedback(conn, opciones[sel], None)
                st.success("Listo — volvió a la lista de pendientes para que la puntúes de nuevo.")
                st.rerun()

# ============================ IRON BUTTERFLY (Estrategia 2) ============================
with tab_butterfly:
    bf = settings.intraday_butterfly
    bf_state = "🟢 ACTIVO" if bf.enabled else "⏸️ APAGADO"
    st.markdown(
        f"**Iron Butterfly 0DTE — {bf.underlying}** &nbsp;·&nbsp; {bf_state} &nbsp;·&nbsp; "
        f"reversión a la SMA{bf.sma_period} en {bf.timeframe_minutes} min"
    )
    st.caption(
        f"Entra cuando el precio se aleja ≥ {bf.distance_threshold_pct:.2%} de la SMA{bf.sma_period} "
        f"(ambas direcciones), arma un butterfly con riesgo máx ${bf.max_collateral:.0f}, y sale a "
        f"+{bf.profit_target_pct:.0%} del crédito (en todo momento) / −${bf.stop_loss:.0f}. Máx {bf.max_open_positions} posición(es) a la vez."
    )
    if not bf.enabled:
        st.info(
            "La estrategia está apagada. Para prenderla, poné `intraday_butterfly.enabled: true` en "
            "`config/settings.yaml` y reiniciá. El motor corre cada minuto durante el mercado en cuanto "
            "el scheduler ('Iniciar Robot.command') esté activo."
        )

    # --- Pausa / reanudación de la apertura de Iron Butterflies (usuario 2026-08) ---
    bf_paused = repo.is_butterfly_paused(conn)
    bfp_col, bft_col = st.columns([1, 3])
    with bfp_col:
        if bf_paused:
            if st.button("▶️ Reanudar Iron Butterfly", type="primary", use_container_width=True, key="bf_resume"):
                repo.set_butterfly_paused(conn, False)
                st.rerun()
        else:
            if st.button("⏸️ Pausar Iron Butterfly", use_container_width=True, key="bf_pause", disabled=all_paused,
                         help="Ya está todo pausado por el interruptor maestro." if all_paused else None):
                repo.set_butterfly_paused(conn, True)
                st.rerun()
    with bft_col:
        if all_paused:
            st.markdown("🛑 **PAUSADO por 'Pausar TODO'**")
        elif bf_paused:
            st.markdown("⏸️ **PAUSADO** — no abre butterflies nuevos (lo abierto se sigue cerrando)")
        else:
            st.markdown("🟢 Operando butterflies por reversión")

    # Freno del día por racha de stop-loss (usuario 2026-08-08).
    _bf_halt = getattr(settings.intraday_butterfly, "stop_loss_streak_halt", 0)
    _bf_streak = repo.butterfly_consecutive_stop_losses_today(conn, date.today())
    if _bf_halt > 0:
        if _bf_streak >= _bf_halt:
            st.error(f"🛑 **Freno del día activo** — {_bf_streak} stop-loss seguidos hoy. No abre más butterflies hasta mañana.", icon="🛑")
        elif _bf_streak > 0:
            st.warning(f"⚠️ {_bf_streak} de {_bf_halt} stop-loss seguidos hoy. Con {_bf_halt} seguidos se frena el resto del día.")

    bstats = repo.get_butterfly_performance_stats(conn)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Abiertas", bstats["open_count"])
    m2.metric("Cerradas", bstats["closed_count"])
    m3.metric("Win rate", f"{bstats['win_rate_pct']:.1f}%" if bstats["win_rate_pct"] is not None else "N/D")
    m4.metric("P&L realizado", f"${bstats['total_realized_pnl']:,.2f}", f"${bstats['open_unrealized_pnl']:,.2f} no real.")

    bf_open = repo.get_open_butterfly_positions(conn)
    st.subheader("Posiciones abiertas")
    if bf_open:
        # Tabla estilo Órdenes con el P/L EN VIVO de cada butterfly abierto (usuario 2026-08-07).
        _bf_tbl = []
        for r in bf_open:
            pl = r["last_unrealized_pnl"] or 0.0
            cred = r["entry_net_credit"] or 0.0
            pct = (pl / (cred * CONTRACT_MULTIPLIER) * 100) if cred else 0.0
            _bf_tbl.append({
                "Estado": "🟢 Ganando" if pl > 0 else ("🔴 Perdiendo" if pl < 0 else "—"),
                "Abierta": (r["entry_ts"] or "")[11:19],
                "Cuerpo": f"{r['body_strike']:.0f}",
                "Dirección": "↓ baja" if r["direction"] == "revert_down" else "↑ alza",
                "Crédito $": round(cred, 2),
                "P/L open $": round(pl, 2),
                "P/L % (del crédito)": round(pct, 1),
            })
        st.dataframe(pd.DataFrame(_bf_tbl), use_container_width=True, hide_index=True,
                     column_config={"Crédito $": st.column_config.NumberColumn(format="$%.2f"),
                                    "P/L open $": st.column_config.NumberColumn(format="$%.2f")})
        _bf_tot = sum((r["last_unrealized_pnl"] or 0.0) for r in bf_open)
        st.caption(f"💰 **P/L abierto total (butterflies): ${_bf_tot:,.2f}**. Tocá cada fila de abajo para ver las 4 patas.")
        for r in bf_open:
            pl = r["last_unrealized_pnl"] or 0.0
            dot = "🟢" if pl > 0 else ("🔴" if pl < 0 else "⚪")
            dirn = "↓ reversión a la baja" if r["direction"] == "revert_down" else "↑ reversión al alza"
            label = (f"{dot} cuerpo {r['body_strike']:.0f}  ·  crédito ${r['entry_net_credit']:.2f}  ·  "
                     f"P/L ${pl:,.2f}  ·  abierta {(r['entry_ts'] or '')[11:19]}")
            with st.expander(label):
                st.markdown(
                    f"**{dirn} · Las 4 patas (0DTE {r['underlying']}):**\n\n"
                    f"- 🔻 **Vende PUT ${r['body_strike']:.0f}** (cuerpo)\n"
                    f"- 🔻 **Vende CALL ${r['body_strike']:.0f}** (cuerpo)\n"
                    f"- 🛡️ Compra PUT ${r['long_put_strike']:.0f} (ala — protección abajo)\n"
                    f"- 🛡️ Compra CALL ${r['long_call_strike']:.0f} (ala — protección arriba)"
                )
                mc1, mc2, mc3 = st.columns(3)
                mc1.metric("Crédito cobrado", f"${r['entry_net_credit']:.2f}")
                mc2.metric("Riesgo máx", f"${r['max_loss']:.2f}")
                mc3.metric("P/L no realizado", f"${pl:,.2f}")
    else:
        st.caption("Sin posiciones abiertas del butterfly.")

    bf_closed = repo.get_closed_butterfly_positions(conn, limit=100)
    st.subheader("Cerradas")
    bf_closed = _period_filter(bf_closed, "close_ts", "bf_cerr", "📅 Buscar butterflies cerrados por período")
    if bf_closed:
        _RSN = {"profit_target": "🟢 objetivo", "stop_loss": "🔴 stop", "expired": "vencimiento"}
        crows = [{
            "Dirección": "↓ revert" if r["direction"] == "revert_down" else "↑ revert",
            "Cuerpo": r["body_strike"],
            "Crédito": r["entry_net_credit"],
            "Cierre": r["close_value"],
            "Motivo": _RSN.get(r["close_reason"], r["close_reason"]),
            "P&L": r["realized_pnl"] or 0.0,
            "Hora cierre": (r["close_ts"] or "")[11:19],
        } for r in bf_closed]
        st.dataframe(
            pd.DataFrame(crows), use_container_width=True, hide_index=True,
            column_config={
                "Cuerpo": st.column_config.NumberColumn(format="%.0f"),
                "Crédito": st.column_config.NumberColumn(format="$%.2f"),
                "Cierre": st.column_config.NumberColumn(format="$%.2f"),
                "P&L": st.column_config.NumberColumn(format="$%.2f"),
            },
        )
    else:
        st.caption("Todavía no cerró ninguna operación del butterfly.")

    st.divider()
    _render_intraday_ratings(
        conn, "iron_butterfly", bf_open, bf_closed,
        lambda r: f"{'↓' if r['direction']=='revert_down' else '↑'} cuerpo {r['body_strike']:.0f} · crédito ${r['entry_net_credit']:.2f} · "
                  f"{'abierta' if r['status']=='open' else 'cerrada'}",
        "bf",
    )


# ============================ IRON CONDOR (Estrategia 3) ============================
with tab_condor:
    ic = settings.intraday_condor
    ic_state = "🟢 ACTIVO" if ic.enabled else "⏸️ APAGADO"
    st.markdown(f"**Iron Condor 0DTE — {ic.underlying}** &nbsp;·&nbsp; {ic_state} &nbsp;·&nbsp; para días calmos")
    st.caption(
        f"En días de POCO movimiento (rango intradía ≤ {ic.calm_range_pct:.2%}), entre las {ic.entry_window_start} "
        f"y las {ic.entry_window_end} ET, vende put+call a delta ≤ {ic.short_delta_max:.2f} (donde mejor pague), "
        f"alas de {ic.wing_width:.0f} puntos. Cierra al {ic.profit_target_pct:.0%} del crédito o con −${ic.stop_loss_dollars:.0f}. "
        + (f"Sin tope diario · hasta {ic.max_open_positions} abiertos a la vez."
           if ic.max_per_day <= 0 else f"Hasta {ic.max_per_day} por día.")
    )
    if not ic.enabled:
        st.info("Apagada. Prendela con `intraday_condor.enabled: true` en `config/settings.yaml` y reiniciá el robot.")

    # --- Pausa / reanudación de la apertura de Iron Condors (usuario 2026-08) ---
    ic_paused = repo.is_condor_paused(conn)
    ic_opens_today = repo.count_condor_opens_today(conn, date.today())
    icp_col, ict_col = st.columns([1, 3])
    with icp_col:
        if ic_paused:
            if st.button("▶️ Reanudar Iron Condor", type="primary", use_container_width=True, key="ic_resume"):
                repo.set_condor_paused(conn, False)
                st.rerun()
        else:
            if st.button("⏸️ Pausar Iron Condor", use_container_width=True, key="ic_pause", disabled=all_paused,
                         help="Ya está todo pausado por el interruptor maestro." if all_paused else None):
                repo.set_condor_paused(conn, True)
                st.rerun()
    with ict_col:
        if all_paused:
            ic_estado = "🛑 **PAUSADO por 'Pausar TODO'**"
        elif ic_paused:
            ic_estado = "⏸️ **PAUSADO** — no abre condors nuevos (lo abierto se sigue cerrando)"
        else:
            ic_estado = "🟢 Operando condors en días calmos"
        _open_ct = len(repo.get_open_condor_positions(conn))
        if ic.max_per_day <= 0:
            cupo_ic = f"{ic_opens_today} hoy (sin tope) · {_open_ct}/{ic.max_open_positions} abiertos"
            tope_ic = " · 🚫 llegó al máx. de abiertos a la vez" if _open_ct >= ic.max_open_positions else ""
        else:
            cupo_ic = f"{ic_opens_today}/{ic.max_per_day} hoy"
            tope_ic = " · 🚫 tope diario alcanzado" if ic_opens_today >= ic.max_per_day else ""
        st.markdown(f"{ic_estado} &nbsp;·&nbsp; {cupo_ic}{tope_ic}")

    # Freno del día por racha de stop-loss (usuario 2026-08-08): avisá si hoy quedó frenado.
    _ic_halt = getattr(ic, "stop_loss_streak_halt", 0)
    _ic_streak = repo.condor_consecutive_stop_losses_today(conn, date.today())
    if _ic_halt > 0:
        if _ic_streak >= _ic_halt:
            st.error(f"🛑 **Freno del día activo** — {_ic_streak} stop-loss seguidos hoy. No abre más condors hasta mañana.", icon="🛑")
        elif _ic_streak > 0:
            st.warning(f"⚠️ {_ic_streak} de {_ic_halt} stop-loss seguidos hoy. Con {_ic_halt} seguidos se frena el resto del día.")

    cstats = repo.get_condor_performance_stats(conn, date.today())
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Abiertos", cstats["open_count"])
    c2.metric("Cerrados", cstats["closed_count"])
    c3.metric("Win rate", f"{cstats['win_rate_pct']:.1f}%" if cstats["win_rate_pct"] is not None else "N/D")
    # Ganancia REALIZADA de HOY (usuario 2026-08-12): lo que se cerró en ganancia/pérdida en el día.
    c4.metric("Ganancia de hoy", f"${cstats['realized_pnl_today']:,.2f}",
              f"{cstats['closed_count_today']} cerrado(s) hoy", delta_color="off")
    c5.metric("P&L realizado", f"${cstats['total_realized_pnl']:,.2f}", f"${cstats['open_unrealized_pnl']:,.2f} no real.")

    ic_open = repo.get_open_condor_positions(conn)
    st.subheader("Posiciones abiertas")
    if ic_open:
        # Tabla estilo Órdenes con el P/L EN VIVO de cada condor abierto (usuario 2026-08-07: "saber
        # cuánto van ganando"). El P/L lo marca el robot cada minuto; refrescá para verlo al día.
        _ic_tbl = []
        for r in ic_open:
            pl = r["last_unrealized_pnl"] or 0.0
            cred = r["entry_net_credit"] or 0.0
            pct = (pl / (cred * CONTRACT_MULTIPLIER) * 100) if cred else 0.0
            _ic_tbl.append({
                "Estado": "🟢 Ganando" if pl > 0 else ("🔴 Perdiendo" if pl < 0 else "—"),
                "Abierto": (r["entry_ts"] or "")[11:19],
                "Cortos P/C": f"{r['short_put_strike']:.0f} / {r['short_call_strike']:.0f}",
                "Crédito $": round(cred, 2),
                "P/L open $": round(pl, 2),
                "P/L % (del crédito)": round(pct, 1),
                "Riesgo máx $": round(r["max_loss"] or 0.0, 2),
                "Rango ganancia": (f"{r['lower_breakeven']:.0f}–{r['upper_breakeven']:.0f}" if r["lower_breakeven"] else "—"),
            })
        st.dataframe(pd.DataFrame(_ic_tbl), use_container_width=True, hide_index=True,
                     column_config={"Crédito $": st.column_config.NumberColumn(format="$%.2f"),
                                    "P/L open $": st.column_config.NumberColumn(format="$%.2f"),
                                    "Riesgo máx $": st.column_config.NumberColumn(format="$%.2f")})
        _ic_tot = sum((r["last_unrealized_pnl"] or 0.0) for r in ic_open)
        st.caption(f"💰 **P/L abierto total (condors): ${_ic_tot:,.2f}** · cierre: {ic.profit_target_early_pct:.0%} si llega en los "
                   f"primeros {ic.early_window_minutes:.0f} min (para reentrar), si no {ic.profit_target_pct:.0%} · stop −${ic.stop_loss_dollars:.0f}. "
                   "Tocá cada fila de abajo para ver las 4 patas.")
        for r in ic_open:
            pl = r["last_unrealized_pnl"] or 0.0
            dot = "🟢" if pl > 0 else ("🔴" if pl < 0 else "⚪")
            label = (f"{dot} SP {r['short_put_strike']:.0f} / SC {r['short_call_strike']:.0f}  ·  "
                     f"crédito ${r['entry_net_credit']:.2f}  ·  P/L ${pl:,.2f}  ·  abierto {(r['entry_ts'] or '')[11:19]}")
            with st.expander(label):
                st.markdown(
                    f"**Las 4 patas (0DTE {r['underlying']}):**\n\n"
                    f"- 🔻 **Vende PUT ${r['short_put_strike']:.0f}** (corto — cobra prima)\n"
                    f"- 🔻 **Vende CALL ${r['short_call_strike']:.0f}** (corto — cobra prima)\n"
                    f"- 🛡️ Compra PUT ${r['long_put_strike']:.0f} (ala — protección abajo)\n"
                    f"- 🛡️ Compra CALL ${r['long_call_strike']:.0f} (ala — protección arriba)"
                )
                mc1, mc2, mc3 = st.columns(3)
                mc1.metric("Crédito cobrado", f"${r['entry_net_credit']:.2f}")
                mc2.metric("Riesgo máx", f"${r['max_loss']:.2f}")
                mc3.metric("P/L no realizado", f"${pl:,.2f}")
                if r["lower_breakeven"] and r["upper_breakeven"]:
                    st.caption(
                        f"Rango de ganancia: **${r['lower_breakeven']:.2f} – ${r['upper_breakeven']:.2f}** — "
                        f"gana si {r['underlying']} queda entre esos dos. Cierra a {ic.profit_target_early_pct:.0%} temprano / {ic.profit_target_pct:.0%} después, o −${ic.stop_loss_dollars:.0f}."
                    )
    else:
        st.caption("Sin condors abiertos. En un día calmo dentro de la ventana horaria, abre solo.")

    ic_closed = repo.get_closed_condor_positions(conn, limit=100)
    st.subheader("Cerrados")
    ic_closed = _period_filter(ic_closed, "close_ts", "ic_cerr", "📅 Buscar condors cerrados por período")
    if ic_closed:
        _RSN = {"profit_target": "🟢 objetivo 60%", "stop_loss": "🔴 stop $100", "expired": "vencimiento"}
        crows = [{
            "Put/Call corto": f"{r['short_put_strike']:.0f} / {r['short_call_strike']:.0f}",
            "Crédito": r["entry_net_credit"],
            "Cierre": r["close_value"],
            "Motivo": _RSN.get(r["close_reason"], r["close_reason"]),
            "P&L": r["realized_pnl"] or 0.0,
            "Hora cierre": (r["close_ts"] or "")[11:19],
        } for r in ic_closed]
        st.dataframe(
            pd.DataFrame(crows), use_container_width=True, hide_index=True,
            column_config={
                "Crédito": st.column_config.NumberColumn(format="$%.2f"),
                "Cierre": st.column_config.NumberColumn(format="$%.2f"),
                "P&L": st.column_config.NumberColumn(format="$%.2f"),
            },
        )
        # Total del período elegido (usuario 2026-08-12: "la ganancia que se hizo en el día") — con el
        # filtro en "Hoy" es exactamente la ganancia del día; con otro rango, la de ese rango.
        _ic_period_pnl = sum((r["realized_pnl"] or 0.0) for r in ic_closed)
        st.caption(f"💰 **Ganancia del período elegido: ${_ic_period_pnl:,.2f}** ({len(ic_closed)} condor(s) cerrado(s)).")
    else:
        st.caption("Todavía no cerró ningún condor.")

    st.divider()
    _render_intraday_ratings(
        conn, "iron_condor", ic_open, ic_closed,
        lambda r: f"SP {r['short_put_strike']:.0f} / SC {r['short_call_strike']:.0f} · crédito ${r['entry_net_credit']:.2f} · "
                  f"{'abierto' if r['status']=='open' else 'cerrado'}",
        "ic",
    )


# ============================ APRENDIZAJE ============================
with tab_aprendizaje:
    # --- Tu estilo real (aprendido de TUS operaciones reales, usuario 2026-08-05) ---
    _profile = learning.analyze_real_trade_profile(conn)
    st.subheader("🎯 Tu estilo real (aprendido de tus operaciones)")
    if not _profile.get("n"):
        st.caption("Todavía no hay operaciones reales para estudiar tu estilo. En cuanto abras opciones en tu broker y se detecten en **Operaciones**, el robot arma tu perfil acá.")
    else:
        st.caption(f"Analizado sobre tus **{_profile['n']}** operaciones reales detectadas. Cuantas más operes, más afina tu patrón.")
        pf1, pf2, pf3, pf4 = st.columns(4)
        pf1.metric("Cobertura típica", f"{_profile.get('median_coverage_pct')}%" if _profile.get('median_coverage_pct') is not None else "—")
        pf2.metric("DTE típico", f"{_profile.get('median_dte')}" if _profile.get('median_dte') is not None else "—")
        pf3.metric("Delta implícita", f"{_profile.get('implied_delta')}" if _profile.get('implied_delta') is not None else "—", help="Estimada como 1 − POP promedio.")
        pf4.metric("Anualizado prom.", f"{_profile.get('avg_annualized_pct')}%" if _profile.get('avg_annualized_pct') is not None else "—")
        favs = _profile.get("top_symbols") or []
        if favs:
            st.markdown("**Tus tickers favoritos:** " + " · ".join(f"**{s}** ({c})" for s, c in favs))
        strat = _profile.get("strategies") or {}
        if strat:
            st.caption("Estrategias que usás: " + ", ".join(f"{k} ({v})" for k, v in strat.items()))
        st.caption(
            "El robot ya tiene acceso a estos datos para aprender tu criterio. Si querés que **priorice tus "
            "tickers favoritos** o **ajuste su delta/cobertura** para parecerse a tu estilo, decímelo y lo activo."
        )
    st.divider()

    st.info("👉 El feedback (👍/👎 + notas) lo cargás en la pestaña **Decisiones**. Esta página muestra lo que el robot APRENDIÓ con eso.")
    st.caption(
        "El robot aprende cruzando **tu feedback (👍/👎)** con el **resultado real** de cada operación. "
        "Modo mixto: ajusta solo los cambios chicos y te deja los grandes acá para que apruebes. "
        "Corre solo cada día tras el cierre; también podés forzarlo."
    )

    # Estado de los datos disponibles para aprender.
    examples = repo.get_learning_examples(conn)
    n_fb = sum(1 for e in examples if e["user_feedback"] in ("good", "bad"))
    n_closed = sum(1 for e in examples if e["position_status"] == "closed")
    n_signal = sum(1 for e in examples if e["user_feedback"] in ("good", "bad") or e["position_status"] == "closed")
    lc1, lc2, lc3, lc4 = st.columns(4)
    lc1.metric("Aperturas registradas", len(examples))
    lc2.metric("Con tu feedback", n_fb)
    lc3.metric("Ya cerradas", n_closed)
    lc4.metric("Con señal (para aprender)", f"{n_signal}/{learning.DEFAULT_MIN_EXAMPLES}")

    if st.button("🧠 Revisar aprendizaje ahora"):
        res = learning.review(conn, sim)
        st.success(res["summary"])
        st.rerun()

    report = repo.get_latest_learning_report(conn)
    if report is not None:
        # Mostramos el resumen sin la parte de "Patrones que veo" (esos van como lista abajo).
        base_summary = (report["summary"] or "").split(" Patrones que veo:")[0]
        st.markdown(f"**Última lección ({report['created_at'][:16].replace('T', ' ')}):** {base_summary}")
        try:
            insights = json.loads(report["detail_json"]).get("insights", []) if report["detail_json"] else []
        except (ValueError, TypeError):
            insights = []
        if insights:
            st.markdown("**🔎 Patrones que veo (en tus datos):**")
            st.markdown("\n".join(f"- {s}" for s in insights))

    # Propuestas pendientes de tu aprobación.
    st.subheader("Propuestas para aprobar")
    st.caption(
        "El robot mira TUS resultados y te propone ajustar **cuánta importancia le da a cada factor** al "
        "elegir qué put vender. Un peso más alto = ese factor pesa más en la decisión. Vos aprobás o rechazás; "
        "nada cambia sin tu OK."
    )
    # Nombre CLARO de cada factor + qué mira (usuario 2026-08-12: "no se qué me quiere decir, más claro").
    _LEARN_FACTOR = {
        "score_weight_delta": ("Delta (riesgo de asignación)", "qué tan probable es que te asignen el put"),
        "score_weight_coverage": ("Colchón hasta el strike", "cuánto puede caer la acción antes de tocar tu strike"),
        "score_weight_return": ("Rendimiento de la prima", "cuánta prima paga la operación para el margen que traba"),
        "score_weight_pop": ("Probabilidad de ganar (POP)", "la chance de quedarte con toda la prima"),
        "score_weight_day_change": ("Cuánto cayó hoy la acción", "preferir acciones que ya corrigieron (put más barato y seguro)"),
        "score_weight_iv_rank": ("Volatilidad (IV Rank)", "cuán cara está la prima por la volatilidad"),
        "score_weight_liquidity": ("Liquidez (spread/volumen)", "qué tan fácil es entrar y salir sin perder en el spread"),
        "score_weight_theta": ("Decaimiento de tiempo (theta)", "cuánto valor pierde la opción por día a tu favor"),
    }
    # Perillas del Iron Condor (usuario 2026-08-14). NO son pesos del scoring como las de arriba: son
    # umbrales concretos de la estrategia, así que se muestran con su propio texto y su propio formato.
    # Sin esto, una propuesta de "crédito mínimo 0 → 150" se leía como "peso 0.00 → 150.00 · darle más
    # importancia al elegir qué put vender", que no quiere decir nada.
    _CONDOR_FACTOR = {
        "short_delta_max": ("Iron Condor · delta de los cortos", "{v:.3f}",
                            "qué tan lejos del precio vende el put y el call"),
        "calm_range_pct": ("Iron Condor · umbral de día calmo", "{v:.2%}",
                           "cuánto se puede mover el SPX en el día para que entre"),
        "min_credit": ("Iron Condor · crédito mínimo", "${v:,.0f}",
                       "la prima mínima que tiene que pagar para que valga la pena"),
        "max_vix_change_pct": ("Iron Condor · tope de VIX en suba", "{v:+.2f}%",
                               "cuánto puede estar subiendo el VIX para que entre igual"),
        "profit_target_pct": ("Iron Condor · objetivo de ganancia", "{v:.0%}",
                              "a qué porcentaje del crédito cierra la posición"),
        "stop_loss_dollars": ("Iron Condor · stop-loss", "${v:,.0f}",
                              "cuánto tolera perder antes de salir"),
    }

    def _why_clear(rationale: str, name: str) -> str:
        r = (rationale or "").lower()
        if "tu voto" in r:
            return "Porque vos lo marcaste 👍 varias veces."
        if "mejor" in r:
            return f"Porque tus operaciones GANADORAS solían tener mejor {name.lower()}."
        if "peor" in r:
            return f"Porque tus ganadoras no necesitaban tanto {name.lower()}."
        return ""

    proposals = repo.get_pending_learning_proposals(conn)
    if not proposals:
        st.caption("No hay propuestas pendientes. Cuando el robot quiera un cambio grande, aparece acá.")
    for p in proposals:
        pc1, pc2, pc3 = st.columns([5, 1, 1])
        with pc1:
            if p["param"] in _CONDOR_FACTOR:
                _name, _fmt, _desc = _CONDOR_FACTOR[p["param"]]
                _de = _fmt.format(v=p["current_value"])
                _a = _fmt.format(v=p["proposed_value"])
                st.markdown(
                    f"**{_name}**  ·  {_de} → **{_a}**  \n"
                    f"<span style='color:{TEXT_MUTED};font-size:0.88rem'>Cambia {_desc}. "
                    f"{p['rationale'] or ''}</span>",
                    unsafe_allow_html=True,
                )
            else:
                _name, _desc = _LEARN_FACTOR.get(p["param"], (p["param"], ""))
                _up = p["proposed_value"] > p["current_value"]
                _verb = "darle MÁS importancia a" if _up else "darle MENOS importancia a"
                _why = _why_clear(p["rationale"], _name)
                st.markdown(
                    f"**{_name}**  ·  peso {p['current_value']:.2f} → **{p['proposed_value']:.2f}**  \n"
                    f"<span style='color:{TEXT_MUTED};font-size:0.88rem'>El robot va a **{_verb}** {_desc} "
                    f"al elegir qué put vender.{(' ' + _why) if _why else ''}</span>",
                    unsafe_allow_html=True,
                )
        with pc2:
            if st.button("✅ Aprobar", key=f"appr_{p['id']}"):
                learning.apply_approved_proposal(conn, p["id"])
                st.rerun()
        with pc3:
            if st.button("❌ Rechazar", key=f"rej_{p['id']}"):
                repo.resolve_learning_proposal(conn, p["id"], "rejected")
                st.rerun()

    # Pesos vigentes del cerebro (base vs aprendido).
    st.subheader("Pesos del cerebro (lo que aprendió)")
    eff = learning.effective_weights(conn, sim)
    _DIM_LABEL = {
        "score_weight_delta": "Delta", "score_weight_coverage": "Cobertura",
        "score_weight_return": "Retorno anual.", "score_weight_pop": "POP (prob. OTM)",
        "score_weight_day_change": "Día que cae", "score_weight_iv_rank": "IV Rank alta",
        "score_weight_liquidity": "Liquidez (spread)", "score_weight_theta": "Theta (decaimiento)",
    }
    weight_rows = [
        {"Factor": _DIM_LABEL[f], "Peso base": getattr(sim, f), "Peso actual (aprendido)": eff[f],
         "Cambio": round(eff[f] - getattr(sim, f), 3)}
        for f in _DIM_LABEL
    ]
    st.dataframe(
        pd.DataFrame(weight_rows), use_container_width=True, hide_index=True,
        column_config={
            "Peso base": st.column_config.NumberColumn(format="%.2f"),
            "Peso actual (aprendido)": st.column_config.NumberColumn(format="%.2f"),
            "Cambio": st.column_config.NumberColumn(format="%+.2f"),
        },
    )
    st.caption(
        "Cuanto más alto el peso, más mira ese factor al elegir una operación. Estos son los que el "
        "robot ya está usando en vivo (base + lo aprendido y aprobado)."
    )

    # Iron Butterfly: umbral de distancia de entrada (lo que aprende esa estrategia).
    st.subheader("Iron Butterfly — distancia de entrada aprendida")
    bf_base = settings.intraday_butterfly.distance_threshold_pct
    bf_eff = learning.effective_butterfly(conn, settings.intraday_butterfly).distance_threshold_pct
    ib1, ib2 = st.columns(2)
    ib1.metric("Umbral base", f"{bf_base:.3%}")
    ib2.metric("Umbral actual (aprendido)", f"{bf_eff:.3%}", f"{(bf_eff - bf_base):+.3%}")
    st.caption("Qué tanto tiene que alejarse SPX de su media de 8 para que el iron entre. El aprendizaje lo sube (más selectivo) o lo baja según cómo resultaron las operaciones.")


# ============================ CONFIGURACIÓN ============================
with tab_config:
    st.markdown(
        f"""
- **DTE**: {sim.dte_range[0]}–{sim.dte_range[1]} días · elige el de mejor retorno anualizado (mín {sim.min_annualized_return:.0%})
- **Delta objetivo**: {sim.delta_target_volatile:.2f} (volátil) / {sim.delta_target_normal:.2f} (normal), banda ±{sim.delta_band:.2f}
- **Volátil si** IV ≥ {sim.volatile_iv_threshold:.0%}
- **Cobertura**: base {sim.coverage_normal:.0%}/{sim.coverage_volatile:.0%} · lejos de soporte {sim.coverage_far_from_support:.0%} · +{sim.event_coverage_bump:.0%} con Fed/earnings
- **Soporte**: diario{" + mensual" if sim.require_monthly_support else ""} · IV Rank ≥ {sim.iv_rank_min:.0f}{" (real)" if sim.require_real_iv_rank else ""} · {"precio ≥ VWAP" if sim.require_price_above_vwap else "sin VWAP"}
- **Liquidez**: bid>0, OI ≥ {sim.min_open_interest}, vol ≥ {sim.min_contract_volume}, spread ≤ {sim.max_bid_ask_spread_pct:.0%}, POP ≥ {sim.min_probability_otm:.0%}
- **Riesgo**: máx {sim.max_open_positions} posiciones · 1 por símbolo · {sim.min_free_buying_power_pct:.0%} buying power libre
- **Tamaño**: {"por precio — <" + str(int(sim.price_tier_low)) + f": {sim.contracts_cheap} contratos, hasta " + str(int(sim.price_tier_high)) + f": {sim.contracts_mid}, +: {sim.contracts_expensive}" if sim.use_price_tier_sizing else f"{sim.max_position_pct:.0%} del capital por símbolo"}
- **Salidas**: {sim.profit_target_pct:.0%} base · {sim.profit_target_far_pct:.0%} lejos del strike · {sim.profit_target_near_exp_pct:.0%} cerca de vencimiento sin noticias · stop-loss a {sim.stop_loss_multiple:.0f}x la prima
- **Ejecución**: órdenes límite negociando el spread (edge {sim.fill_edge_pct:.0%} hacia ask/bid) · comisión ${sim.commission_per_contract:.2f}/contrato
        """
    )
