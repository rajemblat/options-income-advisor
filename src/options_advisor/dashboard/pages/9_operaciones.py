from __future__ import annotations

import itertools
from datetime import date

import streamlit as st

from options_advisor.dashboard.components import (
    ACCENT,
    DATE_RANGE_OPTIONS,
    GOOD,
    TEXT_MUTED,
    filter_trades_by_date_range,
    get_connection,
    icon,
    inject_theme,
    render_header,
    render_notification_bell,
    render_real_trade_card,
)
from options_advisor.storage import repository as repo

st.set_page_config(page_title="Operaciones", page_icon="✅", layout="wide")
inject_theme()
render_header(
    icon("check-circle", size=24, color=ACCENT),
    "Operaciones",
    "Réplica automática de tus operaciones REALES de venta de opciones (cuenta Schwab) — no son "
    "sugerencias, son posiciones que ya abriste, detectadas automáticamente en cada corrida.",
)

conn = get_connection()
render_notification_bell(conn)

# El filtro sale de los símbolos que REALMENTE tienen una operación detectada, no de la
# watchlist analizada (config/symbols.yaml) — una operación real puede caer sobre cualquier
# símbolo que el usuario opere, no solo los ~15 monitoreados por el motor de sugerencias.
all_trades = repo.get_real_trade_alerts(conn, limit=200)
symbols = ["Todos"] + sorted({t["symbol"] for t in all_trades})

filter_col1, filter_col2 = st.columns(2)
with filter_col1:
    selected_symbol = st.selectbox("Símbolo", symbols)
with filter_col2:
    range_labels = list(DATE_RANGE_OPTIONS.keys())
    selected_range = st.selectbox("Rango de fechas", range_labels, index=range_labels.index("Todo"))

trades = all_trades if selected_symbol == "Todos" else [t for t in all_trades if t["symbol"] == selected_symbol]
trades = filter_trades_by_date_range(trades, selected_range, date.today())

macro = repo.get_latest_macro_snapshot(conn)
fed_meeting_date = macro["fed_meeting_date"] if macro else None
investor_profile = repo.get_investor_profile(conn)
capital_available = investor_profile.capital_available if investor_profile else None

if not trades:
    if all_trades:
        st.info("No hay operaciones que coincidan con el símbolo y/o rango de fechas seleccionado.", icon="🔍")
    else:
        st.info(
            "Todavía no se detectó ninguna operación real. Se generan automáticamente cuando se abre "
            "una posición nueva de venta de opciones en tu cuenta Schwab — no hace falta hacer nada acá, "
            "solo correr el análisis (o esperar al polling automático) después de operar.",
            icon="✅",
        )
else:
    # Agrupadas por fecha (pedido 2026-07-28: separar visualmente lo de HOY de lo viejo, "para
    # que sea obvio de un vistazo cuáles son nuevas") — `trades` ya viene ordenado DESC por
    # trade_ts desde el repo, así que fechas iguales quedan contiguas (agrupable sin resortear).
    today = date.today()
    for trade_date_str, group_iter in itertools.groupby(trades, key=lambda t: t["trade_date"]):
        group = list(group_iter)
        trade_date = date.fromisoformat(trade_date_str)
        if trade_date == today:
            label_html = f"{icon('zap', size=15, color=GOOD)} Hoy"
        else:
            label_html = f"{icon('clock', size=15, color=TEXT_MUTED)} {trade_date.strftime('%d/%m/%Y')}"
        st.markdown(
            f"<div style='font-size:1.05rem; font-weight:700; color:{TEXT_MUTED}; margin:1.4rem 0 0.6rem;'>"
            f"{label_html} · {len(group)} operación{'es' if len(group) != 1 else ''}</div>",
            unsafe_allow_html=True,
        )
        for trade in group:
            snapshot = repo.get_indicator_snapshot(conn, trade["symbol"], trade_date)
            if snapshot is not None:
                next_earnings_date = snapshot["next_earnings_date"]
                next_ex_dividend_date = snapshot["next_ex_dividend_date"]
            else:
                # El subyacente puede no estar en la watchlist analizada ese día (una operación
                # real puede caer sobre cualquier símbolo, no solo los monitoreados) — se usa el
                # último dato de earnings conocido de cualquier corrida anterior en vez de
                # dejarlo vacío.
                latest_earnings = repo.get_latest_next_earnings_date(conn, trade["symbol"])
                next_earnings_date = latest_earnings.isoformat() if latest_earnings else None
                next_ex_dividend_date = None
            render_real_trade_card(
                trade,
                next_earnings_date=next_earnings_date,
                fed_meeting_date=fed_meeting_date,
                next_ex_dividend_date=next_ex_dividend_date,
                capital_available=capital_available,
            )
