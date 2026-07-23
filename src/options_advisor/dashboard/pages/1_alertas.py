from __future__ import annotations

from datetime import date

import streamlit as st

from options_advisor.dashboard.components import get_connection, get_symbols, inject_theme, render_alert_card, render_header
from options_advisor.storage import repository as repo

st.set_page_config(page_title="Alertas", page_icon="🔔", layout="wide")
inject_theme()
render_header("🔔", "Alertas — Ingreso a Largo Plazo")

conn = get_connection()
symbols = ["Todos"] + get_symbols()
selected = st.selectbox("Símbolo", symbols)

alerts = repo.get_alerts(conn, symbol=None if selected == "Todos" else selected, limit=200)

if not alerts:
    st.info("Todavía no hay alertas generadas. Andá a la página principal y corré el análisis.")
else:
    for alert in alerts:
        candidate = conn.execute(
            "SELECT * FROM candidate_contracts WHERE id = ?", (alert["candidate_contract_id"],)
        ).fetchone()
        snapshot = repo.get_indicator_snapshot(conn, alert["symbol"], date.fromisoformat(alert["alert_date"]))
        next_earnings_date = snapshot["next_earnings_date"] if snapshot else None
        render_alert_card(alert, candidate, next_earnings_date=next_earnings_date)
