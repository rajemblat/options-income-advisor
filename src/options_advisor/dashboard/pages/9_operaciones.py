from __future__ import annotations

from datetime import date

import streamlit as st

from options_advisor.dashboard.components import (
    ACCENT,
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
selected_symbol = st.selectbox("Símbolo", symbols)

trades = all_trades if selected_symbol == "Todos" else [t for t in all_trades if t["symbol"] == selected_symbol]

macro = repo.get_latest_macro_snapshot(conn)
fed_meeting_date = macro["fed_meeting_date"] if macro else None
investor_profile = repo.get_investor_profile(conn)
capital_available = investor_profile.capital_available if investor_profile else None

if not trades:
    st.info(
        "Todavía no se detectó ninguna operación real. Se generan automáticamente cuando se abre "
        "una posición nueva de venta de opciones en tu cuenta Schwab — no hace falta hacer nada acá, "
        "solo correr el análisis (o esperar al polling automático) después de operar.",
        icon="✅",
    )
else:
    for trade in trades:
        snapshot = repo.get_indicator_snapshot(conn, trade["symbol"], date.fromisoformat(trade["trade_date"]))
        if snapshot is not None:
            next_earnings_date = snapshot["next_earnings_date"]
            next_ex_dividend_date = snapshot["next_ex_dividend_date"]
        else:
            # El subyacente puede no estar en la watchlist analizada ese día (una operación real
            # puede caer sobre cualquier símbolo, no solo los monitoreados) — se usa el último
            # dato de earnings conocido de cualquier corrida anterior en vez de dejarlo vacío.
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
