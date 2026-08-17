from __future__ import annotations

import itertools
import logging
from datetime import date

import streamlit as st

from options_advisor.alerts.formatting import strategy_label
from options_advisor.alerts.real_trades import detect_and_alert_real_trades
from options_advisor.dashboard.components import (
    ACCENT,
    DATE_RANGE_OPTIONS,
    GOOD,
    TEXT_MUTED,
    filter_by_date_range,
    get_broker,
    get_connection,
    get_settings,
    group_roll_pairs,
    icon,
    inject_theme,
    primary_trade_for_group,
    render_header,
    render_notification_bell,
    render_real_trades_table,
    render_roll_group,
)
from options_advisor.storage import repository as repo

st.set_page_config(page_title="Lokshn · Operaciones", page_icon="✅", layout="wide", initial_sidebar_state="expanded")
inject_theme()
render_header(
    icon("check-circle", size=24, color=ACCENT),
    "Operaciones",
    "Réplica automática de tus operaciones REALES de venta de opciones (cuenta Schwab) — no son "
    "sugerencias, son posiciones que ya abriste, detectadas automáticamente en cada corrida.",
)

conn = get_connection()
render_notification_bell(conn)

# Botón de refresh. ANTES solo recargaba la base: si el robot todavía no había detectado la operación,
# apretarlo no servía de nada y había que esperar (usuario 2026-08-17: "demora más de 5 minutos y debe
# demorar menos de 10 segundos"). Ahora VA A BUSCARLAS A SCHWAB en el momento: le pregunta por las
# órdenes llenadas de la última hora y guarda las que falten. Es la misma función que corre el robot
# (`detect_and_alert_real_trades`), así que no hay dos lógicas que puedan divergir.
#
# Es seguro apretarlo muchas veces: la detección es idempotente — se saltea las patas ya alertadas
# (`get_alerted_order_leg_keys`), así que no duplica nada. Y si Schwab falla, la página sigue
# mostrando lo que ya estaba guardado en vez de romperse.
_REFRESH_LOOKBACK_MIN = 60   # 1 hora: cubre de sobra cualquier operación que el robot no haya visto aún
_rc1, _rc2 = st.columns([4, 1])
with _rc2:
    if st.button("🔄 Actualizar", use_container_width=True,
                 help="Le pregunta a Schwab AHORA por las operaciones de la última hora y actualiza la lista."):
        with st.spinner("Buscando en Schwab…"):
            try:
                _br = get_broker()
                _nuevas = detect_and_alert_real_trades(
                    _br, conn, get_settings(), date.today(),
                    share_positions=_br.get_all_share_positions(),
                    anthropic_api_key=None, finnhub_api_key=None,
                    lookback_minutes=_REFRESH_LOOKBACK_MIN,
                )
                st.session_state["_ops_refresh"] = ("ok", len(_nuevas))
            except Exception as _exc:
                logging.getLogger(__name__).exception("Operaciones: fallo el refresh manual")
                st.session_state["_ops_refresh"] = ("error", str(_exc)[:180])
        st.rerun()

_msg = st.session_state.pop("_ops_refresh", None)
if _msg and _msg[0] == "ok":
    st.success(f"✅ Encontré **{_msg[1]}** operación(es) nueva(s) en Schwab." if _msg[1]
               else "✅ Al día — Schwab no tiene ninguna operación que falte acá.")
elif _msg:
    st.warning(f"No pude consultar Schwab ahora ({_msg[1]}). Abajo está lo último que el robot guardó.")

# El filtro sale de los símbolos que REALMENTE tienen una operación detectada, no de la
# watchlist analizada (config/symbols.yaml) — una operación real puede caer sobre cualquier
# símbolo que el usuario opere, no solo los ~15 monitoreados por el motor de sugerencias.
all_trades = repo.get_real_trade_alerts(conn, limit=200)
symbols = ["Todos"] + sorted({t["symbol"] for t in all_trades})

# Rolls agrupados ANTES de filtrar (pedido 2026-07-30, corregido el mismo día: la vista de
# Tabla ahora es la default, 1 fila compacta por operación — apertura o roll, sin distinguir
# layout entre las dos) — agrupar temprano deja que Símbolo/Rango/Estrategia/Tipo filtren
# sobre la unidad "operación" completa (ambas patas de un roll juntas), no patas sueltas.
all_groups = group_roll_pairs(all_trades)
strategy_options = ["Todas"] + sorted({primary_trade_for_group(g)["strategy_type"] for g in all_groups})

# Toggle Tabla/Tarjetas (pedido 2026-07-30, corregido el mismo día): Tabla pasa a ser la vista
# PRINCIPAL/default — fila compacta por operación, clic para el detalle completo. Tarjetas
# sigue disponible como alternativa (avisos/P&L/comentario siempre expandidos, sin tener que
# hacer clic), no se eliminó nada.
view_col, filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([1, 1, 1, 1, 1])
with view_col:
    selected_view = st.radio("Vista", ["Tabla", "Tarjetas"], horizontal=True, label_visibility="collapsed")
with filter_col1:
    selected_symbol = st.selectbox("Símbolo", symbols)
with filter_col2:
    range_labels = list(DATE_RANGE_OPTIONS.keys())
    selected_range = st.selectbox("Rango de fechas", range_labels, index=range_labels.index("Todo"))
with filter_col3:
    selected_strategy = st.selectbox("Estrategia", strategy_options, format_func=lambda s: s if s == "Todas" else strategy_label(s))
with filter_col4:
    selected_type = st.selectbox("Tipo", ["Todos", "Apertura", "Roll"])

trades = all_trades if selected_symbol == "Todos" else [t for t in all_trades if t["symbol"] == selected_symbol]
trades = filter_by_date_range(trades, selected_range, date.today())

groups = group_roll_pairs(trades)
if selected_strategy != "Todas":
    groups = [g for g in groups if primary_trade_for_group(g)["strategy_type"] == selected_strategy]
if selected_type != "Todos":
    want_roll = selected_type == "Roll"
    groups = [g for g in groups if (len(g) > 1) == want_roll]

# Cartel de transparencia (usuario 2026-08-07): dejá clarísimo qué rango se está mostrando y con qué
# fecha de referencia, para que "Hoy" nunca se confunda con datos viejos de una vista sin refrescar.
_hoy = date.today()
_rango_txt = f"Hoy ({_hoy.strftime('%d/%m')})" if selected_range == "Hoy" else selected_range
st.caption(f"📅 Mostrando **{len(groups)}** operación(es) · rango: **{_rango_txt}** · fecha de referencia: {_hoy.strftime('%d/%m/%Y')}")

macro = repo.get_latest_macro_snapshot(conn)
fed_meeting_date = macro["fed_meeting_date"] if macro else None
investor_profile = repo.get_investor_profile(conn)
capital_available = investor_profile.capital_available if investor_profile else None

if not groups:
    if all_trades:
        st.info("No hay operaciones que coincidan con los filtros seleccionados.", icon="🔍")
    else:
        st.info(
            "Todavía no se detectó ninguna operación real. Se generan automáticamente cuando se abre "
            "una posición nueva de venta de opciones en tu cuenta Schwab — no hace falta hacer nada acá, "
            "solo correr el análisis (o esperar al polling automático) después de operar.",
            icon="✅",
        )
elif selected_view == "Tabla":
    render_real_trades_table(groups, conn, fed_meeting_date, capital_available)
else:
    # Agrupadas por fecha (pedido 2026-07-28: separar visualmente lo de HOY de lo viejo, "para
    # que sea obvio de un vistazo cuáles son nuevas") — `groups` ya viene ordenado (preserva el
    # orden DESC por trade_ts del repo), así que fechas iguales quedan contiguas.
    today = date.today()
    for trade_date_str, group_iter in itertools.groupby(groups, key=lambda g: g[0]["trade_date"]):
        day_groups = list(group_iter)
        trade_date = date.fromisoformat(trade_date_str)
        if trade_date == today:
            label_html = f"{icon('zap', size=15, color=GOOD)} Hoy"
        else:
            label_html = f"{icon('clock', size=15, color=TEXT_MUTED)} {trade_date.strftime('%d/%m/%Y')}"
        st.markdown(
            f"<div style='font-size:1.05rem; font-weight:700; color:{TEXT_MUTED}; margin:1.4rem 0 0.6rem;'>"
            f"{label_html} · {len(day_groups)} operación{'es' if len(day_groups) != 1 else ''}</div>",
            unsafe_allow_html=True,
        )
        for group in day_groups:
            symbol = primary_trade_for_group(group)["symbol"]
            snapshot = repo.get_indicator_snapshot(conn, symbol, trade_date)
            if snapshot is not None:
                next_earnings_date = snapshot["next_earnings_date"]
                next_ex_dividend_date = snapshot["next_ex_dividend_date"]
            else:
                # El subyacente puede no estar en la watchlist analizada ese día (una operación
                # real puede caer sobre cualquier símbolo, no solo los monitoreados) — se usa el
                # último dato de earnings conocido de cualquier corrida anterior en vez de
                # dejarlo vacío.
                latest_earnings = repo.get_latest_next_earnings_date(conn, symbol)
                next_earnings_date = latest_earnings.isoformat() if latest_earnings else None
                next_ex_dividend_date = None
            render_roll_group(
                group,
                next_earnings_date=next_earnings_date,
                fed_meeting_date=fed_meeting_date,
                next_ex_dividend_date=next_ex_dividend_date,
                capital_available=capital_available,
            )
