from __future__ import annotations

import json
import datetime as _dt   # para formatear la fecha/hora de apertura (usuario 2026-08-19)
from datetime import date, timedelta as _timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from options_advisor.config import load_settings
from options_advisor.dashboard.components import (
    ACCENT,
    BORDER,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    cached_all_positions,
    cached_option_chains,
    cached_quotes,
    get_broker,
    get_connection,
    icon,
    inject_theme,
    render_header,
    render_notification_bell,
    utilidad_con_periodo,
)
from options_advisor.dashboard.rating import _render_intraday_ratings, condor_data_rows
from options_advisor.simulator import learning
from options_advisor.storage import repository as repo

st.set_page_config(page_title="Lokshn · Real Market", page_icon="🔴", layout="wide", initial_sidebar_state="expanded")
inject_theme()
render_header(
    icon("zap", size=24, color="#ff3b3b"),
    "Real Market",
    "Trading REAL en tu cuenta Schwab. Réplica del Simulador pero con órdenes de verdad — protegido por el "
    "guardián de seguridad, con START diario y kill switch. El Simulador sigue corriendo aparte, en paper.",
)

conn = get_connection()
render_notification_bell(conn)

settings = load_settings()
lt = settings.live_trading
today = date.today()
armed = repo.is_live_armed(conn, today)

# ═══ QUÉ MÁQUINA ES ESTA, EN GRANDE Y ARRIBA DE TODO (usuario 2026-09-08) ═══
#
# La Mac y el servidor corren dashboards IDÉNTICOS, los dos en el puerto 8501. Ese día el usuario
# dio START del día en el del servidor creyendo que era el de la Mac: el robot real quedó sin armar
# y no abrió nada hasta que nos dimos cuenta, media hora después. La única forma de distinguirlos
# era mirar la barra de direcciones del navegador.
#
# Dos páginas iguales, una que opera con plata real y otra que no, es un accidente esperando pasar.
import platform as _platform  # noqa: E402

_soy = _platform.node().split(".")[0]
_designada = (getattr(lt, "real_machine_hostname", "") or "").split(".")[0]
_opera = lt.enabled and not lt.dry_run and not lt.kill_switch
if _opera and (not _designada or _soy.lower() == _designada.lower()):
    _tono, _icono, _que = "#b71c1c", "🔴", "OPERA CON DINERO REAL"
else:
    _tono, _icono, _que = "#1565c0", "🔵", "SOLO MIRA · no manda órdenes"
st.markdown(
    f"<div style='background:{_tono};color:#fff;padding:10px 16px;border-radius:8px;"
    f"font-size:1.05rem;font-weight:700;letter-spacing:.3px;margin-bottom:12px'>"
    f"{_icono}&nbsp; {_soy.upper()} &nbsp;·&nbsp; {_que}</div>",
    unsafe_allow_html=True,
)


def _status_of(row) -> str:
    try:
        return (row["order_status"] or "").upper() if "order_status" in row.keys() else ""
    except Exception:
        return ""


# Estados de Schwab en los que la orden sigue viva esperando fill (la "puesta al mid").
_RESTING_STATES = {"WORKING", "QUEUED", "ACCEPTED", "PENDING_ACTIVATION", "NEW", "PENDING_RECALL", "AWAITING_MANUAL_REVIEW"}
kill = repo.is_live_kill_switch(conn)

GOOD = "#00e676"   # verde más intenso/vivo (usuario 2026-08-12)
# Capital disponible para invertir (usuario 2026-08-13: "calculá el % de utilidad con el dinero que tengo
# para invertir, 50K"). El % del P&L total se mide sobre este monto. Cambiá el número si cambia tu capital.
CAPITAL_DISPONIBLE = 50_000
BAD = "#ff3b3b"
WARN = "#f59e0b"
# Amarillo del cartel de exposición promedio (usuario 2026-09-14: "lo quería en
# amarillo"). Va aparte de WARN, que es ámbar y en pantalla se lee naranja.
AMARILLO = "#ffd60a"


def _fmt_money(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def _fmt_pct(v):
    return f"{v:+.1f}%" if isinstance(v, (int, float)) else "—"


_PERIODOS = ["Hoy", "Semana", "Mes", "Año", "Todo"]


def _desde_periodo(periodo: str, hoy: date):
    """Fecha DESDE la que cuenta cada período, o None para 'Todo'. Misma semántica que los filtros que
    ya usaba la página para las órdenes, para que los números coincidan entre paneles."""
    if periodo == "Hoy":
        return hoy
    if periodo == "Semana":
        return hoy - _timedelta(days=hoy.weekday())      # desde el lunes
    if periodo == "Mes":
        return hoy.replace(day=1)
    if periodo == "Año":
        return hoy.replace(month=1, day=1)
    return None


def _fmt_apertura(row) -> str:
    """'18/08 11:53' — fecha y hora en que se abrió la posición. Compacto a propósito: la tabla ya
    tiene 12 columnas y el año se sobreentiende. Nunca lanza; si no hay dato devuelve '—'.

    Usa `log_ts` y NO `sent_ts`: `sent_ts` se pisa en CADA reemplazo del precio caminado, así que en
    una orden que negoció un rato marca el último envío, no la apertura (AAL del 18/08: log_ts
    11:53:12 pero sent_ts 12:11:43, 18 minutos después). `log_ts` además es el mismo instante que
    muestra 'Órdenes que armó el robot', así que las dos pantallas coinciden."""
    for _campo in ("log_ts", "sent_ts"):
        try:
            _v = row[_campo]
        except (KeyError, IndexError, TypeError):
            _v = None
        if _v:
            try:
                return _dt.datetime.fromisoformat(str(_v)).strftime("%d/%m %H:%M")
            except (ValueError, TypeError):
                pass
    try:
        _d = row["log_date"]
        return _dt.date.fromisoformat(str(_d)).strftime("%d/%m") if _d else "—"
    except (KeyError, IndexError, TypeError, ValueError):
        return "—"


def _fmt_plain(v):
    return "—" if v is None else str(v)


def _render_symbol_tooltip_table(rows: list[dict], col_specs: list[tuple], bg_fn) -> None:
    """Misma tabla que el Simulador (usuario 2026-08-10, punto 7): al pasar el MOUSE por el símbolo
    aparece un tooltip con el precio actual y el % del día del subyacente, verde/rojo. `col_specs` =
    lista de (encabezado, key, formateador). Cada fila espera 'Precio ahora' y '% día' para el tooltip."""
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

# ------------------------- Semáforo (rojo apagado / verde trabajando) -------------------------
working = armed and not kill and (lt.enabled or lt.dry_run)
# Panel de estado MINIMALISTA (usuario 2026-08-12: "más lindo, cuadrados y botones"): 4 cuadrados uniformes.
if kill:
    _est_txt, _est_col = "FRENADO", BAD
elif not armed:
    _est_txt, _est_col = "APAGADO", TEXT_MUTED
elif lt.enabled and not lt.dry_run:
    _est_txt, _est_col = "TRABAJANDO", GOOD
elif working:
    _est_txt, _est_col = "DRY-RUN", ACCENT
else:
    _est_txt, _est_col = "APAGADO", TEXT_MUTED

if not lt.enabled:
    _modo_txt, _modo_col = "OFF", TEXT_MUTED
elif lt.dry_run:
    _modo_txt, _modo_col = "DRY-RUN", ACCENT
else:
    _modo_txt, _modo_col = "REAL", GOOD

_dot = (f"<span style='display:inline-block;width:7px;height:7px;border-radius:50%;background:{_est_col};"
        f"margin-right:5px;vertical-align:middle;{'animation:oiapulse 1.8s infinite;' if _est_col == GOOD else ''}'></span>")


def _status_tile(label: str, value: str, col: str, dot: str = "") -> str:
    # Cuadros CHICOS y ordenados (usuario 2026-08-12): poco padding, tipografía compacta, borde superior de color.
    return (f"<div style='flex:1 1 90px; background:{SURFACE}; border:1px solid {BORDER}; border-top:2px solid {col}; "
            f"border-radius:0.5rem; padding:0.5rem 0.45rem; text-align:center;'>"
            f"<div style='color:{TEXT_MUTED}; font-size:0.6rem; text-transform:uppercase; letter-spacing:0.04em;'>{label}</div>"
            f"<div style='color:{col}; font-size:0.98rem; font-weight:700; margin-top:0.18rem; line-height:1;'>{dot}{value}</div>"
            f"</div>")

st.markdown(
    f"<style>@keyframes oiapulse{{0%{{box-shadow:0 0 0 0 {GOOD}66}}70%{{box-shadow:0 0 0 6px {GOOD}00}}100%{{box-shadow:0 0 0 0 {GOOD}00}}}}</style>"
    "<div style='display:flex; gap:0.45rem; flex-wrap:wrap; margin:0.1rem 0 0.7rem;'>"
    + _status_tile("Estado", _est_txt, _est_col, dot=_dot)
    + _status_tile("Modo", _modo_txt, _modo_col)
    + _status_tile("Armado hoy", "SÍ" if armed else "No", GOOD if armed else TEXT_MUTED)
    + _status_tile("Kill switch", "ACTIVO" if kill else "Off", BAD if kill else TEXT_MUTED)
    + "</div>",
    unsafe_allow_html=True,
)

# ═══════════ REVISIÓN DE LAS 9:00 (usuario 2026-09-15) ═══════════
# "Me gustaría que todas las mañanas 30 min antes que abra el mercado hagas esta revisión."
#
# La corre el servidor solo (deploy/instalar_revision_matinal.sh) y deja el resultado en un archivo.
# Acá se muestra. No se recalcula en la pantalla a propósito: lo que se ve tiene que ser EL chequeo
# que corrió a las 9:00, con su hora — no uno nuevo que podría dar distinto y dejar sin saber cuál
# valía. Si la revisión no corrió, se dice; un panel en blanco se confunde con "todo bien".
_rev_path = Path(__file__).resolve().parents[3].parent / "data" / "logs" / "revision_matinal.json"
try:
    _rev = json.loads(_rev_path.read_text(encoding="utf-8")) if _rev_path.exists() else None
except Exception:
    _rev = None

if _rev and _rev.get("fecha") == today.isoformat():
    _hora = (_rev.get("ts") or "")[11:16]
    if _rev.get("ok"):
        st.success(f"✅ **Revisión de las {_hora} — todo en orden.** "
                   + ("Falta que vos: " + " · ".join(_rev.get("pendientes") or [])
                      if _rev.get("pendientes") else "No falta nada."), icon="✅")
    else:
        st.error(f"🔴 **Revisión de las {_hora} — hay {len(_rev['problemas'])} problema(s) "
                 "que impiden operar hoy:**\n\n"
                 + "\n".join(f"- {p}" for p in _rev["problemas"]), icon="🔴")
    with st.expander(f"Ver la revisión completa de las {_hora}"):
        st.code("\n".join(_rev.get("lineas") or []), language=None)
elif _rev:
    st.warning(f"⏳ La última revisión automática es del **{_rev.get('fecha')}**, no de hoy. "
               "Si el mercado abre hoy, conviene correrla a mano: "
               "`systemctl --user start lokshn-revision.service`", icon="⏳")

# ═══════════ RÉCORD DE EXPOSICIÓN DE LOS NAKED (usuario 2026-09-14) ═══════════
# "Me puedes poner un cartel rojo en los naked donde siempre se quede la última exposición máxima
# y la fecha; si otro día la pasa se actualiza y si no llega queda esta."
#
# Es el NOCIONAL: strike × 100 × contratos de todo lo que estuvo vivo AL MISMO TIEMPO — o sea lo
# que costaría comprar las acciones si te asignaran todo junto. No es el colateral que el broker
# traba (mucho más chico): el colateral dice qué te cuesta hoy, esto dice cuánto podrías llegar a
# deber, y es el número que corresponde mirar para dimensionar.
#
# Se calcula de la historia completa en cada carga, no se guarda un récord en una bandera: así el
# número siempre es correcto, sube solo cuando se supera, nunca baja, y no puede quedar desfasado
# si algún día se corrige una fila.
_exp = repo.exposicion_naked(conn)
if _exp["maximo"] > 0:
    _fecha_max = _dt.date.fromisoformat(_exp["maximo_fecha"]).strftime("%d/%m/%Y") \
        if _exp["maximo_fecha"] else "—"
    _es_hoy = _exp["ahora"] >= _exp["maximo"]      # hoy estás igualando o superando el récord
    _pct = (_exp["ahora"] / _exp["maximo"] * 100.0) if _exp["maximo"] else 0.0
    st.markdown(
        f"<div style='background:{BAD}14; border:1px solid {BAD}; border-left:5px solid {BAD}; "
        f"border-radius:0.5rem; padding:0.6rem 0.9rem; margin:0.2rem 0 0.8rem;'>"
        f"<div style='color:{BAD}; font-size:0.62rem; font-weight:700; text-transform:uppercase; "
        f"letter-spacing:0.06em;'>Exposición máxima de los naked · récord histórico</div>"
        f"<div style='color:{BAD}; font-size:1.5rem; font-weight:800; line-height:1.15; "
        f"margin-top:0.15rem;'>&#36;{_exp['maximo']:,.0f}"
        f"<span style='font-size:0.85rem; font-weight:600; opacity:0.85;'> &nbsp;·&nbsp; "
        f"{_fecha_max} &nbsp;·&nbsp; {_exp['maximo_posiciones']} posiciones</span></div>"
        f"<div style='color:{TEXT_MUTED}; font-size:0.72rem; margin-top:0.25rem;'>"
        f"Ahora: <b style='color:{BAD if _es_hoy else TEXT_MUTED}'>&#36;{_exp['ahora']:,.0f}</b> "
        f"({_exp['ahora_posiciones']} posiciones · {_pct:.0f}% del récord). "
        + ("<b>Estás en el máximo histórico.</b> " if _es_hoy else "")
        + "Es el nocional: lo que costaría comprar las acciones si te asignaran todo junto."
        f"</div></div>",
        unsafe_allow_html=True,
    )

    # Cartel AMARILLO con el promedio (usuario 2026-09-14). El récord dice cuánto llegaste a
    # arriesgar UNA vez; el promedio dice cuánto arriesgás habitualmente, que para dimensionar la
    # cuenta suele ser el número más honesto. Un récord alto sobre un promedio bajo es un día
    # suelto; un promedio cerca del récord significa que operás siempre al tope.
    if _exp["dias_con_exposicion"]:
        _prom = _exp["promedio"]
        _rel = (_prom / _exp["maximo"] * 100.0) if _exp["maximo"] else 0.0
        st.markdown(
            f"<div style='background:{AMARILLO}14; border:1px solid {AMARILLO}; "
            f"border-left:5px solid {AMARILLO}; border-radius:0.5rem; padding:0.6rem 0.9rem; "
            f"margin:0 0 0.8rem;'>"
            f"<div style='color:{AMARILLO}; font-size:0.62rem; font-weight:700; "
            f"text-transform:uppercase; letter-spacing:0.06em;'>Exposición promedio por día</div>"
            f"<div style='color:{AMARILLO}; font-size:1.5rem; font-weight:800; line-height:1.15; "
            f"margin-top:0.15rem;'>&#36;{_prom:,.0f}"
            f"<span style='font-size:0.85rem; font-weight:600; opacity:0.85;'> &nbsp;·&nbsp; "
            f"sobre {_exp['dias_con_exposicion']} días con posiciones abiertas</span></div>"
            f"<div style='color:{TEXT_MUTED}; font-size:0.72rem; margin-top:0.25rem;'>"
            f"Es el {_rel:.0f}% del récord. Promedio del máximo de cada día; los días sin nada "
            f"abierto no cuentan.</div></div>",
            unsafe_allow_html=True,
        )

st.divider()

# ------------------------- Acciones: START / desarmar / kill (minimalista, con doble confirmación) -------------------------
if kill:
    st.error("🛑 Kill switch activo — desactivalo para poder armar el día.", icon="🛑")

_ac1, _ac2 = st.columns(2)
with _ac1:
    if armed:
        if st.button("⏸️  Desarmar el día", use_container_width=True,
                     help=f"Armado para hoy ({today.strftime('%d/%m/%Y')}). Frena NUEVAS órdenes; lo abierto se sigue gestionando."):
            repo.disarm_live(conn)
            st.rerun()
        # Volver a armar el MISMO día = permiso nuevo (usuario 2026-09-09: "si puede abrir si yo pongo
        # otra vez armar, que sea asi la regla"): el cupo diario vuelve a cero desde este momento.
        # No toca el tope semanal ni el capital comprometido.
        if st.button("🔄  Re-armar (habilitar otra orden hoy)", use_container_width=True, key="live_rearm",
                     disabled=kill,
                     help="Vuelve a poner el cupo DIARIO en cero desde ahora, para que el robot pueda "
                          "abrir otra vez hoy. El tope semanal y los límites de capital siguen igual."):
            repo.arm_live_today(conn, today)
            st.toast("Cupo diario de los naked reiniciado — el robot puede volver a abrir hoy.", icon="🔄")
            st.rerun()
    else:
        _conf = st.checkbox("Confirmo habilitar el trading real de HOY")
        if st.button("🚀  START del día", type="primary", use_container_width=True, disabled=(not _conf) or kill,
                     help="Cada mañana hay que dar START para que el robot opere hoy. Se resetea a la medianoche."):
            st.session_state["_arm_pending"] = True
with _ac2:
    if kill:
        if st.button("▶️  Desactivar kill switch", type="primary", use_container_width=True):
            repo.set_live_kill_switch(conn, False)
            st.rerun()
    else:
        if st.button("🛑  Kill switch (frenar todo)", use_container_width=True,
                     help="Corta TODAS las órdenes reales al instante, aun con el día armado. Lo abierto se sigue gestionando."):
            repo.set_live_kill_switch(conn, True)
            st.rerun()

# Segunda confirmación del START (seguridad — plata real, no se toca).
if st.session_state.get("_arm_pending"):
    st.warning("Segunda confirmación: ¿seguro que querés armar el trading real de hoy?")
    _cc1, _cc2 = st.columns(2)
    if _cc1.button("Sí, armar", type="primary", use_container_width=True):
        repo.arm_live_today(conn, today)
        st.session_state["_arm_pending"] = False
        st.rerun()
    if _cc2.button("Cancelar", use_container_width=True):
        st.session_state["_arm_pending"] = False
        st.rerun()

st.divider()

# ------------------------- Configuración de la Fase 1 -------------------------
st.markdown("### ⚙️ Configuración activa (Fase 1)")
_eff_max_day = repo.get_max_live_orders_per_day(conn, lt.max_orders_per_day, today)  # override en vivo (resetea a la medianoche)
_, _live_rearm_id = repo.live_rearm_mark(conn, today)   # el cupo se cuenta desde el último START
_live_used = repo.count_live_approved_opens_today(conn, today, after_id=_live_rearm_id)
_live_used_dia = repo.count_live_approved_opens_today(conn, today)   # total del día, solo para mostrar
# Métricas EN VIVO del libro real (usuario 2026-08-12: agregar win rate / exposición / colateral; sacar "órdenes/semana").
_cfg_open = repo.get_open_real_put_positions(conn)
_cfg_col_usado = sum((r["collateral"] or 0.0) for r in _cfg_open)
_cfg_exp_real = sum(float(r["strike"]) * 100.0 * (r["filled_contracts"] or r["final_contracts"] or 1) for r in _cfg_open)
_cfg_all_closed = repo.get_closed_real_positions_between(conn, date(2000, 1, 1), today)
_cfg_realiz = [r for r in _cfg_all_closed if r["realized_pnl"] is not None]
_cfg_wr = (100.0 * sum(1 for r in _cfg_realiz if (r["realized_pnl"] or 0) > 0) / len(_cfg_realiz)) if _cfg_realiz else None
g1, g2, g3, g4, g5, g6 = st.columns(6)
# Mostrar el techo del guardián acá sería engañoso: lo que el robot PIDE es el base, y solo sube a la
# cantidad de strikes baratos cuando corresponde (usuario 2026-08-17). Se muestran las dos cosas.
_c_base = getattr(lt, "base_contracts_per_order", 1)
_c_barato = getattr(lt, "cheap_strike_contracts", 0)
_c_umbral = getattr(lt, "cheap_strike_max", 0.0)
g1.metric("Contratos / orden",
          f"{_c_base} · {_c_barato}" if (_c_barato and _c_umbral) else str(_c_base),
          help=(f"Normalmente {_c_base} contrato(s). Si el strike es menor a ${_c_umbral:,.0f}, pide "
                f"{_c_barato} — un put barato traba mucho menos colateral, así los tamaños quedan "
                f"parejos. El guardián nunca manda más de {lt.max_contracts_per_order} por orden y "
                "recorta si no entra por colateral o cash."
                if (_c_barato and _c_umbral) else
                f"El guardián nunca manda más de {lt.max_contracts_per_order} por orden."))
g2.metric("Órdenes / día", f"{_live_used}/{_eff_max_day}")
g3.metric("Colateral máx.", f"${lt.max_total_deployed:,.0f}")
g4.metric("Win rate (real)", f"{_cfg_wr:.0f}%" if _cfg_wr is not None else "—",
          help="% de posiciones reales cerradas que terminaron en ganancia (histórico).")
g5.metric("Exposición real", f"${_cfg_exp_real:,.0f}", help="Strike × 100 × contratos de las posiciones reales abiertas.")
g6.metric("Colateral usado", f"${_cfg_col_usado:,.0f}", help="Margen comprometido HOY por las posiciones reales abiertas.")

# Control DIRECTO del tope de HOY (usuario 2026-08-10: "¿y si solo quiero UNA operación?"). Reemplaza el
# botón aditivo "abrir más" por un casillero donde ponés el TOTAL que querés que abra hoy (1, 2, 3…). El
# robot se para exactamente en ese número. Vale solo para hoy; a la medianoche vuelve al tope de config.
# 0 = no abrir ninguna hoy (pero seguir gestionando/cerrando lo que ya está abierto).
st.markdown("**Cuántas operaciones querés que abra HOY** — poné el total y guardá. El robot se para justo ahí.")
_od1, _od2, _od3 = st.columns([1, 1, 3])
with _od1:
    _hoy_tope = st.number_input("Órdenes hoy", min_value=0, max_value=20, value=int(_eff_max_day), step=1,
                                label_visibility="collapsed", key="live_today_cap",
                                help="Total de aperturas reales para HOY. Poné 1 si solo querés una. "
                                     "El robot igual solo abre si hay oportunidad (caída ≥1%, delta ≤0.21, etc.) — "
                                     "es un TECHO, no una obligación. 0 = no abrir ninguna hoy.")
with _od2:
    if st.button("Guardar", use_container_width=True, key="save_live_today_cap", type="primary"):
        repo.set_max_live_orders_per_day(conn, int(_hoy_tope), today)
        st.session_state["_live_more_msg"] = f"✅ Tope de hoy: {int(_hoy_tope)} (llevás {int(_live_used)} abiertas)."
        st.rerun()
    if st.session_state.get("_live_more_msg"):
        st.success(st.session_state.pop("_live_more_msg"))
with _od3:
    _quedan = max(0, _eff_max_day - _live_used)
    st.caption(f"Hoy llevás **{_live_used}** abiertas · tope de hoy **{_eff_max_day}** · le quedan **{_quedan}** por abrir. "
               "Aplica en el próximo escaneo (no hace falta reiniciar). Una posición que llenó ocupa el cupo; "
               "una rechazada/cancelada no. A la medianoche vuelve al tope base de config.")

# El bloque de "Símbolos permitidos / Exentos del tope / Caminar el precio" que estaba acá se sacó
# (usuario 2026-08-19: "arreglemos la estética, borrar esto"). Era un paredón de texto fijo que no
# cambia nunca y empujaba las posiciones para abajo. Los valores siguen vivos en config/settings.yaml
# (live_trading.allowed_symbols, price_cap_exempt_symbols y los price_walk_*); solo se dejó de mostrar.

# ------------------------- Avisos por email (apertura/cierre real) -------------------------
with st.expander("📧 Avisos por email — te aviso en cada apertura y cierre REAL"):
    st.caption("El robot manda un email cada vez que ABRE y cada vez que CIERRA una operación real. "
               "Necesita configurar SMTP en el archivo `.env` del proyecto (una vez):")
    st.code("SMTP_HOST=smtp.gmail.com\nSMTP_PORT=587\nSMTP_USER=tu-cuenta@gmail.com\n"
            "SMTP_PASSWORD=tu_app_password_de_google\nEMAIL_TO=roberto@crownsensor.com", language="bash")
    st.caption("Con Gmail, el `SMTP_PASSWORD` es un **App Password** (Google → Seguridad → Verificación en 2 "
               "pasos → Contraseñas de aplicaciones), NO tu contraseña normal. Después reiniciá el robot.")
    if st.button("📨 Probar email ahora", key="test_email"):
        from options_advisor.alerts import notifier
        if notifier.send_email("✅ Lokshn — prueba de email",
                               "Si recibís esto, los avisos de apertura/cierre real están funcionando."):
            st.success("Email de prueba enviado ✅ — revisá tu casilla (y spam).")
        else:
            st.error("No se pudo enviar: falta config SMTP en el .env (SMTP_HOST/USER/PASSWORD/EMAIL_TO) o falló el envío.")

st.divider()

# ------------------------- Registro de operaciones REALES (control aparte, como el simulador) -------------------------
st.markdown("### 📒 Registro de operaciones reales")
st.caption("Tu control de lo REAL, separado del Simulador (que es papel). Filtrá por período. El robot ABRE reales "
           "en tu cuenta Schwab; el P&L en vivo de las abiertas se lee de Schwab.")

_lg1, _lg2 = st.columns([1, 3])
_ledger_periodo = _lg1.selectbox("Período", ["Hoy", "Semana", "Mes", "Año", "Todo"], key="rm_ledger_periodo")
if _ledger_periodo == "Hoy":
    _ldesde = today
elif _ledger_periodo == "Semana":
    _ldesde = today - _timedelta(days=today.weekday())
elif _ledger_periodo == "Mes":
    _ldesde = today.replace(day=1)
elif _ledger_periodo == "Año":
    _ldesde = today.replace(month=1, day=1)
else:
    _ldesde = date(2000, 1, 1)

_ledger = repo.get_live_orders_between(conn, _ldesde, today)
_fills = [r for r in _ledger if _status_of(r) == "FILLED" and r["action"] == "SELL_TO_OPEN"]
# (Las métricas de resumen se consolidaron en el panel único de "Abiertas ahora" — usuario 2026-08-12.
# El selector de período de arriba sigue filtrando el detalle de aperturas/cerradas.)

# --- Posiciones abiertas por el ROBOT (solo las suyas, no tus trades manuales), con P&L de Schwab ---
st.markdown("#### 📈 Abiertas ahora — SOLO las que abrió el robot")
st.caption("Solo las que abrió el robot y siguen abiertas (no tus operaciones manuales de la cuenta). "
           "El P&L en vivo se lee de Schwab si está disponible.")
_robot_open = repo.get_open_real_put_positions(conn)
# A nivel de módulo (usuario 2026-08-12: el indicador de retorno anualizado se movió al FINAL de la página):
# se inicializan acá para que existan aunque no haya posiciones, y el loop de abajo los llena.
_ann_num = {1.0: 0.0}   # numerador (retorno $ anualizado) por fracción de captura
_ann_den = 0.0          # denominador (margen total trabado)
if not _robot_open:
    # Sin posiciones abiertas igual hay que mostrar la UTILIDAD REALIZADA (usuario 2026-08-21: "no
    # tengo mas la utilidad de los naked en real, estaban como el iron pero mejor y no los veo mas").
    # Todo el panel colgaba del `else` de abajo, asi que el dia que se cerraron las 5 posiciones
    # desaparecio tambien el historico. La plata YA COBRADA no depende de tener algo abierto: el
    # condor nunca tuvo este problema porque su panel vive fuera de la guarda.
    _realized_all = sum((r["realized_pnl"] or 0.0) for r in _cfg_realiz)
    _real_pct = (_realized_all / CAPITAL_DISPONIBLE * 100.0) if CAPITAL_DISPONIBLE > 0 else None
    _tcol = GOOD if _realized_all >= 0 else BAD
    _sin_pnl_n = len([r for r in _cfg_all_closed if r["realized_pnl"] is None])
    _sin_pnl_txt = (f" &middot; <b style='color:{WARN}'>{_sin_pnl_n} cerrada(s) sin precio de cierre cargado</b>"
                    if _sin_pnl_n else "")
    st.markdown(
        f"<div style='background:{_tcol}1a; border:1px solid {_tcol}55; border-radius:0.6rem; padding:0.75rem 1rem; margin:0.1rem 0 0.7rem;'>"
        f"<span style='color:{TEXT_MUTED}; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em;'>Ganancia realizada de los naked put reales &middot; solo operaciones cerradas</span><br>"
        f"<span style='color:{_tcol}; font-size:1.7rem; font-weight:800;'>${_realized_all:+,.2f}</span>"
        f"<span style='color:{_tcol}; font-size:1.05rem; font-weight:700; margin-left:0.5rem;'>"
        f"{('(' + format(_real_pct, '+.2f') + '%)') if _real_pct is not None else ''}</span>"
        f"<span style='color:{TEXT_MUTED}; font-size:0.86rem; margin-left:0.6rem;'>sobre ${CAPITAL_DISPONIBLE:,.0f} de capital &middot; "
        f"{len(_cfg_realiz)} cerrada(s){_sin_pnl_txt}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    _vp1, _vp2 = st.columns([1, 3])
    _nak_periodo_v = _vp1.selectbox("Ganancia del período", _PERIODOS, key="rm_nak_periodo_vacio")
    _nak_desde_v = _desde_periodo(_nak_periodo_v, today)
    _nak_cerr_v = [r for r in _cfg_realiz
                   if _nak_desde_v is None or (r["close_ts"] or "")[:10] >= _nak_desde_v.isoformat()]
    _nak_pnl_v = round(sum((r["realized_pnl"] or 0.0) for r in _nak_cerr_v), 2)
    _nak_win_v = [r for r in _nak_cerr_v if (r["realized_pnl"] or 0.0) > 0]
    _va, _vb, _vc, _vd = st.columns(4)
    _va.metric("Posiciones abiertas", 0)
    _vb.metric(f"Ganancia {_nak_periodo_v.lower()}", f"${_nak_pnl_v:+,.2f}",
               help=f"Realizado de los naked cerrados en el período · {len(_nak_cerr_v)} operación(es).")
    _vc.metric("Win rate", f"{len(_nak_win_v) / len(_nak_cerr_v) * 100:.0f}%" if _nak_cerr_v else "—",
               help=f"Ganadoras sobre cerradas en el período: {len(_nak_win_v)} de {len(_nak_cerr_v)}.")
    _vd.metric("Cerradas (histórico)", len(_cfg_realiz),
               help=f"Total de naked put reales cerrados con P&L cargado · ${_realized_all:+,.2f}.")
    st.caption("El robot no tiene posiciones reales abiertas ahora mismo.")
    if _sin_pnl_n:
        st.warning(f"⚠️ Hay **{_sin_pnl_n} operación(es) cerrada(s) sin precio de cierre**. Su ganancia NO está "
                   "sumada en los totales de arriba. Se cargan más abajo, en la lista de cerradas.", icon="⚠️")
else:
    import json as _json

    # Índice de posiciones de Schwab para enriquecer con P&L (match por símbolo/strike/vto).
    # Match robusto (usuario 2026-08-10, punto 7): se indexa con clave por fecha `date` Y por string
    # ISO, y el símbolo normalizado en mayúsculas/sin espacios — así el Mark/P&L no queda vacío por
    # una diferencia boba de tipo/format entre lo guardado y lo que devuelve Schwab.
    _sch = {}
    if settings.broker.mode == "schwab":
        try:
            for p in cached_all_positions():
                if p.option_type == "put" and (p.quantity or 0) < 0 and p.strike and p.expiration:
                    _sym = (p.underlying_symbol or "").strip().upper()
                    _stk = round(float(p.strike), 2)
                    _sch[(_sym, _stk, p.expiration)] = p
                    _sch[(_sym, _stk, p.expiration.isoformat())] = p
        except Exception:
            _sch = {}

    # Quotes del subyacente EN VIVO para el tooltip (precio ahora + % del día), como en el Simulador.
    _syms = tuple(sorted({(r["symbol"] or "").strip().upper() for r in _robot_open}))
    try:
        _quotes = cached_quotes(_syms)
    except Exception:
        _quotes = {}

    _tot_unreal = 0.0
    _tot_exp = 0.0
    _tot_premium = 0.0   # crédito total cobrado en las ABIERTAS (denominador del P&L Open total %)
    _rows = []
    for r in _robot_open:
        _sym = (r["symbol"] or "").strip().upper()
        _exp = date.fromisoformat(r["expiration"])
        _n = r["filled_contracts"] or r["final_contracts"] or 1
        _cred = r["fill_price"] or 0.0
        _days = (_exp - today).days
        try:
            _ctx = _json.loads(r["open_context_json"]) if r["open_context_json"] else {}
        except (ValueError, TypeError):
            _ctx = {}
        _tot_exp += float(r["strike"]) * 100.0 * _n
        _p = _sch.get((_sym, round(float(r["strike"]), 2), _exp))
        if _p is not None:
            # OJO (usuario 2026-08-18: "en el dashboard me marca mal"): `_p` es la posición AGREGADA
            # de Schwab. Si el mismo strike/vencimiento se abrió en VARIAS órdenes, el broker las suma
            # en UNA sola — AAL 13P 18-sep eran 1 contrato del 17/08 (orden 101) + 4 del 18/08 (orden
            # 126) = -5. Como esta tabla lista UNA FILA POR ORDEN, poner `_p.quantity` mostraba -5 en
            # las dos filas, como si hubiera 10 contratos. La cantidad de esta fila es la de SU orden.
            _qty = -_n
            _mark = abs(_p.market_value) / (100.0 * abs(_p.quantity)) if _p.quantity else None
            # El margen de Schwab también viene agregado: lo prorrateamos por contratos para que la
            # suma de las filas dé el margen real y no el doble.
            _raw_bp = getattr(_p, "maintenance_requirement", None)
            _pos_qty = abs(int(_p.quantity))
            _bp = (round(float(_raw_bp) * _n / _pos_qty, 2)
                   if isinstance(_raw_bp, (int, float)) and _pos_qty else _raw_bp)
        else:
            _qty, _mark, _bp = -_n, None, None
        _premium = _cred * 100.0 * _n
        _tot_premium += _premium   # acumula la prima cobrada (para el P&L Open total %)
        # P&L calculado NOSOTROS desde el crédito de entrada y el mark actual (usuario 2026-08-11: "me sale
        # P/L en 0"). El unrealized_pnl de Schwab llega en 0 recién abierta la posición, pero el mark ya es
        # real — así que el P&L verdadero es (crédito − mark). Put corto: ganás cuando el mark baja del crédito.
        if _mark is not None and _cred > 0:
            _pnl = round((_cred - _mark) * 100.0 * _n, 2)
            _pnlpct = round((_cred - _mark) / _cred * 100.0, 1)
        elif _p is not None and getattr(_p, "unrealized_pnl", None):
            _pnl = _p.unrealized_pnl
            _pnlpct = (_pnl / _premium * 100.0) if _premium > 0 else None
        else:
            _pnl, _pnlpct = None, None
        if _pnl is not None:
            _tot_unreal += _pnl
        _estado = "—" if _pnl is None else ("🟢 Ganando" if _pnl >= 0 else "🔴 Perdiendo")

        # --- Anualizado del libro abierto (usuario 2026-08-10): "si dejo expirar, ¿cuánto rinde al
        # año?", y lo mismo si cerrás capturando un % objetivo de la prima. Consistente con el
        # Simulador: (prima_capturada / margen) × (365 / DTE del trade). DTE = el del trade (entrada
        # a vencimiento) para que sea la tasa con que se diseñó, no una foto del día.
        _dte_ann = _ctx.get("chosen_dte")
        if not isinstance(_dte_ann, (int, float)) or _dte_ann <= 0:
            try:
                _dte_ann = (_exp - date.fromisoformat(r["log_date"])).days
            except Exception:
                _dte_ann = None
        if not isinstance(_dte_ann, (int, float)) or _dte_ann <= 0:
            _dte_ann = max(_days, 1)
        _margin = _bp if isinstance(_bp, (int, float)) and _bp > 0 else (r["collateral"] or float(r["strike"]) * 100.0 * _n)
        if _margin and _margin > 0 and _premium > 0:
            _ann_den += _margin
            _ann_num[1.0] += _premium * (365.0 / _dte_ann)

        # Fecha y hora en que se abrió la posición (usuario 2026-08-19: "debe decir la fecha y hora de
        # apertura"). Con dos órdenes del mismo strike/vencimiento — AAL 13P: una del 17/08 y otra del
        # 18/08 — la fila sola no dejaba distinguir cuál era cuál.
        _abierta = _fmt_apertura(r)
        _q = _quotes.get(_sym)
        _rows.append({
            "ID": r["id"],   # ID único de la posición (usuario 2026-08-11): decíselo a Moshe para cerrar la exacta
            "Abierta": _abierta,
            "Symbol": _sym,
            "Strike": float(r["strike"]) if r["strike"] is not None else None,   # strike vendido (usuario 2026-08-12)
            "Cant": _qty,
            "Days": _days,   # días que FALTAN para vencer, actualizado a hoy (_exp - today)
            "Trade Price": _cred,
            "Mark": _mark,
            "P/L %": _pnlpct,
            "P/L Open": _pnl,
            "BP Effect": _bp,
            "Estado": _estado,
            "Precio ahora": getattr(_q, "last_price", None),
            "% día": getattr(_q, "net_change_pct", None),
            "_premium": _premium,
            "_dte_ann": _dte_ann,
        })

    # Panel de indicadores CONSOLIDADO (usuario 2026-08-12: "todos juntos") con P&L Open total $ y % —
    # "desde el día 0" = ganancia/pérdida no realizada desde que se abrió cada posición, sobre la prima cobrada.
    _tot_pct = (_tot_unreal / _tot_premium * 100.0) if _tot_premium > 0 else None
    _col_usado = sum((r["collateral"] or 0.0) for r in _robot_open)
    # P&L TOTAL desde el INICIO del real (usuario 2026-08-12: "el P&L total desde que empezamos"):
    # realizado histórico (todas las cerradas) + abierto no realizado. _cfg_realiz viene de la config de arriba.
    # Filtro de PERIODO, ARRIBA de todo (usuario 2026-08-21: "quiero filtro de fechas para ver las
    # utilidades: hoy, esta semana, este mes, este año"). Manda sobre TODO el panel, incluido el numero
    # grande: elegis "Mes" y el titular pasa a ser la ganancia del mes. Antes el filtro estaba abajo y
    # solo movia una metrica chica, asi que el numero grande siempre decia lo mismo.
    _np1, _np2 = st.columns([1, 3])
    _nak_periodo = _np1.selectbox("Período de la ganancia", _PERIODOS, key="rm_nak_periodo",
                                  help="Filtra la ganancia REALIZADA (operaciones cerradas). Las "
                                       "posiciones abiertas se muestran siempre, no dependen del período.")
    _nak_desde = _desde_periodo(_nak_periodo, today)
    _nak_cerradas = [r for r in _cfg_realiz
                     if _nak_desde is None or (r["close_ts"] or "")[:10] >= _nak_desde.isoformat()]
    _nak_pnl_per = round(sum((r["realized_pnl"] or 0.0) for r in _nak_cerradas), 2)
    _nak_wins = [r for r in _nak_cerradas if (r["realized_pnl"] or 0.0) > 0]
    _nak_wr = round(len(_nak_wins) / len(_nak_cerradas) * 100, 0) if _nak_cerradas else None

    _realized_all = sum((r["realized_pnl"] or 0.0) for r in _cfg_realiz)
    _pl_total = _realized_all + _tot_unreal
    # % de utilidad = P&L total (realizado + abierto) sobre el CAPITAL DISPONIBLE (usuario 2026-08-13: "con
    # el dinero que tengo para invertir, 50K"), no sobre la prima. Rendimiento sobre el capital.
    _pl_total_pct = (_pl_total / CAPITAL_DISPONIBLE * 100.0) if CAPITAL_DISPONIBLE > 0 else None
    # Plazo del rendimiento (usuario 2026-08-13: "cuántas semanas se refiere ese %"): desde el PRIMER trade
    # real (día 0) hasta hoy.
    _first_real = conn.execute(
        "SELECT MIN(substr(COALESCE(sent_ts, log_ts), 1, 10)) FROM live_order_log "
        "WHERE action = 'SELL_TO_OPEN' AND dry_run = 0 AND sent = 1 AND order_status = 'FILLED'"
    ).fetchone()[0]
    _plazo_txt = ""
    if _first_real:
        try:
            _d0 = date.fromisoformat(_first_real)
            _dias0 = max(0, (today - _d0).days)
            _plazo_txt = f" · en {_dias0} día(s) = {_dias0 / 7.0:.1f} semana(s) (desde el {_d0.strftime('%d/%m/%Y')})"
        except (ValueError, TypeError):
            _plazo_txt = ""
    # El número GRANDE muestra solo lo REALIZADO (usuario 2026-08-20: "me gustaría que el número
    # grande sea solo de operaciones cerradas"). Antes mezclaba realizado + flotante, así que el
    # titular se movía con el mercado y no se sabía cuánta plata estaba cobrada de verdad. El total
    # con el flotante sigue estando, en la fila TOTAL del cuadro de abajo.
    _real_pct = (_nak_pnl_per / CAPITAL_DISPONIBLE * 100.0) if CAPITAL_DISPONIBLE > 0 else None
    _tcol = GOOD if _nak_pnl_per >= 0 else BAD
    _rot = {"Hoy": "de hoy", "Semana": "de esta semana", "Mes": "de este mes",
            "Año": "de este año", "Todo": "desde el inicio del real (día 0)"}[_nak_periodo]
    _extra = _plazo_txt if _nak_periodo == "Todo" else f" · histórico total ${_realized_all:+,.2f}"
    st.markdown(
        f"<div style='background:{_tcol}1a; border:1px solid {_tcol}55; border-radius:0.6rem; padding:0.75rem 1rem; margin:0.1rem 0 0.7rem;'>"
        f"<span style='color:{TEXT_MUTED}; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em;'>Ganancia realizada {_rot} · naked put · solo operaciones cerradas</span><br>"
        f"<span style='color:{_tcol}; font-size:1.7rem; font-weight:800;'>${_nak_pnl_per:+,.2f}</span>"
        f"<span style='color:{_tcol}; font-size:1.05rem; font-weight:700; margin-left:0.5rem;'>"
        f"{('(' + format(_real_pct, '+.2f') + '%)') if _real_pct is not None else ''}</span>"
        f"<span style='color:{TEXT_MUTED}; font-size:0.86rem; margin-left:0.6rem;'>sobre ${CAPITAL_DISPONIBLE:,.0f} de capital · "
        f"{len(_nak_cerradas)} cerrada(s){_extra}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # --- Cuadro que SEPARA lo realizado de lo abierto (usuario 2026-08-14: "sin mezclar con
    # operaciones abiertas"). Antes las dos cifras iban apretadas en una sola línea de texto chico
    # debajo del total, y no se podía leer cuánto está cobrado de verdad y cuánto todavía flota.
    _dias_op = 0
    _semanas = 0.0
    if _first_real:
        try:
            _dias_op = max(0, (today - date.fromisoformat(_first_real)).days)
            _semanas = _dias_op / 7.0
        except (ValueError, TypeError):
            pass
    _pct = lambda v: (v / CAPITAL_DISPONIBLE * 100.0) if CAPITAL_DISPONIBLE > 0 else 0.0
    _n_cerr = len(_cfg_realiz)
    _n_abie = len(_robot_open)
    _sin_pnl = [r for r in _cfg_all_closed if r["realized_pnl"] is None]

    def _fila(icono, titulo, detalle, monto, fuerte=False):
        col = GOOD if monto >= 0 else BAD
        peso = "800" if fuerte else "600"
        borde = f"border-top:2px solid {BORDER};" if fuerte else f"border-top:1px solid {BORDER}44;"
        return (
            f"<tr style='{borde}'>"
            f"<td style='padding:9px 12px;'>{icono} <span style='font-weight:{peso}'>{titulo}</span>"
            f"<div style='color:{TEXT_MUTED};font-size:0.78rem'>{detalle}</div></td>"
            f"<td style='padding:9px 12px;text-align:right;color:{col};font-weight:{peso};font-size:1.05rem;white-space:nowrap'>${monto:+,.2f}</td>"
            f"<td style='padding:9px 12px;text-align:right;color:{col};font-weight:{peso};white-space:nowrap'>{_pct(monto):+.2f}%</td>"
            f"</tr>"
        )

    # Ritmo = plata COBRADA por semana, para que acompañe al número grande (antes usaba el total con
    # el flotante y decía un ritmo que todavía no estaba en la cuenta).
    _ritmo = f" · ritmo <b>${(_realized_all / _semanas):+,.0f}</b> por semana cobrado" if _semanas >= 0.5 else ""
    st.markdown(
        f"<table style='border-collapse:collapse;width:100%;font-size:0.9rem;color:{TEXT_PRIMARY};"
        f"background:{SURFACE};border:1px solid {BORDER};border-radius:0.6rem;overflow:hidden;margin-bottom:0.5rem'>"
        f"<thead><tr style='color:{TEXT_MUTED};font-size:0.74rem;text-transform:uppercase;letter-spacing:0.04em'>"
        f"<th style='padding:8px 12px;text-align:left'>Concepto</th>"
        f"<th style='padding:8px 12px;text-align:right'>Monto</th>"
        f"<th style='padding:8px 12px;text-align:right'>% del capital</th></tr></thead><tbody>"
        + _fila("✅", "Realizado", f"plata ya cobrada · {_n_cerr} operación(es) cerrada(s)", _realized_all)
        + _fila("🕐", "Abierto (no realizado)", f"todavía flota, puede cambiar · {_n_abie} posición(es) viva(s)", _tot_unreal)
        + _fila("📊", "TOTAL", f"sobre ${CAPITAL_DISPONIBLE:,.0f} de capital", _pl_total, fuerte=True)
        + "</tbody></table>"
        + f"<div style='color:{TEXT_MUTED};font-size:0.82rem;margin-bottom:0.6rem'>"
        + (f"Operando hace <b>{_dias_op} día(s)</b> = <b>{_semanas:.1f} semana(s)</b>{_ritmo}."
           if _dias_op else "Todavía sin operaciones reales cerradas.")
        + "</div>",
        unsafe_allow_html=True,
    )
    if _sin_pnl:
        # Honestidad del número: una posición que se cerró FUERA del robot (a mano, o porque la
        # recompra no llenó y la cerraste vos) queda sin precio de salida, así que su ganancia no
        # entra en el "realizado". Sin este aviso, el total se lee como si estuviera completo.
        # Y como el usuario quiere llevar el control completo (2026-08-14), acá mismo puede cargar
        # el precio de salida real y el P&L se calcula solo.
        with st.expander(f"⚠️ {len(_sin_pnl)} operación(es) cerrada(s) SIN P&L — cargá el precio de salida",
                         expanded=False):
            st.caption("Se cerraron fuera del robot, así que su ganancia no está sumada arriba y el "
                       "realizado real es MAYOR que el que ves. Poné a cuánto recompraste cada una "
                       "(el precio de la opción, no el total) y se calcula solo.")
            for _r in _sin_pnl:
                _c = _r["filled_contracts"] or _r["final_contracts"] or 1
                _e1, _e2, _e3 = st.columns([2.4, 1, 1])
                _e1.markdown(
                    f"**{_r['symbol']} put ${_r['strike']:,.2f}** × {_c} · entrada **${_r['fill_price']:.2f}** · "
                    f"cerrada {str(_r['close_ts'] or '')[:16].replace('T', ' ')}")
                with _e2:
                    _px = st.number_input("Salida $", min_value=0.0, max_value=999.0, step=0.01, value=0.0,
                                          format="%.2f", key=f"salida_{_r['id']}", label_visibility="collapsed")
                with _e3:
                    if st.button("Guardar", key=f"guardar_salida_{_r['id']}", use_container_width=True):
                        _pnl = repo.set_real_close_price_manual(conn, _r["id"], float(_px))
                        st.session_state["_pnl_cargado"] = (_r["symbol"], _pnl)
                        st.rerun()
                _prev = round((_r["fill_price"] - float(_px)) * 100.0 * _c, 2) if _px else None
                if _prev is not None:
                    st.caption(f"   → quedaría en **${_prev:+,.2f}** de ganancia")
    if st.session_state.get("_pnl_cargado"):
        _sym, _pnl = st.session_state.pop("_pnl_cargado")
        if _pnl is None:
            st.warning(f"No se pudo guardar el P&L de {_sym}.")
        else:
            st.success(f"✅ {_sym}: P&L de **${_pnl:+,.2f}** cargado y sumado al realizado.")
    _pa, _pb, _pc, _pd, _pe, _pf = st.columns(6)
    _pa.metric("Posiciones", len(_robot_open))
    _pb.metric("P&L Open total", f"${_tot_unreal:+,.2f}", help="Ganancia/pérdida NO realizada de todo el libro abierto, desde que se abrió cada posición.")
    _pc.metric("P&L Open total %", f"{_tot_pct:+.1f}%" if _tot_pct is not None else "—",
               help="P&L Open total sobre la prima total cobrada en las posiciones abiertas.")
    _pd.metric("Crédito cobrado", f"${_tot_premium:,.0f}", help="Prima total cobrada en las posiciones abiertas.")
    _pe.metric(f"Ganancia {_nak_periodo.lower()}", f"${_nak_pnl_per:+,.2f}",
               help=f"Realizado de los naked put cerrados en el período · {len(_nak_cerradas)} operación(es). "
                    f"Exposición (strike×100): ${_tot_exp:,.0f} · Colateral usado: ${_col_usado:,.0f}.")
    _pf.metric("Win rate", f"{_nak_wr:.0f}%" if _nak_wr is not None else "—",
               help=f"Ganadoras sobre cerradas en el período: {len(_nak_wins)} de {len(_nak_cerradas)}. "
                    f"Histórico completo: {len(_cfg_realiz)} cerradas, ${_realized_all:+,.2f}.")

    _pos_cols = [
        ("ID", "ID", _fmt_plain), ("Símbolo", "Symbol", _fmt_plain),
        ("Strike", "Strike", _fmt_money),   # strike vendido (usuario 2026-08-12)
        ("Cant", "Cant", _fmt_plain),
        ("Abierta", "Abierta", _fmt_plain),   # fecha/hora de apertura (usuario 2026-08-19)
        ("Días p/ vencer", "Days", _fmt_plain),   # días que faltan para el vencimiento, actualizado a hoy
        ("Trade Price", "Trade Price", _fmt_money),
        ("Mark", "Mark", _fmt_money), ("P/L %", "P/L %", _fmt_pct),
        ("P/L Open", "P/L Open", _fmt_money), ("BP Effect", "BP Effect", _fmt_money),
        ("Estado", "Estado", _fmt_plain),
    ]
    _render_symbol_tooltip_table(
        _rows, _pos_cols,
        bg_fn=lambda r: "rgba(63,185,80,0.14)" if (r["P/L Open"] or 0) > 0 else ("rgba(248,81,73,0.14)" if (r["P/L Open"] or 0) < 0 else "transparent"),
    )
    st.caption("🟢 ganando · 🔴 perdiendo. Mark = precio actual de la opción · Trade Price = crédito cobrado · "
               "P/L Open = ganancia/pérdida no realizada · BP Effect = margen que traba en la cuenta. Mirar acá NO cierra nada.")

# --- REPORTE DIARIO (usuario 2026-08-12: "reporte diario, se resetea todos los días") — SOLO HOY. ---
st.markdown("#### 📅 Reporte diario (ganancia realizada · se resetea cada día)")
_closed = repo.get_closed_real_positions_between(conn, today, today)
_realizadas = [r for r in _closed if r["realized_pnl"] is not None]
_realized_pnl = sum((r["realized_pnl"] or 0.0) for r in _realizadas)
_wins = sum(1 for r in _realizadas if (r["realized_pnl"] or 0) > 0)
# P&L % = ganancia realizada sobre la PRIMA que se había cobrado en esas operaciones (qué % de la prima capturó).
_prem_cobrada = sum((r["fill_price"] or 0.0) * 100.0 * (r["filled_contracts"] or r["final_contracts"] or 1) for r in _realizadas)
_realized_pct = (100.0 * _realized_pnl / _prem_cobrada) if _prem_cobrada > 0 else None
_cw1, _cw2, _cw3, _cw4, _cw5 = st.columns(5)
_cw1.metric("Cerradas hoy", len(_closed))
_cw2.metric("P&L realizado", f"${_realized_pnl:+,.2f}")
_cw3.metric("P&L realizado %", f"{_realized_pct:+.1f}%" if _realized_pct is not None else "—")
_cw4.metric("Win rate", f"{(100.0 * _wins / len(_realizadas)):.0f}%" if _realizadas else "—")
_cw5.metric("Abiertas ahora", len(_robot_open))
if _closed:
    with st.expander(f"📋 Ver las {len(_closed)} cerrada(s) de hoy"):
        _reason_es = {"profit_target": "objetivo de ganancia", "stop_loss": "stop-loss", "dte_close": "cerca del vencimiento",
                      "news_close": "noticia importante", "expired": "vencida", "closed_in_broker": "cerrada en Schwab",
                      "manual_ai": "cierre manual (pedido por chat)"}
        for r in _closed:
            _pnl = r["realized_pnl"]
            _pnl_txt = f"**P&L \\${_pnl:+,.2f}**" if _pnl is not None else "P&L (reconciliar en Schwab)"
            _emoji = "🟢" if (_pnl or 0) >= 0 else "🔴"
            _cx = f"recompró a \\${r['close_fill_price']:.2f}" if r["close_fill_price"] is not None else "sin recompra"
            st.markdown(f"{_emoji} **{r['symbol']} put {r['strike']}** · crédito \\${r['fill_price']:.2f} → {_cx} · "
                        f"{_pnl_txt} · motivo: {_reason_es.get(r['close_reason'], r['close_reason'] or '—')} · "
                        f"{(r['close_ts'] or '')[:10]}")
else:
    st.caption("Todavía no hay operaciones reales cerradas hoy.")

# --- Aperturas del período (detalle) ---
if _fills:
    with st.expander(f"📋 Ver las {len(_fills)} apertura(s) real(es) del período"):
        for r in _fills:
            _cred_r = (r["fill_price"] or 0.0) * 100.0 * (r["filled_contracts"] or r["final_contracts"] or 1)
            st.markdown(f"- **{r['symbol']} put {r['strike']}** · llenó a **\\${r['fill_price']:.2f}** "
                        f"({r['filled_contracts'] or r['final_contracts']} contrato/s = \\${_cred_r:,.0f} de crédito) · "
                        f"colateral \\${r['collateral'] or 0:,.0f} · {r['log_date']} · vto {r['expiration']}")
else:
    st.caption("Todavía no hay aperturas reales llenadas en este período.")

st.caption("El robot **cierra solo** sus posiciones reales con las mismas reglas del Simulador (objetivo de ganancia "
           "escalonado, stop-loss, DTE) recomprando y caminando el precio. Las vencidas/asignadas se reconcilian "
           "contra tu cuenta Schwab.")

st.divider()

# ------------------------- Iron Condors REALES (usuario 2026-08-13) -------------------------
# El condor real "el mismo cerebro del papel pero con plata real". Se muestra como las órdenes de naked
# (misma info + estética), pero al pasar el MOUSE por el recuadro sale un pop con las alas VENDIDAS, las
# alas COMPRADAS y el crédito obtenido — todo en el tooltip del mismo cuadro.
st.markdown("### 🦅 Iron Condors reales")
_cond_cfg = settings.intraday_condor
_cond_system_on = _cond_cfg.enabled and getattr(_cond_cfg, "live_enabled", False) and lt.enabled and not lt.dry_run
_cond_armed = repo.is_condor_live_armed(conn, today)
_cond_paused = repo.is_condor_real_paused(conn)
_cond_live_on = _cond_system_on and _cond_armed and not kill and not _cond_paused
# Estado del PERMISO a la vista (usuario 2026-09-09: "me gustaría que salga algo verde cuando está
# activo autorizado"). Apretó «Re-autorizar», el botón no pide segunda confirmación, y para saber si
# había quedado tuvo que correr un script por SSH. Eso lo tiene que contestar la pantalla: cuándo fue
# la última autorización y cómo quedaron los dos contadores que dependen de ella.
_cond_ts_marca, _cond_id_marca = repo.condor_rearm_mark(conn, today)
_cond_hora_marca = ""
if _cond_ts_marca:
    try:
        _cond_hora_marca = _dt.datetime.fromisoformat(_cond_ts_marca).strftime("%H:%M")
    except (TypeError, ValueError):
        _cond_hora_marca = ""
_cond_cupo_badge = repo.get_condor_live_max_per_day(
    conn, getattr(_cond_cfg, "live_max_per_day", 1), today)
_cond_usados_badge = repo.count_real_condor_opens_today(conn, today, after_id=_cond_id_marca)
_cond_halt_badge = getattr(_cond_cfg, "stop_loss_streak_halt", 0)
_cond_racha_badge = repo.real_condor_consecutive_stop_losses_today(
    conn, today, since_ts=_cond_ts_marca)
# ¿Le queda permiso para abrir otro, acá y ahora? Es lo del PERMISO nada más: la señal (día calmo,
# VIX, crédito) la evalúa el motor con datos de mercado y no se mira desde el dashboard.
_cond_con_cupo = not (_cond_cupo_badge > 0 and _cond_usados_badge >= _cond_cupo_badge)
_cond_sin_racha = not (_cond_halt_badge > 0 and _cond_racha_badge >= _cond_halt_badge)
_cond_puede_abrir = _cond_live_on and _cond_con_cupo and _cond_sin_racha

if kill:
    _cond_badge = f"<span style='color:{BAD};font-weight:700'>● FRENADO (kill)</span>"
elif _cond_paused:
    _cond_badge = f"<span style='color:{ACCENT};font-weight:700'>⏸ PAUSADO por vos</span>"
elif _cond_puede_abrir:
    _cond_badge = (f"<span style='color:{GOOD};font-weight:700'>● AUTORIZADO Y LIBRE PARA ABRIR</span>"
                   + (f"<span style='color:{TEXT_MUTED}'> · autorizado a las {_cond_hora_marca}</span>"
                      if _cond_hora_marca else ""))
elif _cond_live_on and not _cond_sin_racha:
    _cond_badge = (f"<span style='color:{ACCENT};font-weight:700'>● AUTORIZADO — frenado por "
                   f"{_cond_racha_badge} stop-loss seguido(s)</span>"
                   f"<span style='color:{TEXT_MUTED}'> · «Re-autorizar» lo destraba</span>")
elif _cond_live_on:
    _cond_badge = (f"<span style='color:{ACCENT};font-weight:700'>● AUTORIZADO — cupo del día usado "
                   f"({_cond_usados_badge}/{_cond_cupo_badge})</span>"
                   f"<span style='color:{TEXT_MUTED}'> · «Re-autorizar» lo destraba</span>")
elif _cond_system_on and not _cond_armed:
    _cond_badge = f"<span style='color:{ACCENT};font-weight:700'>○ sistema listo — falta autorizar HOY</span>"
else:
    _cond_badge = f"<span style='color:{TEXT_MUTED};font-weight:700'>○ apagado (corre en papel)</span>"
st.markdown(
    f"{_cond_badge} &nbsp;·&nbsp; 0DTE {_cond_cfg.underlying} · "
    # OJO: esta línea va con unsafe_allow_html=True, así que el `\$` de Markdown NO se procesa y deja
    # un "\" a la vista. Se usa la entidad HTML: sale un "$" sin despertar el LaTeX de Streamlit.
    f"stop &#36;{_cond_cfg.stop_loss_dollars:,.0f} · mismo cerebro, mismo stop y profit % que el papel. "
    "**Autorización y conteo SEPARADOS de los naked.**",
    unsafe_allow_html=True,
)

# Los tres números que deciden si PUEDE abrir, en cuadros como los de arriba. Sin esto había que
# entrar por SSH a la base para saber si un clic en «Re-autorizar» había quedado (usuario 2026-09-09).
st.markdown(
    "<div style='display:flex; gap:0.45rem; flex-wrap:wrap; margin:0.1rem 0 0.7rem;'>"
    + _status_tile("Autorizado hoy",
                   (f"SÍ · {_cond_hora_marca}" if _cond_hora_marca else "SÍ") if _cond_armed else "No",
                   GOOD if _cond_armed else TEXT_MUTED)
    + _status_tile("Cupo del día", f"{_cond_usados_badge}/{_cond_cupo_badge}",
                   GOOD if _cond_con_cupo else ACCENT)
    + _status_tile("Racha stop-loss",
                   f"{_cond_racha_badge}" + (f"/{_cond_halt_badge}" if _cond_halt_badge > 0 else ""),
                   GOOD if _cond_sin_racha else ACCENT)
    + _status_tile("Puede abrir", "SÍ" if _cond_puede_abrir else "No",
                   GOOD if _cond_puede_abrir else TEXT_MUTED)
    + "</div>",
    unsafe_allow_html=True,
)
if _cond_puede_abrir:
    st.caption("🟢 El permiso está dado y el cupo libre. Que abra o no ahora depende del mercado "
               "(día calmo, VIX y que la cadena pague el crédito mínimo) — eso lo decide el motor "
               "en cada tick. Para ver qué compuerta frena: `python scripts/por_que_no_abre_condor.py`.")

# --- Métricas de utilidad / P&L del condor real, TODO por separado de los naked (usuario 2026-08-13) ---
_cond_stats = repo.get_real_condor_performance_stats(conn, today)
_co_open = repo.get_open_real_condor_positions(conn)

# P&L EN VIVO del condor abierto (usuario 2026-08-20: "en el broker sube y baja y aca se actualiza muy
# despacio"). `last_unrealized_pnl` de la base lo escribe el robot en su tick de 1 minuto, y si ese tick
# se saltea (paso hoy) el numero se queda clavado varios minutos. Aca lo recalculamos con las posiciones
# que ya trae `cached_all_positions()` (cache de 20s, la MISMA llamada que usa la tabla de naked, asi que
# no cuesta ni una consulta extra a Schwab).
#
# Cuenta: el P&L de un credito es (credito cobrado - lo que cuesta cerrar hoy). El valor de mercado que
# reporta Schwab ya viene con signo -- negativo en las patas vendidas, positivo en las compradas -- asi que
# la suma de las cuatro patas ES el costo de cerrar en negativo. Por eso alcanza con sumarla al credito.
def _condor_pnl_en_vivo(row):
    """P&L al segundo de un condor abierto, o None si no se pueden identificar las 4 patas."""
    if settings.broker.mode != "schwab":
        return None
    try:
        _legs = {
            ("put", round(float(row["short_put_strike"]), 2)), ("put", round(float(row["long_put_strike"]), 2)),
            ("call", round(float(row["short_call_strike"]), 2)), ("call", round(float(row["long_call_strike"]), 2)),
        }
        _venc = date.fromisoformat(row["expiration_date"])
        _sub = (row["underlying"] or "").strip().upper().lstrip("$")
        _valor, _vistas = 0.0, set()
        for _p in cached_all_positions():
            if _p.option_type is None or _p.strike is None or _p.expiration != _venc:
                continue
            if (_p.underlying_symbol or "").strip().upper().lstrip("$") != _sub:
                continue
            _clave = (_p.option_type, round(float(_p.strike), 2))
            if _clave in _legs:
                _valor += float(_p.market_value or 0.0)
                _vistas.add(_clave)
        if len(_vistas) != 4:      # falta alguna pata: no inventamos, se usa el ultimo marcado
            return None
        return round(float(row["entry_net_credit"] or 0.0) + _valor, 2)
    except Exception:
        return None

_cond_open_vivo = 0.0
_cond_hay_vivo = False
for _cr in _co_open:
    _v = _condor_pnl_en_vivo(_cr)
    if _v is None:
        _cond_open_vivo += float(_cr["last_unrealized_pnl"] or 0.0)
    else:
        _cond_open_vivo += _v
        _cond_hay_vivo = True
_cond_stats["open_unrealized_pnl"] = round(_cond_open_vivo, 2) if _co_open else 0.0

_cond_pl_total = round(_cond_stats["total_realized_pnl"] + _cond_stats["open_unrealized_pnl"], 2)
_cond_pl_pct = (_cond_pl_total / CAPITAL_DISPONIBLE * 100.0) if CAPITAL_DISPONIBLE > 0 else None
# Mismo filtro y misma caja que los naked, para comparar manzanas con manzanas (usuario 2026-08-21:
# "el de los iron condor debe tener filtro para ver por fecha las utilidades... lo mismo para iron").
_cp1, _cp2 = st.columns([1, 3])
_cond_periodo = _cp1.selectbox("Período de la ganancia", _PERIODOS, key="rm_cond_periodo",
                               help="Filtra la ganancia REALIZADA de los condors cerrados. Los abiertos "
                                    "se muestran siempre, no dependen del período.")
_cond_desde = _desde_periodo(_cond_periodo, today)
_cond_cerrados = [r for r in conn.execute(
    "SELECT realized_pnl, close_ts FROM real_condor_positions "
    "WHERE status='closed' AND realized_pnl IS NOT NULL").fetchall()
    if _cond_desde is None or (r["close_ts"] or "")[:10] >= _cond_desde.isoformat()]
_cond_pnl_per = round(sum((r["realized_pnl"] or 0.0) for r in _cond_cerrados), 2)
_cond_wins_per = [r for r in _cond_cerrados if (r["realized_pnl"] or 0.0) > 0]
_cond_wr_per = round(len(_cond_wins_per) / len(_cond_cerrados) * 100, 0) if _cond_cerrados else None

_cond_real_pct = (_cond_pnl_per / CAPITAL_DISPONIBLE * 100.0) if CAPITAL_DISPONIBLE > 0 else None
_ccol = GOOD if _cond_pnl_per >= 0 else BAD
_crot = {"Hoy": "de hoy", "Semana": "de esta semana", "Mes": "de este mes",
         "Año": "de este año", "Todo": "desde el primer condor"}[_cond_periodo]
# Con "Todo", el numero no dice nada sin el plazo (usuario 2026-08-28: "que diga cuantos meses va
# esa utilidad"). Con cualquier otro periodo el plazo ya esta implicito en el filtro, y ahi lo util
# es el historico. Mismo criterio que el panel de naked.
if _cond_periodo == "Todo":
    _cfechas = sorted((r["close_ts"] or "")[:10] for r in _cond_cerrados if r["close_ts"])
    if _cfechas:
        from options_advisor.dashboard.components import texto_del_periodo as _txt_per
        _d0c = date.fromisoformat(_cfechas[0])
        _cextra = (f" &middot; {_txt_per(_cfechas[0], _cfechas[-1])} operando "
                   f"(desde el {_d0c.strftime('%d/%m/%Y')})")
    else:
        _cextra = ""
else:
    _cextra = f" &middot; histórico total {_fmt_money(_cond_stats['total_realized_pnl'])}"
st.markdown(
    f"<div style='background:{_ccol}1a; border:1px solid {_ccol}55; border-radius:0.6rem; padding:0.75rem 1rem; margin:0.1rem 0 0.7rem;'>"
    f"<span style='color:{TEXT_MUTED}; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em;'>Ganancia realizada {_crot} &middot; Iron Condor real &middot; solo operaciones cerradas</span><br>"
    f"<span style='color:{_ccol}; font-size:1.7rem; font-weight:800;'>{_fmt_money(_cond_pnl_per)}</span>"
    f"<span style='color:{_ccol}; font-size:1.05rem; font-weight:700; margin-left:0.5rem;'>"
    f"{('(' + format(_cond_real_pct, '+.2f') + '%)') if _cond_real_pct is not None else ''}</span>"
    f"<span style='color:{TEXT_MUTED}; font-size:0.86rem; margin-left:0.6rem;'>sobre ${CAPITAL_DISPONIBLE:,.0f} de capital &middot; "
    f"{len(_cond_cerrados)} cerrado(s){_cextra}</span>"
    f"</div>",
    unsafe_allow_html=True,
)

_cc1, _cc2, _cc3, _cc4, _cc5, _cc6 = st.columns(6)
_cc1.metric("Abiertos ahora", _cond_stats["open_count"])
_cc2.metric("P&L abierto" + (" \u26a1" if _cond_hay_vivo else ""), _fmt_money(_cond_stats["open_unrealized_pnl"]),
            help=("EN VIVO: calculado con el valor de mercado de las 4 patas que reporta Schwab, "
                  "refrescado cada 20 segundos."
                  if _cond_hay_vivo else
                  "Ultimo valor marcado por el robot. Sin las 4 patas en Schwab no se puede calcular en vivo."))
_cc3.metric(f"Ganancia {_cond_periodo.lower()}", _fmt_money(_cond_pnl_per),
            help=f"Realizado de los condors cerrados en el período · {len(_cond_cerrados)} operación(es). "
                 f"Hoy: {_fmt_money(_cond_stats['realized_pnl_today'])}.")
_cc4.metric("P&L total (día 0)", _fmt_money(_cond_pl_total),
            help="Realizado histórico + abierto de TODOS los condors reales, desde el primero.")
_cc5.metric("P&L % / 50K", f"{_cond_pl_pct:+.2f}%" if _cond_pl_pct is not None else "—",
            help=f"P&L total del condor sobre ${CAPITAL_DISPONIBLE:,.0f} de capital disponible.")
_cc6.metric("Win rate", f"{_cond_wr_per:.0f}%" if _cond_wr_per is not None else "—",
            help=f"Ganadores sobre cerrados en el período: {len(_cond_wins_per)} de {len(_cond_cerrados)}. "
                 f"Histórico completo: {_cond_stats['closed_count']} cerrados, {_fmt_money(_cond_stats['total_realized_pnl'])}.")

# --- Autorización PROPIA del condor (botón aparte de los naked) + cuántos por día autoriza ---
# Los mismos números del badge de arriba (se calculan una sola vez, ahí): el cupo se cuenta DESDE la
# última autorización (usuario 2026-09-09: re-armar = permiso nuevo), que es lo que mira el motor.
# `_cond_abiertos_dia` es el total del día, solo para mostrar.
_cond_cap_hoy = _cond_cupo_badge
_cond_abiertos_hoy = _cond_usados_badge
_cond_abiertos_dia = repo.count_real_condor_opens_today(conn, today)
_ca1, _capa, _ca2, _ca3 = st.columns([1.3, 1.2, 1, 2.2])
with _ca1:
    if _cond_armed:
        if st.button("⏸️  Desautorizar condor hoy", use_container_width=True, key="condor_disarm",
                     help="Frena NUEVOS condors reales hoy. Lo abierto se sigue gestionando/cerrando igual."):
            repo.disarm_condor_live(conn)
            st.rerun()
        # Re-autorizar el MISMO día = permiso nuevo (usuario 2026-09-09: "si puede abrir si yo pongo
        # otra vez armar, que sea asi la regla"): pone en cero el cupo del día Y el freno por racha de
        # stop-loss, desde este momento. Los límites de capital y el kill switch no se tocan.
        if st.button("🔄  Re-autorizar (habilitar otro condor)", use_container_width=True,
                     key="condor_rearm", disabled=kill or not _cond_system_on,
                     help="Reinicia desde AHORA el cupo del día y el freno por stop-loss del condor, "
                          "para que pueda abrir otro hoy. Un stop-loss nuevo vuelve a frenarlo."):
            repo.arm_condor_live_today(conn, today)
            st.toast("Condor re-autorizado — cupo y freno por stop-loss en cero desde ahora.", icon="🔄")
            st.rerun()
    else:
        _cond_conf = st.checkbox("Confirmo operar el condor en REAL hoy", key="condor_arm_conf")
        if st.button("🦅  Autorizar condor HOY", type="primary", use_container_width=True, key="condor_arm",
                     disabled=(not _cond_conf) or kill or not _cond_system_on,
                     help="Botón SEPARADO del START de los naked. Autoriza solo al condor a abrir hoy en real."):
            repo.arm_condor_live_today(conn, today)
            st.rerun()
with _capa:
    # Pausa PROPIA del condor real (usuario 2026-08-14: "solo al real"). No toca el Simulador, que sigue
    # operando en papel. Frena SOLO aperturas nuevas: lo que ya está abierto se sigue gestionando y
    # cerrando — de una posición viva siempre tenés que poder salir.
    if _cond_paused:
        if st.button("▶️  Reanudar el condor real", type="primary", use_container_width=True,
                     key="condor_real_resume",
                     help="Vuelve a habilitar aperturas nuevas del condor REAL (sigue haciendo falta la "
                          "autorización del día)."):
            repo.set_condor_real_paused(conn, False)
            st.rerun()
    else:
        if st.button("⏸️  Pausar el condor real", use_container_width=True, key="condor_real_pause",
                     help="Frena aperturas NUEVAS del condor real hasta que lo reanudes — sin vencimiento "
                          "diario, queda pausado hasta que aprietes Reanudar. El Simulador sigue en papel "
                          "y lo que ya está abierto se sigue gestionando y cerrando igual."):
            repo.set_condor_real_paused(conn, True)
            st.rerun()
with _ca2:
    _cond_cap_new = st.number_input("Condors/día", min_value=0, max_value=10, value=int(_cond_cap_hoy), step=1,
                                    key="condor_cap_hoy", label_visibility="visible",
                                    help="Cuántos condors reales autorizás POR DÍA (aparte de los naked). "
                                         "0 = ninguno hoy. Un condor mandado ocupa el cupo del día.")
    if st.button("Guardar", use_container_width=True, key="condor_cap_save"):
        repo.set_condor_live_max_per_day(conn, int(_cond_cap_new), today)
        st.rerun()
with _ca3:
    _cond_quedan = max(0, _cond_cap_hoy - _cond_abiertos_hoy)
    _cond_desde = (" (contando desde la última autorización; en todo el día van "
                   f"**{_cond_abiertos_dia}**)" if _cond_abiertos_dia != _cond_abiertos_hoy else "")
    st.caption(f"Hoy llevás **{_cond_abiertos_hoy}** condor(s) mandado(s){_cond_desde} · "
               f"autorizás **{_cond_cap_hoy}**/día · "
               f"quedan **{_cond_quedan}**. Se resetea a la medianoche. "
               + ("⏸️ **PAUSADO** — no abre condors nuevos hasta que lo reanudes (lo abierto se sigue "
                  "gestionando). " if _cond_paused else "")
               + ("" if _cond_system_on else "⚠️ El sistema real del condor está apagado en el settings "
                  "(`intraday_condor.live_enabled`) o el trading real no está en modo REAL."))


# --- Puntuá los condors REALES (usuario 2026-08-14: "tanto en real como simulador") --------------
# Mismo bloque y mismos datos que en el Simulador, pero leyendo la tabla REAL. El `book="real"` es
# imprescindible: las dos tablas llevan ids independientes, así que sin él el condor real #7 traería
# la decisión del condor de papel #7 y estarías puntuando otra operación.
_co_cerrados_rate = repo.get_closed_real_condor_positions(conn, limit=30)
if _co_open or _co_cerrados_rate:
    _render_intraday_ratings(
        conn, "iron_condor", _co_open, _co_cerrados_rate,
        # "$" escapados: en el título de un expander Streamlit interpreta un par de $ como LaTeX.
        lambda r: (f"REAL · {r['entry_date']} · SP {r['short_put_strike']:.0f} / SC {r['short_call_strike']:.0f} · "
                   f"alas {r['short_put_strike'] - r['long_put_strike']:.0f}/"
                   f"{r['long_call_strike'] - r['short_call_strike']:.0f} pts · "
                   f"crédito \\${r['entry_net_credit']:.2f} · "
                   + (f"{r['close_reason']} \\${r['realized_pnl']:+,.2f}"
                      if r["status"] == "closed" and r["realized_pnl"] is not None
                      else ("abierto" if r["status"] == "open" else str(r["status"])))),
        "icreal", data_rows_fn=condor_data_rows, book="real",
    )
    st.divider()


# --- Lo que el condor APRENDIÓ (usuario 2026-08-14) ---------------------------------------------
# Se muestra acá, pegado al condor real, porque lo aprendido aplica a los DOS libros: el papel y el
# real usan el mismo `effective_condor`. Las propuestas grandes se aprueban en la pestaña Simulador,
# junto con las del resto del aprendizaje (una sola bandeja, no dos).
with st.expander("🧠 Lo que aprendió el condor", expanded=False):
    _cd_base = settings.intraday_condor
    _cd_eff = learning.effective_condor(conn, _cd_base)
    _cd_filas = [
        ("Delta de los cortos", "{v:.3f}", _cd_base.short_delta_max, _cd_eff.short_delta_max),
        ("Umbral de día calmo", "{v:.2%}", _cd_base.calm_range_pct, _cd_eff.calm_range_pct),
        ("Crédito mínimo", "${v:,.0f}", _cd_base.min_credit, _cd_eff.min_credit),
        ("Tope de movimiento del VIX (día lateral)", "±{v:.2f}%",
         _cd_base.max_vix_change_pct, _cd_eff.max_vix_change_pct),
        ("Objetivo de ganancia", "{v:.0%}", _cd_base.profit_target_pct, _cd_eff.profit_target_pct),
        ("Stop-loss", "${v:,.0f}", _cd_base.stop_loss_dollars, _cd_eff.stop_loss_dollars),
    ]
    _cd_cambiadas = [f for f in _cd_filas if f[2] != f[3]]
    for _lbl, _fmt, _base_v, _eff_v in _cd_filas:
        _b = _fmt.format(v=_base_v) if _base_v is not None else "sin filtro"
        _e = _fmt.format(v=_eff_v) if _eff_v is not None else "sin filtro"
        if _base_v == _eff_v:
            st.markdown(f"- **{_lbl}**: {_e}  <span style='color:{TEXT_MUTED}'>(tu valor, sin cambios)</span>",
                        unsafe_allow_html=True)
        else:
            st.markdown(f"- **{_lbl}**: {_b} → **{_e}**  <span style='color:{ACCENT}'>(aprendido)</span>",
                        unsafe_allow_html=True)
    if not _cd_cambiadas:
        st.caption(f"Todavía no cambió nada: el condor no toca ninguna perilla hasta tener "
                   f"{learning.CONDOR_MIN_EXAMPLES} operaciones cerradas (papel + real juntas). "
                   "Aprende de cada cierre y revisa una vez por día, después del cierre de mercado.")
    st.caption("El stop-loss solo se ajusta solo cuando se hace MÁS estricto. Aflojarlo siempre te "
               "queda como propuesta para aprobar en la pestaña Simulador → Aprendizaje.")

st.divider()


def _render_condor_card(row) -> str:
    """Una tarjeta de condor real como las de naked (info + estética), con POP al pasar el mouse:
    alas vendidas (put/call cortos), alas compradas (put/call largos) y crédito obtenido."""
    _st = row["status"]
    _st_txt = {"working": "NEGOCIANDO", "open": "ABIERTO", "closed": "CERRADO"}.get(_st, _st.upper())
    _st_col = GOOD if _st == "open" else (ACCENT if _st == "working" else TEXT_MUTED)
    _cred = row["entry_net_credit"] or 0.0
    _unreal = row["last_unrealized_pnl"]
    _unreal_txt = _fmt_money(_unreal) if _unreal is not None else "—"
    _unreal_col = GOOD if (isinstance(_unreal, (int, float)) and _unreal >= 0) else BAD
    _qty = row["quantity"] or 1
    # Contenido visible del recuadro (como las de naked): subyacente + strikes cortos + crédito + estado.
    _visible = (
        f"<span style='font-weight:700;color:{TEXT_PRIMARY}'>{row['underlying']} Iron Condor</span> "
        f"<span style='color:{TEXT_MUTED}'>· vto {row['expiration_date']} · {_qty}x</span><br>"
        f"<span style='color:{TEXT_MUTED};font-size:0.82rem'>vende "
        f"<b style='color:{TEXT_PRIMARY}'>{row['short_put_strike']:.0f}P</b> / "
        f"<b style='color:{TEXT_PRIMARY}'>{row['short_call_strike']:.0f}C</b> · "
        f"crédito <b style='color:{GOOD}'>${_cred:,.0f}</b> · "
        f"P&amp;L <b style='color:{_unreal_col}'>{_unreal_txt}</b> · "
        f"<b style='color:{_st_col}'>{_st_txt}</b></span>"
    )
    # POP (tooltip del mismo recuadro): alas vendidas / alas compradas / crédito.
    _pop = (
        f"<span class='oia-tt-box' style='width:250px;text-align:left;white-space:normal'>"
        f"<b style='color:{GOOD}'>Alas VENDIDAS (cobrás)</b><br>"
        f"• Put corto: <b>{row['short_put_strike']:.0f}</b><br>"
        f"• Call corto: <b>{row['short_call_strike']:.0f}</b><br>"
        f"<b style='color:{ACCENT};display:inline-block;margin-top:5px'>Alas COMPRADAS (protección)</b><br>"
        f"• Put largo: <b>{row['long_put_strike']:.0f}</b><br>"
        f"• Call largo: <b>{row['long_call_strike']:.0f}</b><br>"
        f"<span style='display:inline-block;margin-top:5px'>💰 Crédito obtenido: "
        f"<b style='color:{GOOD}'>${_cred:,.2f}</b></span><br>"
        f"<span style='color:{TEXT_MUTED};font-size:0.78rem'>Rango de ganancia: "
        f"{row['lower_breakeven']:.0f} – {row['upper_breakeven']:.0f} · riesgo máx ${row['max_loss']:,.0f}</span>"
        f"</span>"
    )
    return (
        f"<div style='background:{SURFACE};border:1px solid {BORDER};border-left:3px solid {_st_col};"
        f"border-radius:0.6rem;padding:0.6rem 0.8rem;margin-bottom:0.5rem;'>"
        f"<span class='oia-tt' style='display:block'>{_visible}{_pop}</span></div>"
    )


if _co_open:
    # Una tarjeta POR posición, cada una con su propio botón de cierre manual (usuario 2026-08-14: "si hay
    # más operaciones y quiero cerrar manual una en específico"). El dashboard NO manda la orden: deja la
    # bandera y el scheduler la ejecuta en el próximo tick, con la misma escalera de precio que el cierre
    # automático (nunca cruza el mid). Mirar una página nunca debe operar en tu cuenta.
    for _r in _co_open:
        st.markdown(_render_condor_card(_r), unsafe_allow_html=True)
        _rid = _r["id"]
        _pedido = ("manual_close_requested" in _r.keys()) and bool(_r["manual_close_requested"])
        _etiqueta = (f"#{_rid} · {_r['underlying']} "
                     f"{(_r['short_put_strike'] or 0):.0f}P/{(_r['short_call_strike'] or 0):.0f}C")
        _mc1, _mc2 = st.columns([1.5, 2.5])
        if _pedido:
            with _mc1:
                if st.button("↩️  Anular el cierre manual", use_container_width=True, key=f"cond_close_undo_{_rid}",
                             help="Vuelve a la gestión automática (objetivo de ganancia y stop-loss del papel)."):
                    repo.cancel_real_condor_manual_close(conn, _rid)
                    st.rerun()
            _mc2.caption(f"⏳ **Cierre manual pedido** para {_etiqueta} — el robot manda la recompra en el "
                         f"próximo tick y camina el precio hasta el mid. Si no llena, reintenta; podés anularlo "
                         f"hasta que llene.")
        else:
            with _mc1:
                _cerrar_ok = st.checkbox("Confirmo cerrar esta", key=f"cond_close_conf_{_rid}")
                if st.button("🔻  Cerrar ESTA operación ahora", use_container_width=True, key=f"cond_close_{_rid}",
                             disabled=not _cerrar_ok,
                             help="Ordena recomprar el condor YA, aunque no haya llegado al objetivo de "
                                  "ganancia (o esté en pérdida). Lo ejecuta el scheduler en el próximo tick."):
                    repo.request_real_condor_manual_close(conn, _rid)
                    st.rerun()
            _mc2.caption(f"Cierre manual de {_etiqueta}: recompra al precio de mercado caminando hasta el mid, "
                         f"sin esperar al objetivo de ganancia. El resto de las posiciones no se toca.")
    st.caption("💡 Pasá el mouse por el recuadro y aparece el pop con las alas vendidas, las compradas y el crédito.")
else:
    if _cond_live_on:
        st.caption("Sin condors reales abiertos ahora. El robot abre 1 por día en días CALMOS dentro de la ventana (10–14 ET).")
    elif _cond_paused:
        st.caption("Sin condors reales abiertos. El condor real está **pausado por vos** — no abre nuevos "
                   "hasta que aprietes Reanudar.")
    elif _cond_system_on:
        # El sistema SÍ está encendido en el settings: lo único que falta es la autorización del día. Antes
        # este cartel decía "apagado, para pasarlo a real poné live_enabled: true" incluso con live_enabled
        # ya en true, contradiciendo al badge de arriba (bug de copy encontrado 2026-08-14).
        st.caption("Sin condors reales abiertos. El sistema está listo — falta **autorizar el condor HOY** "
                   "con el botón de arriba para que pueda abrir.")
    else:
        st.caption("El condor real está **apagado** — corre en papel (Simulador). Para pasarlo a real: "
                   "`intraday_condor.live_enabled: true` en el settings + trading real encendido y armado.")

# ---------------------------- Cerrados (tabla) ----------------------------
# Usuario 2026-08-24: "quiero verlo asi en real tambien, como esta en simulador". Misma tabla y mismas
# columnas que la de condors del Simulador (12_simulador.py), con el mismo filtro por periodo.
#
# Diferencia deliberada con el Simulador: alla las etiquetas de motivo estan escritas a mano y dicen
# "objetivo 60%" / "stop $100", numeros viejos que YA NO son los que usa el robot. Aca se arman desde
# la config viva (`_cond_cfg`), asi que la pantalla no puede volver a desincronizarse del motor.
st.subheader("Cerrados")
_co_cerrados = repo.get_closed_real_condor_positions(conn, limit=200)


def _co_periodo(rows):
    """Filtro por periodo, copiado del Simulador para que se sienta igual. Usa `close_ts` (ISO)."""
    if not rows:
        return rows
    _dias = sorted({(r["close_ts"] or "")[:10] for r in rows if r["close_ts"]}, reverse=True)
    _sel = st.selectbox("📅 Buscar condors cerrados por período",
                        ["Todo", "Hoy", "Últimos 7 días", "Este mes"] + _dias, key="pf_co_cerr")
    if _sel == "Todo":
        return rows

    def _keep(r):
        _t = (r["close_ts"] or "")[:10]
        if not _t:
            return False
        try:
            _d = date.fromisoformat(_t)
        except ValueError:
            return False
        if _sel == "Hoy":
            return _d == today
        if _sel == "Últimos 7 días":
            return _d >= today - _timedelta(days=7)
        if _sel == "Este mes":
            return _d.year == today.year and _d.month == today.month
        return _t == _sel
    return [r for r in rows if _keep(r)]


_co_cerrados = _co_periodo(_co_cerrados)
if _co_cerrados:
    _CO_RSN = {
        "profit_target": f"🟢 objetivo {_cond_cfg.profit_target_pct:.0%}",
        "stop_loss": f"🔴 stop ${_cond_cfg.stop_loss_dollars:,.0f}",
        "expired": "⏰ vencimiento",
        "manual": "✋ cierre manual",
        "cerrado_en_el_broker": "ℹ️ cerrado en el broker",
        "no_confirmada": "⚠️ sin confirmar",
        "apertura_no_llenó": "— no llegó a abrir",
    }
    _co_rows = [{
        "Put/Call corto": f"{r['short_put_strike']:.0f} / {r['short_call_strike']:.0f}",
        "Crédito": r["entry_net_credit"],
        "Cierre": r["close_value"],
        "Motivo": _CO_RSN.get(r["close_reason"], r["close_reason"] or "—"),
        "P&L": r["realized_pnl"],
        "Hora cierre": (r["close_ts"] or "")[11:19],
    } for r in _co_cerrados]
    st.dataframe(
        pd.DataFrame(_co_rows), use_container_width=True, hide_index=True,
        column_config={
            "Crédito": st.column_config.NumberColumn(format="$%.2f"),
            "Cierre": st.column_config.NumberColumn(format="$%.2f"),
            "P&L": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    # Total del periodo elegido. Solo suma las que TIENEN P&L: una fila con P&L desconocido (por
    # ejemplo un cierre que quedo sin precio) no se cuenta como $0, porque eso mentiria el total.
    _co_con_pnl = [r for r in _co_cerrados if r["realized_pnl"] is not None]
    _co_period_pnl = sum(r["realized_pnl"] for r in _co_con_pnl)
    _co_sin_pnl = len(_co_cerrados) - len(_co_con_pnl)
    _co_nota = f" · {_co_sin_pnl} sin P&L registrado (no suman)" if _co_sin_pnl else ""
    _cou1, _cou2 = st.columns([1, 2])
    utilidad_con_periodo(_cou1, "Ganancia del período elegido", _co_period_pnl, _co_con_pnl,
                         "close_ts", "close_date")
    _cou2.caption(f"{len(_co_con_pnl)} condor(s) cerrado(s){_co_nota}.")
else:
    st.caption("Ningún condor real cerrado en el período elegido.")

st.divider()

# ------------------------- Órdenes reales (con filtros) -------------------------
# ─────────────────── TODO lo real del período, junto ───────────────────
# Usuario 2026-09-02: "quiero en real ver las operaciones que se hizo hoy, un filtro así para iron y
# naked real, que funcione porque no estaba funcionando".
#
# Lo que no funcionaba: "Órdenes que armó el robot" lee SOLO `live_order_log`, que es el camino de
# los naked. Los condors viven en `real_condor_positions` y nunca pasan por ahí. El 02/09 el robot
# abrió y cerró un condor real y la tabla decía "no hay órdenes" — correcta para naked, inútil para
# saber qué pasó en el día. Acá van los dos en una sola lista.
st.markdown("### 🧾 Todo lo que operó en REAL")
_tr1, _tr2 = st.columns([1, 3])
_todo_periodo = _tr1.selectbox("Período", _PERIODOS, key="rm_todo_periodo")
_todo_desde = _desde_periodo(_todo_periodo, today)


def _dentro(fecha_iso: str | None) -> bool:
    if not fecha_iso:
        return False
    return _todo_desde is None or str(fecha_iso)[:10] >= _todo_desde.isoformat()


def _toca_el_periodo(apertura, cierre) -> bool:
    """Una operación entra en el período si ABRIÓ o si CERRÓ dentro de él.

    Filtrar solo por la apertura dejaba afuera lo más importante del día: el 02/09 el robot cerró
    UAL (+$63) y NU (+$22), abiertos el 01/09 y el 28/08. Con "Hoy" no aparecían, y "las operaciones
    que se hizo hoy" son justamente esas — cerrar una posición es operar. Ahora una fila abierta
    ayer y cerrada hoy sale en los dos días, con las dos fechas a la vista."""
    return _dentro(apertura) or _dentro(cierre)


def _hora_de(sello) -> str:
    return str(sello)[11:19] if sello and len(str(sello)) > 10 else "—"


def _cuando_txt(sello) -> str:
    if not sello:
        return "—"
    _h = _hora_de(sello)
    return f"{str(sello)[:10]} {_h}" if _h != "—" else str(sello)[:10]


_filas_todo = []

# NAKED: solo las que se ENVIARON de verdad (sent=1). Las frenadas por el guardián están abajo.
for _o in conn.execute(
    "SELECT log_ts, sent_ts, symbol, strike, expiration, final_contracts, fill_price, "
    "       order_status, closed, close_ts, close_fill_price, realized_pnl, close_reason "
    "FROM live_order_log WHERE sent = 1 ORDER BY id DESC").fetchall():
    _cuando = _o["sent_ts"] or _o["log_ts"]
    _cerro = _o["close_ts"] if _o["closed"] else None
    if not _toca_el_periodo(_cuando, _cerro):
        continue
    _filas_todo.append({
        "Hora": _hora_de(_cuando),
        "Fecha": str(_cuando)[:10],
        "Tipo": "Naked put",
        "Detalle": f"{_o['symbol']} {_o['strike']:g} × {_o['final_contracts'] or 1}",
        "Entrada": (_o["fill_price"] or 0) * 100 * (_o["final_contracts"] or 1),
        "Cerrada": _cuando_txt(_cerro),
        "Estado": "cerrada" if _o["closed"] else (_o["order_status"] or "abierta"),
        "P&L": _o["realized_pnl"],
        "Motivo": _o["close_reason"] or "—",
        "_orden": str(_cerro or _cuando),
    })

# CONDOR REAL: todas las filas que llegaron a existir en el broker.
for _cd in conn.execute(
    "SELECT id, entry_ts, entry_date, underlying, short_put_strike, short_call_strike, "
    "       entry_net_credit, status, close_value, realized_pnl, close_reason, close_ts "
    "FROM real_condor_positions ORDER BY id DESC").fetchall():
    _cuando = _cd["entry_ts"] or _cd["entry_date"]
    _cerro = _cd["close_ts"] if _cd["status"] == "closed" else None
    if not _toca_el_periodo(_cuando, _cerro):
        continue
    _filas_todo.append({
        "Hora": _hora_de(_cuando),
        "Fecha": str(_cuando)[:10],
        "Tipo": "Iron Condor",
        "Detalle": f"{_cd['underlying']} {_cd['short_put_strike']:.0f}P/{_cd['short_call_strike']:.0f}C",
        "Entrada": _cd["entry_net_credit"],
        "Cerrada": _cuando_txt(_cerro),
        "Estado": _cd["status"],
        "P&L": _cd["realized_pnl"],
        "Motivo": _cd["close_reason"] or "—",
        "_orden": str(_cerro or _cuando),
    })

# Ordena por el ÚLTIMO movimiento (el cierre si lo hubo, si no la apertura): lo que pasó recién
# queda arriba, que es para lo que se mira esta tabla.
_filas_todo.sort(key=lambda r: r["_orden"], reverse=True)
for _f in _filas_todo:
    _f.pop("_orden", None)

if _filas_todo:
    st.dataframe(
        pd.DataFrame(_filas_todo), use_container_width=True, hide_index=True,
        column_config={
            "Entrada": st.column_config.NumberColumn("Prima/crédito", format="$%.2f"),
            "P&L": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    # El total suma SOLO lo que tiene P&L cerrado. Una posición abierta no es ganancia todavía, y
    # una fila sin P&L conocido no puede contarse como $0 sin mentir el total.
    _con_pnl = [r for r in _filas_todo if r["P&L"] is not None]
    _tot_todo = sum(r["P&L"] for r in _con_pnl)
    _abiertas = [r for r in _filas_todo if r["Estado"] in ("open", "working", "sending", "abierta")]
    _nota_ab = f" · {len(_abiertas)} todavía abierta(s)" if _abiertas else ""
    utilidad_con_periodo(_tr2, f"Resultado · {_todo_periodo.lower()}", _tot_todo, _filas_todo, "Fecha")
    st.caption(f"{len(_filas_todo)} operación(es) real(es): "
               f"{sum(1 for r in _filas_todo if r['Tipo'] == 'Naked put')} naked · "
               f"{sum(1 for r in _filas_todo if r['Tipo'] == 'Iron Condor')} condor"
               f"{_nota_ab}. El total suma las {len(_con_pnl)} que ya tienen resultado.")
else:
    st.caption("No hubo operaciones reales en el período elegido — ni naked ni condor.")

st.divider()

st.markdown("### 📋 Órdenes que armó el robot")

# Filtro por PERÍODO y por ESTADO (usuario 2026-08-10: "mostrar las que alcanzó a entrar y las que no,
# por día/semana/mes"). "Entraron" = aprobadas por el guardián (se volvieron orden real); "Frenadas" = las
# que el guardián no dejó pasar.
_fc1, _fc2 = st.columns(2)
_periodo = _fc1.selectbox("Período", ["Hoy", "Semana", "Mes", "Año", "Todo"], key="rm_periodo")
_estado_filtro = _fc2.selectbox("Mostrar", ["Todas", "Las que entraron (llenaron)", "Enviadas al broker",
                                            "Frenadas por el guardián"], key="rm_estado_filtro")

# --- Placa de votación IGUAL a la del Simulador (usuario 2026-08-10): voto general + nota + voto por
#     cada parámetro, con TODO el detalle de la decisión desplegado. Alimenta el mismo aprendizaje. ---
_VOTE_OPTS_RM = ["—", "👍", "😐", "👎"]
_VOTE_TO_FB_RM = {"👍": "good", "😐": "normal", "👎": "bad", "—": None}
_FB_TO_IDX_RM = {"good": 1, "normal": 2, "bad": 3}


def _live_param_rows(ctx, live_row):
    """(param_key, etiqueta, valor) con TODOS los datos de la decisión detrás de la orden real. Mismos
    param_keys que el Simulador, para que el voto por casillero alimente el mismo cerebro."""
    def f(v, fmt="{}", pct=False, money=False):
        if not isinstance(v, (int, float)):
            return "—"
        if pct:
            return f"{v*100:.1f}%"
        if money:
            return f"${v:,.2f}"
        return fmt.format(v)

    pop = ctx.get("chosen_pop")
    if not isinstance(pop, (int, float)):
        dl = ctx.get("chosen_delta")
        if isinstance(dl, (int, float)):
            pop = round(1 - abs(dl), 4)
    _lim = live_row["start_limit_price"] if "start_limit_price" in live_row.keys() else None
    # Bid/Ask REALES de la orden (lo que de verdad usó al mandarla), no el del contexto de la decisión — que
    # puede ser de otro momento/strike (usuario 2026-08-10: "el bid/ask decía otro número que el fill").
    _obid = live_row["bid"] if "bid" in live_row.keys() else None
    _oask = live_row["ask"] if "ask" in live_row.keys() else None
    _bidask = f"{f(_obid, money=True)} / {f(_oask, money=True)}" if (_obid is not None and _oask is not None) \
        else f"{f(ctx.get('chosen_bid'), money=True)} / {f(ctx.get('chosen_ask'), money=True)}"
    _spread = None
    if _obid is not None and _oask is not None and ((_obid + _oask) / 2) > 0:
        _spread = (_oask - _obid) / ((_obid + _oask) / 2)
    return [
        ("delta", "Delta", f(ctx.get("chosen_delta"), "{:.2f}")),
        ("pop", "POP (prob. OTM)", f(pop, pct=True)),
        ("dte", "DTE", f(ctx.get("chosen_dte"))),
        ("cobertura", "Cobertura", f(ctx.get("chosen_coverage_pct"), pct=True)),
        ("iv_hv", "IV / HV", f"{f(ctx.get('chosen_iv'), pct=True)} / {f(ctx.get('hv_20d'), pct=True)}"),
        ("iv_rank", "IV Rank", f(ctx.get("iv_rank"), "{:.0f}")),
        ("dia_pct", "% del día", f(ctx.get("day_change_pct"), "{:+.1f}%") if isinstance(ctx.get("day_change_pct"), (int, float)) else "—"),
        ("anualizado", "Anualizado", f(ctx.get("chosen_annualized_return"), pct=True)),
        ("bid_ask", "Bid / Ask (de la orden)", _bidask),
        ("spread", "Spread", f(_spread, pct=True) if _spread is not None else f(ctx.get("chosen_spread_pct"), pct=True)),
        ("open_interest", "Open Interest", f(ctx.get("chosen_open_interest"), "{:,}")),
        ("volumen", "Volumen", f(ctx.get("chosen_volume"), "{:,}")),
        ("rsi", "RSI", f(ctx.get("rsi_14"), "{:.0f}")),
        ("prima", "Prima (límite)", f"${_lim:.2f}" if isinstance(_lim, (int, float)) else "—"),
    ]


@st.fragment
def _render_live_rating(conn, live_row):
    """Placa de votación completa de una orden real: detalle desplegado + voto general + nota + voto por
    parámetro (idéntica al Simulador). Guarda en la orden real Y en la decisión del robot (aprendizaje)."""
    import json as _j
    lid = live_row["id"]
    dec = repo.get_open_decision_for(conn, live_row["symbol"], live_row["log_date"], strike=live_row["strike"])
    ctx = {}
    # 1º: el contexto EXACTO guardado en la propia orden al abrir (el dato real de ESTA orden). 2º (órdenes
    # viejas sin ese dato): la decisión del robot del MISMO strike. Así el % del día, delta, etc. son los del
    # momento en que abrió la orden, no de otra evaluación (usuario 2026-08-10).
    _octx = live_row["open_context_json"] if "open_context_json" in live_row.keys() else None
    if _octx:
        try:
            ctx = _j.loads(_octx)
        except Exception:
            ctx = {}
    elif dec is not None and dec["context_json"]:
        try:
            ctx = _j.loads(dec["context_json"])
        except Exception:
            ctx = {}
    prows = _live_param_rows(ctx, live_row)

    # Detalle read-only (todos los datos, dos columnas) — "que despliegue todo detallado igual".
    _half = (len(prows) + 1) // 2
    _left, _right = prows[:_half], prows[_half:]
    _lines = ["| Dato | Valor | Dato | Valor |", "|---|---|---|---|"]
    for i in range(_half):
        l = _left[i]
        rr = _right[i] if i < len(_right) else ("", "", "")
        _lines.append(f"| {l[1]} | **{l[2]}** | {rr[1]} | **{rr[2] if rr[1] else ''}** |")
    st.markdown("\n".join(_lines))

    _cur_fb = live_row["user_feedback"] if "user_feedback" in live_row.keys() else None
    _cur_idx = _FB_TO_IDX_RM.get(_cur_fb, 0)
    _stored = repo.get_decision_param_feedback(conn, dec["id"]) if dec is not None else {}
    with st.form(key=f"rmrate_{lid}"):
        c1, c2 = st.columns([1, 2])
        with c1:
            choice = st.radio("¿La IA operó BIEN esta orden?", ["Sin marcar", "👍 Bien", "😐 Normal", "👎 Mal"],
                              index=_cur_idx, key=f"rmfb_{lid}")
        with c2:
            note = st.text_area("📝 Tu nota (enseñale con tus palabras)",
                                value=(live_row["user_note"] if "user_note" in live_row.keys() and live_row["user_note"] else ""),
                                key=f"rmnote_{lid}", height=90,
                                placeholder="Ej: buena entrada, venía cayendo y con IV alta / se cerró tarde…")
        pw = {}
        with st.expander("🗳️ Votar cada parámetro (opcional) — enseñale casillero por casillero"):
            for pk, pl, pv in prows:
                pc1, pc2 = st.columns([3, 2])
                with pc1:
                    st.markdown(f"**{pl}:** {pv}")
                with pc2:
                    idx = _FB_TO_IDX_RM.get(_stored.get(pk), 0)
                    pw[pk] = st.radio(pl, _VOTE_OPTS_RM, index=idx, horizontal=True,
                                      key=f"rmpv_{lid}_{pk}", label_visibility="collapsed")
        if st.form_submit_button("💾 Guardar puntuación"):
            fb = {"👍 Bien": "good", "😐 Normal": "normal", "👎 Mal": "bad"}.get(choice)
            repo.set_live_order_feedback(conn, lid, fb, note or None)
            if dec is not None:
                repo.set_decision_feedback(conn, dec["id"], fb)
                repo.set_decision_note(conn, dec["id"], note)
                votes = {k: _VOTE_TO_FB_RM[v] for k, v in pw.items() if _VOTE_TO_FB_RM.get(v)}
                repo.set_decision_param_feedback(conn, dec["id"], votes)
            _marca = {"good": "👍 Bien", "normal": "😐 Normal", "bad": "👎 Mal"}.get(fb, "sin marcar")
            st.success(f"Guardado ✅ ({_marca}). Refrescá para actualizar la lista.")

if _periodo == "Hoy":
    _desde = today
elif _periodo == "Semana":
    _desde = today - _timedelta(days=today.weekday())
elif _periodo == "Mes":
    _desde = today.replace(day=1)
elif _periodo == "Año":
    _desde = today.replace(month=1, day=1)
else:
    _desde = date(2000, 1, 1)

_rows = repo.get_live_orders_between(conn, _desde, today)
if _estado_filtro.startswith("Las que entraron"):
    _rows = [r for r in _rows if _status_of(r) == "FILLED"]        # ENTRÓ = llenó de verdad
elif _estado_filtro.startswith("Enviadas"):
    _rows = [r for r in _rows if r["sent"] == 1]
elif _estado_filtro.startswith("Frenadas"):
    _rows = [r for r in _rows if r["approved"] == 0]

if not _rows:
    if armed:
        st.caption("No hay órdenes para este filtro. Cuando una entrada de la whitelist califique, va a "
                   "aparecer acá con el precio que arrancaría a negociar. (Requiere que el robot esté corriendo.)")
    else:
        st.caption("Armá el día con START para que el robot empiece a registrar lo que haría.")
else:
    import json as _json

    _llenaron = sum(1 for r in _rows if _status_of(r) == "FILLED")
    _enviadas = sum(1 for r in _rows if r["sent"] == 1)
    _frenadas = sum(1 for r in _rows if r["approved"] == 0)
    st.caption(f"{len(_rows)} decisión(es) · ✅ {_llenaron} ENTRARON (llenaron) · 📤 {_enviadas} enviadas al broker · "
               f"🚫 {_frenadas} frenadas. Tocá cada orden para ver la negociación y puntuarla 👍/👎.")

    def _keys(row):  # sqlite3.Row → set de columnas presentes (por si la migración aún no corrió)
        try:
            return set(row.keys())
        except Exception:
            return set()

    for r in _rows:
        _cols = _keys(r)
        # Resultado REAL de la orden (usuario 2026-08-10: "aprobada" no es lo mismo que "entró"). Mostramos
        # lo que de verdad pasó: entró/llenó, se canceló, la rechazó el broker, o quedó puesta esperando.
        _st = _status_of(r)
        if r["approved"] != 1:
            _estado = "🚫 Frenada por el guardián"
        elif r["dry_run"] == 1:
            _estado = "🧪 Aprobada (simulacro, no enviada)"
        elif _st == "FILLED":
            _estado = f"✅ ENTRÓ · llenó a ${r['fill_price']:.2f}" if r["fill_price"] else "✅ ENTRÓ (llenó)"
        elif _st == "REJECTED":
            _estado = "❌ Rechazada por el broker"
        elif _st == "CANCELED":
            _estado = "🚫 Cancelada (no llenó)"
        elif _st in _RESTING_STATES:
            _estado = "⏳ Puesta al mid, esperando fill"
        elif r["sent"] == 1:
            _estado = "📤 Enviada al broker"
        else:
            _estado = "✅ Aprobada"
        _acc = "Vender put" if r["action"] == "SELL_TO_OPEN" else "Recomprar put"
        _fb = r["user_feedback"] if "user_feedback" in _cols else None
        _fb_icon = " · 👍" if _fb == "good" else (" · 👎" if _fb == "bad" else "")
        _precio = f"${r['start_limit_price']:.2f}" if r["start_limit_price"] else "—"
        _hdr = (f"{(r['log_ts'] or '')[11:19]} · {r['symbol']} put {r['strike']} · {r['final_contracts']} contrato(s) "
                f"@ {_precio} · {_estado}{_fb_icon}")
        with st.expander(_hdr):
            # --- Negociación: cómo caminaría el precio y dónde queda vs el mid ---
            _ladder = []
            if "ladder_json" in _cols and r["ladder_json"]:
                try:
                    _ladder = _json.loads(r["ladder_json"])
                except Exception:
                    _ladder = []
            _bid = r["bid"] if "bid" in _cols else None
            _ask = r["ask"] if "ask" in _cols else None
            _mid = _ladder[-1] if _ladder else None
            _line = ""
            if _bid is not None and _ask is not None:
                _line = f"**Bid {_bid:.2f} / Ask {_ask:.2f}**"
                if _mid is not None:
                    _line += f" · mid {_mid:.2f}"
            elif _mid is not None:
                _line = f"mid {_mid:.2f}"
            if _line:
                st.markdown("🤝 **Negociación** — " + _line)
            if _ladder:
                _dir = "baja hasta el mid (vende alto)" if r["action"] == "SELL_TO_OPEN" else "sube hasta el mid (recompra bajo)"
                _steps = "  →  ".join(f"**{p:.2f}**" for p in _ladder)
                _iv = lt.price_walk_interval_seconds
                st.markdown(f"Arranca en **{_ladder[0]:.2f}** y {_dir}, un paso cada {_iv}s:\n\n{_steps}")
                if _mid is not None:
                    st.caption(f"El último precio ({_mid:.2f}) es el **mid** — el peor precio que aceptaría (no lo cruza).")
            else:
                st.caption("Esta orden no tiene escalera guardada (es de antes de esta actualización).")
            if r["collateral"]:
                st.markdown(f"💵 Colateral (margen) que comprometería: **\\${r['collateral']:,.0f}** · Vto {r['expiration']}")
            if r["reasons"]:
                st.markdown(f"ℹ️ {r['reasons']}")

            # --- Puntuar: MISMA placa que el Simulador (voto general + nota + voto por parámetro) ---
            if "user_feedback" in _cols:
                st.markdown("**⭐ Puntuar esta orden** (le enseña al robot — igual que en el Simulador):")
                _render_live_rating(conn, r)
            else:
                st.caption("Reiniciá el dashboard para habilitar la puntuación (falta correr la migración de la base).")

st.divider()

st.info(
    "El guardián, el START diario, el kill switch, el armado de órdenes y el caminar-el-precio están listos y "
    "probados. Cuando el robot corre con el día armado, registra acá las órdenes que armaría (dry-run) para que "
    "las revises. **Nada se envía** mientras `enabled` esté en false o en modo DRY-RUN — el paso a real lo damos "
    "juntos cuando digas que está todo OK.",
    icon="🛡️",
)

# --- Indicador de retorno anualizado del libro abierto (usuario 2026-08-10) — movido al FINAL de la página
#     (usuario 2026-08-12: "ponerlo abajo de todo"). Usa _ann_num/_ann_den/_robot_open calculados más arriba. ---
if _ann_den > 0:
    st.divider()
    st.markdown("##### 📐 Retorno anualizado de lo que tenés abierto")
    _ai1, _ai2, _ai3 = st.columns([1.1, 1.1, 1.4])
    _ann_expire = _ann_num[1.0] / _ann_den * 100.0
    _ai1.metric("Si dejás expirar (100% de la prima)", f"{_ann_expire:.1f}%",
                help="Anualizado del libro completo si todas las posiciones expiran sin valor (te quedás con toda la prima). "
                     "Fórmula igual al Simulador: (prima / margen) × (365 / DTE del trade), ponderado por el margen de cada una.")
    with _ai3:
        _preset = st.radio("Objetivo de cierre", [35, 45, 55], horizontal=True, index=1,
                           format_func=lambda x: f"{x}%",
                           help="Tu estrategia: cerrás cada put cuando ya ganaste este % de la prima, y con el capital "
                                "liberado abrís otra. Suponiendo que todas terminan ganadoras.")
        _tgt = st.number_input("…o poné otro %", min_value=1, max_value=100, value=int(_preset), step=5,
                               help="Cualquier objetivo entre 1% y 100%.")
    _cap = _tgt / 100.0
    # Anualizado de la ESTRATEGIA de cerrar al X% y REABRIR (usuario 2026-08-11): capturás X% de la prima
    # en una FRACCIÓN del tiempo y reusás el capital para otra operación. Con decaimiento tipo raíz-del-
    # tiempo (regla estándar de theta) el tiempo hasta el objetivo es f(X)=1-(1-X)²=X(2-X), así que
    # anualizado = expira × X/f(X) = expira / (2-X). Suponiendo TODAS ganadoras (sin pérdidas).
    _ann_tgt = _ann_expire / (2.0 - _cap)
    _ai2.metric(f"Si cerrás siempre al {_tgt}% (y reabrís)", f"{_ann_tgt:.1f}%",
                help="Anualizado si tu estrategia es cerrar cada put al capturar ese % de la prima y reinvertir el "
                     "capital liberado en otra, TODO el año, suponiendo que todas ganan. Cerrar antes libera el "
                     "capital y sube las vueltas al año, pero dejás algo de prima en la mesa: por eso rinde menos "
                     "que aguantar al vencimiento, pero con MENOS riesgo (no aguantás el tramo final). Estimación "
                     "con decaimiento típico de la prima (raíz del tiempo).")
    st.caption(f"Con las **{len(_robot_open)}** posición(es) abiertas y suponiendo TODAS ganadoras: dejándolas expirar "
               f"rinden **{_ann_expire:.1f}%** anual; con la estrategia de **cerrar siempre al {_tgt}%** y reabrir, "
               f"**{_ann_tgt:.1f}%** anual. Cerrar antes baja un poco el anualizado pero también el riesgo (soltás el "
               f"tramo final, que decae lento). Es una estimación sobre el margen trabado hoy.")
