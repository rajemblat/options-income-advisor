from __future__ import annotations

import json
import os
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from options_advisor.backtest import engine
from options_advisor.config import load_priority_watchlist_symbols, load_settings
from options_advisor.dashboard.components import (
    ACCENT,
    BORDER,
    CRITICAL,
    GOOD,
    SURFACE,
    TEXT_PRIMARY,
    get_connection,
    get_symbols,
    icon,
    inject_theme,
    render_header,
    render_notification_bell,
)
from options_advisor.market_context import earnings as earnings_source
from options_advisor.simulator import learning
from options_advisor.storage import repository as repo

st.set_page_config(page_title="Lokshn · Backtesting", page_icon="⏮️", layout="wide", initial_sidebar_state="expanded")
inject_theme()
render_header(
    icon("bar-chart", size=24, color=ACCENT),
    "Backtesting histórico",
    "Probá las estrategias del robot sobre el histórico REAL de tu watchlist — por reglas, por gatillo "
    "(RSI/soporte) o alrededor de earnings. Naked puts: riguroso (precio real + Black-Scholes, IV "
    "estimada). Iron Condor/Butterfly (0DTE): aproximados desde el rango diario.",
)

conn = get_connection()
render_notification_bell(conn)

settings = load_settings()
sim_settings = learning.load_effective_simulator(conn, settings.simulator)   # margen calibrado al broker
_broker = None
_labels = {"naked_put": "🟢 Naked puts", "naked_put_earnings": "📅 Naked puts (earnings)",
           "iron_condor": "🔵 Iron Condor (aprox.)", "iron_butterfly": "🟣 Iron Butterfly (aprox.)"}


@st.cache_data(ttl=3600, show_spinner=False)
def _price_history(symbol: str, lookback_days: int):
    global _broker
    if _broker is None:
        from options_advisor.broker import get_broker_client
        _broker = get_broker_client(settings)
    try:
        return _broker.get_price_history(symbol, lookback_days)
    except Exception:
        return []


@st.cache_data(ttl=86400, show_spinner=False)
def _earnings_by_symbol(symbols: tuple[str, ...], start: date, end: date) -> tuple[dict, dict]:
    """Fechas de earnings históricas POR SÍMBOLO, combinando Finnhub + Yahoo/yfinance. Devuelve
    (mapa_de_fechas, mapa_de_diagnóstico). El diagnóstico por símbolo trae cuántas fechas dio cada
    fuente y el error de Yahoo si trajo cero — así la UI explica por qué hay pocas fechas en vez de
    fallar en silencio (usuario 2026-08-07: 'hay 4 earnings al año, no puede ser 1'). Cacheadas 24 h;
    usá el botón 'Refrescar earnings' para forzar una búsqueda nueva sin esperar el TTL."""
    key = os.environ.get("FINNHUB_API_KEY")
    out: dict[str, list[date]] = {}
    diag: dict[str, dict] = {}
    for s in symbols:
        try:
            dates, d = earnings_source.historical_earnings_dates_detail(s, start, end, key)
            out[s] = dates
            diag[s] = d
        except Exception as e:
            out[s] = []
            diag[s] = {"finnhub": 0, "yahoo": 0, "yahoo_error": f"{e.__class__.__name__}: {e}"}
    return out, diag


# ------------------------------- Filtros -------------------------------
watchlist = load_priority_watchlist_symbols()
all_symbols = sorted(set(get_symbols()) | set(watchlist))
default_syms = watchlist or all_symbols[:20]

mode = st.radio("Modo de backtest", ["🤖 Reglas del robot", "🎯 Por gatillo (RSI / soporte)", "📅 Alrededor de earnings"],
                horizontal=True,
                help="Reglas: como opera el robot. Gatillo: solo abre cuando se da una condición técnica. "
                     "Earnings: vende un put antes de cada reporte y mide cómo salió.")

c1, c2 = st.columns([3, 2])
with c1:
    symbols = st.multiselect("Símbolos (tu watchlist por defecto)", all_symbols, default=default_syms)
with c2:
    today = date.today()
    dcol1, dcol2 = st.columns(2)
    start_date = dcol1.date_input("Desde", value=today - timedelta(days=365 * 5), max_value=today)
    end_date = dcol2.date_input("Hasta", value=today, max_value=today)

do_naked = do_condor = do_butterfly = False
if mode == "🤖 Reglas del robot":
    scol1, scol2, scol3 = st.columns(3)
    do_naked = scol1.checkbox("🟢 Naked puts (riguroso)", value=True)
    do_condor = scol2.checkbox("🔵 Iron Condor (aprox.)", value=True)
    do_butterfly = scol3.checkbox("🟣 Iron Butterfly (aprox.)", value=True)

with st.expander("⚙️ Parámetros (avanzado)"):
    st.caption("Estos controles son de los **naked puts**. El Iron Condor y el Iron Butterfly son 0DTE "
               "y usan sus propias reglas — se muestran abajo, cuando los tildás.")
    p1, p2, p3, p4 = st.columns(4)
    target_delta = p1.slider("Delta objetivo (put)", 0.10, 0.45, 0.25, 0.01)
    target_dte = p2.slider("DTE objetivo", 7, 60, 40, 1)
    min_cov_pct = p3.slider("Cobertura mín. (colchón ↓ hasta el strike)", 0, 20, 0, 1, format="%d%%",
                            help="Cuánto tiene que CAER la acción para tocar el strike (colchón hacia abajo). "
                                 "7% = solo vende puts que están al menos 7% por debajo del precio actual.")
    min_cov = min_cov_pct / 100.0
    iv_mult = p4.slider("IV ≈ HV ×", 1.0, 1.5, 1.15, 0.05)
    rsi_max = None
    near_support_pct = None
    days_before = 5
    if mode == "🎯 Por gatillo (RSI / soporte)":
        g1, g2 = st.columns(2)
        use_rsi = g1.checkbox("Solo si RSI(14) ≤", value=True)
        rsi_val = g1.slider("RSI máximo", 10, 60, 35, 1, disabled=not use_rsi)
        rsi_max = rsi_val if use_rsi else None
        use_sup = g2.checkbox("Solo si el precio está cerca de un soporte", value=False)
        sup_val = g2.slider("Dentro de este % arriba del soporte", 0.01, 0.10, 0.03, 0.01, disabled=not use_sup)
        near_support_pct = sup_val if use_sup else None
    earnings_hold_through = True
    if mode == "📅 Alrededor de earnings":
        e1, e2 = st.columns(2)
        days_before = e1.slider("Vender el put cuántos días hábiles ANTES del earnings", 1, 15, 5, 1)
        earn_mode = e2.radio("¿Qué hace con el reporte?",
                             ["Aguantar el earnings (vencimiento después del reporte)",
                              "Salir ANTES del earnings (cierra el día previo)"],
                             help="Aguantar: el put abarca el reporte, capturás el gap (más riesgo, más prima). "
                                  "Salir antes: cobrás el decaimiento y cerrás antes del reporte (evitás el gap).")
        earnings_hold_through = earn_mode.startswith("Aguantar")
        # Botón para forzar una búsqueda nueva de fechas de earnings (usuario 2026-08-07): la búsqueda
        # se cachea 24 h; si acabás de instalar yfinance o Yahoo estaba con límite de tasa, el caché
        # viejo te sigue mostrando pocas fechas. Esto lo limpia sin tener que reiniciar el dashboard.
        if st.button("🔄 Refrescar earnings (limpiar caché de fechas)", help="Vuelve a pedir las fechas a Yahoo/Finnhub, ignorando el caché de 24 h."):
            _earnings_by_symbol.clear()
            st.rerun()

    cap1, cap2 = st.columns(2)
    initial_capital = cap1.number_input("💼 Capital de la cuenta ($)", min_value=1000, max_value=10_000_000,
                                        value=100_000, step=10_000,
                                        help="El backtest simula una cuenta con este capital: las operaciones compiten "
                                             "por la plata y se saltean si no hay disponible.")
    max_pct_per_trade = cap2.slider("Máx. capital por operación (%)", 1, 50, 10, 1, format="%d%%",
                                    help="Cuánto del capital puede usar UNA operación. 10% de $100K = hasta $10K por operación.") / 100.0

    q1, q2 = st.columns(2)
    _pt_opts = {"Reglas del robot (escalonado)": None, "30% plano": 0.30, "45% plano": 0.45, "65% plano": 0.65}
    pt_choice = q1.selectbox("🎯 Tomar ganancia (profit)", list(_pt_opts.keys()),
                             help="Plano = cierra apenas la ganancia llega a ese % de la prima. "
                                  "Escalonado = las reglas del robot (18% sem1 / 30% sem2 / 45% <20DTE).")
    profit_target_pct = _pt_opts[pt_choice]
    model_assignment = q2.checkbox("🔄 Modelar asignaciones como la rueda (te quedás las acciones y las vendés al recuperar)",
                                   value=True,
                                   help="ON: al vencer ITM, te quedás con las 100 acciones y las vendés cuando el precio "
                                        "recupera el break-even (más realista, baja el drawdown). OFF: cuenta la asignación "
                                        "como pérdida al vencimiento (peor caso).")

npar = engine.NakedPutParams(target_delta=target_delta, target_dte=target_dte, min_coverage=min_cov,
                             iv_mult=iv_mult, rsi_max=rsi_max, near_support_pct=near_support_pct,
                             profit_target_pct=profit_target_pct, model_assignment=model_assignment)
# Los 0DTE NO usan los controles de arriba (delta objetivo, DTE, cobertura): esos son de los naked
# puts. El condor y el butterfly se miden con SUS PROPIAS reglas, y con los valores EFECTIVOS — los
# que el aprendizaje ya ajustó — no con los de config.
#
# Hasta el 2026-08-23 había UN solo juego de parámetros para las dos estrategias, tomado del condor.
# O sea que el butterfly se venía midiendo con 35% del crédito y stop de $100, cuando su regla real
# es cerrar a +$50 FIJOS con stop de -$70 (usuario 2026-08-10: "scalp rápido"). El backtest del
# butterfly estaba describiendo una estrategia que no existe.
_cfg_condor = learning.effective_condor(conn, settings.intraday_condor)
_cfg_fly = learning.effective_butterfly(conn, settings.intraday_butterfly)

from dataclasses import replace as _replace
ipar_condor = _replace(engine.params_del_condor(_cfg_condor), iv_mult=iv_mult)
ipar_fly = _replace(engine.params_del_butterfly(_cfg_fly), iv_mult=iv_mult)

if do_condor or do_butterfly:
    _c1, _c2 = st.columns(2)
    if do_condor:
        _c1.caption(
            f"🔵 **Iron Condor** se corre con SUS reglas (no con el delta/DTE de arriba): "
            f"cierra al **{ipar_condor.profit_target_pct:.0%}** del crédito, stop **${ipar_condor.stop_loss_dollars:,.0f}**, "
            f"cortos a delta **{ipar_condor.short_delta:.2f}** (≈{engine.sigmas_para_delta(ipar_condor.short_delta):.2f}σ), "
            f"y **solo en días calmos** (rango del día anterior ≤ {ipar_condor.calm_range_pct:.2%}).")
    if do_butterfly:
        _c2.caption(
            f"🟣 **Iron Butterfly** se corre con SUS reglas: cierra a **+${ipar_fly.profit_dollars:,.0f} fijos**, "
            f"stop **-${ipar_fly.stop_loss_dollars:,.0f}**. Necesita acertar más de "
            f"**{ipar_fly.stop_loss_dollars / (ipar_fly.profit_dollars + ipar_fly.stop_loss_dollars):.1%}** "
            f"solo para empatar.")

run = st.button("▶️ Correr backtest", type="primary", use_container_width=True)
lookback_days = (today - start_date).days + 40


def _load_bars(syms):
    bars_by = {}
    prog = st.progress(0.0, text="Cargando histórico…")
    for i, s in enumerate(syms):
        raw = _price_history(s, lookback_days)
        bars_by[s] = [b for b in raw if start_date <= b.trade_date <= end_date]
        prog.progress((i + 1) / len(syms), text=f"Cargando histórico… {s}")
    prog.empty()
    return bars_by


if run:
    if not symbols:
        st.warning("Elegí al menos un símbolo.")
        st.stop()
    if start_date >= end_date:
        st.warning("El rango de fechas no es válido.")
        st.stop()

    bars_by = _load_bars(symbols)
    st.session_state["bt_bars"] = bars_by
    earnings_map = {}
    if mode == "📅 Alrededor de earnings":
        with st.spinner("Buscando fechas de earnings…"):
            earnings_map, earnings_diag = _earnings_by_symbol(tuple(symbols), start_date, end_date)
        _fh = sum(d.get("finnhub", 0) for d in earnings_diag.values())
        _yh = sum(d.get("yahoo", 0) for d in earnings_diag.values())
        _got = sum(len(v) for v in earnings_map.values())
        _syms_ok = sum(1 for v in earnings_map.values() if v)
        # Errores de Yahoo por símbolo (solo los que fallaron) para explicar el motivo real.
        _yerrs = sorted({d["yahoo_error"] for d in earnings_diag.values() if d.get("yahoo_error")})
        if _got == 0:
            st.warning(
                f"No traje ninguna fecha de earnings **en el rango {start_date}…{end_date}**. "
                f"Finnhub aportó {_fh} y Yahoo/yfinance {_yh}.\n\n"
                + (("**Motivo de Yahoo:** " + " · ".join(_yerrs) + "\n\n") if _yerrs else "")
                + "Finnhub free suele dar solo la PRÓXIMA fecha (futura, no sirve para backtestear), así que las "
                "pasadas dependen de Yahoo. Si Yahoo dice 'límite de tasa', esperá unos minutos y tocá "
                "**🔄 Refrescar earnings**. Los otros modos (Reglas / Gatillo) andan igual mientras tanto."
            )
        else:
            _msg = f"📅 {_got} fecha(s) de earnings en {_syms_ok} símbolo(s) · Yahoo: {_yh} · Finnhub: {_fh}."
            if _yerrs:
                _msg += " ⚠️ Yahoo con avisos: " + " · ".join(_yerrs)
            st.caption(_msg)

    all_trades: list[engine.BacktestTrade] = []
    with st.spinner("Corriendo el backtest…"):
        for s, bars in bars_by.items():
            if len(bars) < 30:
                continue
            if mode == "📅 Alrededor de earnings":
                all_trades.extend(engine.backtest_earnings_puts(bars, earnings_map.get(s, []), s, sim_settings,
                                                                npar, days_before=days_before,
                                                                hold_through=earnings_hold_through))
            elif mode == "🎯 Por gatillo (RSI / soporte)":
                all_trades.extend(engine.backtest_naked_puts(bars, s, sim_settings, npar))
            else:
                if do_naked:
                    all_trades.extend(engine.backtest_naked_puts(bars, s, sim_settings, npar))
                if do_condor:
                    all_trades.extend(engine.backtest_iron_condor_daily(bars, s, ipar_condor))
                if do_butterfly:
                    all_trades.extend(engine.backtest_iron_butterfly_daily(bars, s, ipar_fly))
    st.session_state["bt_trades"] = all_trades
    st.session_state["bt_mode"] = mode


trades = st.session_state.get("bt_trades")
if trades is not None:
    if not trades:
        st.info("No se generaron operaciones con ese modo/parámetros/rango. Probá ampliar la ventana o aflojar los gatillos.")
        st.stop()

    st.divider()
    st.subheader("Resultado del backtest")
    overall = engine.summarize(trades)

    def _m(v):
        """Formato compacto de dinero (k/M) para que los números grandes entren en la tarjeta."""
        a = abs(v)
        sign = "-" if v < 0 else ""
        if a >= 1e6:
            return f"${sign}{a / 1e6:.2f}M"
        if a >= 1e3:
            return f"${sign}{a / 1e3:.1f}k"
        return f"${sign}{a:.0f}"

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Operaciones", f"{overall['n']:,}")
    k2.metric("Win rate", f"{overall['win_rate']:.1f}%")
    k3.metric("P&L total", _m(overall["total_pnl"]))
    k4.metric("Anualizado medio", f"{overall['avg_annualized']:.0f}%")
    k5.metric("Máx. drawdown", _m(overall["max_drawdown"]), help="La peor caída del P&L acumulado desde un pico (cuánto llegó a estar bajo agua).")

    eq = overall["equity_curve"]
    if eq:
        edf = pd.DataFrame(eq)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=pd.to_datetime(edf["date"]), y=edf["equity"], mode="lines",
                                 line=dict(color=ACCENT, width=2), name="P&L acumulado"))
        fig.add_hline(y=0, line_dash="dot", line_color=BORDER)
        fig.update_layout(height=320, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(color=TEXT_PRIMARY),
                          margin=dict(t=20, b=20), title="Curva de ganancia acumulada (por fecha de cierre)")
        fig.update_xaxes(gridcolor=BORDER)
        fig.update_yaxes(gridcolor=BORDER, title_text="P&L acumulado ($)")
        st.plotly_chart(fig, use_container_width=True)

    # --- Cuenta de $100K (simulación de cartera con capital compartido) ---
    port = engine.portfolio_replay(trades, float(initial_capital), max_pct_per_trade)
    st.markdown(f"#### 💼 Cuenta de {_m(port['initial_capital'])} (capital compartido, real)")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Equity final", _m(port["final_equity"]), f"{port['return_pct']:+.1f}%")
    a2.metric("Ganancia neta", _m(port["realized_pnl"]))
    a3.metric("Pico de capital usado", _m(port.get("peak_capital_used", 0.0)), f"{port.get('peak_capital_pct', 0.0):.0f}% del capital")
    a4.metric("Operaciones tomadas", f"{port['taken']:,}", f"{port['skipped']:,} salteadas por falta de capital",
              delta_color="off")
    e1, e2, e3 = st.columns(3)
    e1.metric("Exposición pico (notional)", _m(port.get("peak_exposure", 0.0)),
              help="El máximo de VALOR del subyacente que tuviste comprometido a la vez (strike×100×contratos): "
                   "lo que tendrías que comprar si TODO se asigna. Es tu riesgo real, distinto del colateral.")
    e2.metric("Exposición vs cuenta", f"{port.get('peak_exposure_pct', 0.0):.0f}%",
              help=">100% = estuviste apalancado (más notional que tu capital). Ej. 300% = 3× tu cuenta expuesta.")
    e3.metric("Retorno sobre exposición", f"{port.get('return_on_exposure_pct', 0.0):+.1f}%",
              help="Ganancia neta ÷ exposición pico. Es el retorno 'real' contra el riesgo que tomaste, no contra el margen chico.")
    _pc = port["equity_curve"]
    if _pc:
        pdf = pd.DataFrame(_pc)
        pfig = go.Figure()
        pfig.add_trace(go.Scatter(x=pd.to_datetime(pdf["date"]), y=pdf["equity"], mode="lines",
                                  line=dict(color=GOOD, width=2), name="Equity de la cuenta"))
        pfig.add_hline(y=port["initial_capital"], line_dash="dot", line_color=BORDER)
        pfig.update_layout(height=300, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(color=TEXT_PRIMARY),
                           margin=dict(t=20, b=20), title=f"Equity de la cuenta (arrancó en {_m(port['initial_capital'])})")
        pfig.update_xaxes(gridcolor=BORDER)
        pfig.update_yaxes(gridcolor=BORDER, title_text="Equity ($)")
        st.plotly_chart(pfig, use_container_width=True)
    st.caption(f"Simula una cuenta de {_m(port['initial_capital'])}: cada operación usa hasta {max_pct_per_trade * 100:.0f}% "
               "del capital y se **saltea** si no hay plata libre. **Colateral** = lo que se traba (margen). "
               "**Exposición** = el notional del subyacente que arriesgás si te asignan (strike×100). Vender puts usa poco "
               "colateral pero puede tener mucha exposición — por eso conviene mirar el retorno sobre exposición, no solo sobre el margen.")

    # --- Cómo se ganó (desglose honesto, usuario 2026-08-07) ---
    st.markdown("#### 🔎 Cómo se ganó (desglose)")
    npos = overall["n"]
    _reasons_es = {"profit_target": "🎯 objetivo de ganancia", "near_strike": "⚠️ cerca del strike",
                   "expired": "⏳ venció OTM (ganó la prima)", "stop_loss": "🛑 stop",
                   "assigned_recovered": "🔄 asignado y recuperado (vendió al break-even)",
                   "assigned_open": "📦 asignado, todavía con acciones al final",
                   "pre_earnings_exit": "📅 cerrado antes del earnings"}
    b1, b2, b3 = st.columns(3)
    b1.metric("Ganadoras", f"{overall['wins']:,}", f"aportaron {_m(overall['gross_win'])}")
    b2.metric("Perdedoras", f"{overall['losses']:,}", f"restaron {_m(overall['gross_loss'])}", delta_color="inverse")
    b3.metric("Asignaciones (venció ITM)", f"{overall['assignments']:,}", f"costaron {_m(overall['assignment_loss'])}", delta_color="inverse")
    st.caption(
        f"De **{npos:,} operaciones**: {overall['wins']:,} ganadoras (prom. {_m(overall['avg_win'])} c/u) sumaron "
        f"{_m(overall['gross_win'])}, y {overall['losses']:,} perdedoras (prom. {_m(overall['avg_loss'])} c/u) "
        f"restaron {_m(overall['gross_loss'])} → **neto {_m(overall['total_pnl'])}**. "
        + " · ".join(f"{_reasons_es.get(k, k)}: {v:,}" for k, v in sorted(overall["by_reason"].items(), key=lambda x: -x[1]))
    )
    _assign_txt = ("🔄 **Asignaciones = rueda (activado):** cuando un put vence ITM, el backtest **te queda con las "
                   "100 acciones** al strike y las **vende cuando el precio recupera el break-even** (strike − prima). "
                   "Así una asignación en una caída no es una pérdida fija: si la acción rebota, recuperás — como operás "
                   "de verdad. Si al final del histórico seguís con acciones, se marcan a último precio."
                   if model_assignment else
                   "⚠️ **Asignaciones = peor caso (rueda desactivada):** cada put que vence ITM se cuenta como pérdida "
                   "al vencimiento por su valor intrínseco (no se arrastran acciones).")
    st.info(_assign_txt + " Todavía **no** modela **rolls** ni vender **calls cubiertos** sobre las asignadas "
            "(eso mejoraría más el número). Fills a precio medio, sin comisiones.")

    # Por estrategia (si hay más de una)
    strats = sorted({t.strategy for t in trades})
    if len(strats) > 1:
        st.markdown("#### Por estrategia")
        rows = []
        for strat in strats:
            s = engine.summarize([t for t in trades if t.strategy == strat])
            rows.append({"Estrategia": _labels.get(strat, strat), "Operaciones": s["n"], "Win rate %": s["win_rate"],
                         "P&L total": s["total_pnl"], "Anualizado % medio": s["avg_annualized"], "Drawdown $": s["max_drawdown"]})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                     column_config={"P&L total": st.column_config.NumberColumn(format="$%.0f"),
                                    "Drawdown $": st.column_config.NumberColumn(format="$%.0f")})

    # --- Comparador por empresa (ranking) ---
    st.markdown("#### 🏆 Comparador por empresa")
    sym_rows = []
    for s in {t.symbol for t in trades}:
        ss = engine.summarize([t for t in trades if t.symbol == s])
        sym_rows.append({"Símbolo": s, "Operaciones": ss["n"], "Win rate %": ss["win_rate"],
                         "P&L total": ss["total_pnl"], "Anualizado % medio": ss["avg_annualized"]})
    sym_df = pd.DataFrame(sym_rows).sort_values("P&L total", ascending=False)
    rank_metric = st.radio("Rankear por", ["P&L total", "Win rate %", "Anualizado % medio"], horizontal=True, key="rankmetric")
    top = sym_df.sort_values(rank_metric, ascending=False).head(25)
    bar = go.Figure(go.Bar(x=top[rank_metric], y=top["Símbolo"], orientation="h",
                           marker_color=[GOOD if v >= 0 else CRITICAL for v in top[rank_metric]]))
    bar.update_layout(height=max(300, 22 * len(top)), paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
                      font=dict(color=TEXT_PRIMARY), margin=dict(t=10, b=10), yaxis=dict(autorange="reversed"),
                      title=f"Empresas por {rank_metric}")
    bar.update_xaxes(gridcolor=BORDER)
    st.plotly_chart(bar, use_container_width=True)
    st.dataframe(sym_df, use_container_width=True, hide_index=True,
                 column_config={"P&L total": st.column_config.NumberColumn(format="$%.0f")})

    # --- Peores operaciones ---
    st.markdown("#### Peores operaciones (cómo se porta cuando duele)")
    worst = sorted(trades, key=lambda t: t.pnl)[:10]
    wrows = [{"Símbolo": t.symbol, "Estrategia": _labels.get(t.strategy, t.strategy),
              "Entrada": t.entry_date.isoformat(), "Salida": t.exit_date.isoformat(),
              "Strike": t.strike or "—", "Motivo": t.close_reason, "P&L": t.pnl} for t in worst]
    st.dataframe(pd.DataFrame(wrows), use_container_width=True, hide_index=True,
                 column_config={"P&L": st.column_config.NumberColumn(format="$%.0f")})

    st.caption("⚠️ Naked puts = riguroso (precio real + Black-Scholes; IV estimada como HV×"
               f"{iv_mult:g}, captura las pérdidas por asignación en caídas). Iron Condor/Butterfly = "
               "**aproximados** desde el rango diario (no hay intradía histórico de años atrás). Fills teóricos "
               "(mid), sin comisiones ni asignación anticipada. Earnings vía Finnhub.")

    # --- Aprendizaje ---
    st.divider()
    st.markdown("#### 🧠 Que el robot aprenda de esto")
    st.caption("Corre el backtest de naked puts a varios deltas sobre los símbolos elegidos y guarda en "
               "Aprendizaje qué delta rindió mejor. No cambia nada solo — es para que lo veas y decidas.")
    if st.button("🧠 Enseñarle al robot lo que muestra el histórico"):
        bars_by = st.session_state.get("bt_bars", {})
        if not bars_by:
            st.warning("Corré primero un backtest.")
        else:
            with st.spinner("Barriendo deltas…"):
                ranking = sorted(engine.sweep_delta(bars_by, sim_settings, base=npar),
                                 key=lambda r: r["total_pnl"], reverse=True)
            st.dataframe(pd.DataFrame(ranking), use_container_width=True, hide_index=True,
                         column_config={"total_pnl": st.column_config.NumberColumn(format="$%.0f")})
            best = ranking[0] if ranking else None
            if best:
                resumen = (f"Backtesting ({start_date} a {end_date}, {len(bars_by)} símbolos): mejor delta "
                           f"histórico = {best['delta']:.2f} (win {best['win_rate']:.1f}%, P&L ${best['total_pnl']:,.0f}, "
                           f"anualizado {best['avg_annualized']:.0f}%).")
                repo.insert_learning_report(conn, best["n"], resumen,
                                            json.dumps({"source": "backtest", "ranking": ranking}, default=str))
                st.success(f"Aprendizaje guardado ✅ — lo ves en **Aprendizaje** del Simulador. Mejor delta: **{best['delta']:.2f}**.")
else:
    st.info("Elegí el modo, los símbolos y el rango, y tocá **Correr backtest**. Por defecto: tu watchlist, últimos 5 años.")
