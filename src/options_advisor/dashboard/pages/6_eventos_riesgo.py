from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from options_advisor.alerts.risk_calendar import build_risk_calendar
from options_advisor.dashboard.components import get_connection, get_symbols, inject_theme, render_header, render_notification_bell, risk_level_pill_html
from options_advisor.storage import repository as repo

LOOKAHEAD_DAYS = 30

st.set_page_config(page_title="Eventos de riesgo", page_icon="⚡", layout="wide")
inject_theme()
render_header("⚡", "Eventos de riesgo", f"Volatilidad esperada en los próximos {LOOKAHEAD_DAYS} días: FOMC, CPI, empleo y earnings de tu watchlist")

conn = get_connection()
render_notification_bell(conn)
symbols = get_symbols()
today = date.today()

macro = repo.get_latest_macro_snapshot(conn)
upcoming_events = json.loads(macro["upcoming_events_json"]) if macro and macro["upcoming_events_json"] else []

earnings_by_symbol = {symbol: repo.get_latest_next_earnings_date(conn, symbol) for symbol in symbols}

events = build_risk_calendar(upcoming_events, earnings_by_symbol, today, lookahead_days=LOOKAHEAD_DAYS)

if not events:
    st.info(
        "No hay eventos de riesgo detectados en los próximos días. Corré el análisis desde la página "
        "principal si todavía no trajiste el contexto macro (FRED/Kalshi) ni earnings (Finnhub).",
        icon="⚡",
    )
else:
    html = ["<div class='oia-card'>"]
    for event in events:
        html.append(
            "<div class='oia-leg-row'>"
            f"<span>{event['date'].isoformat()} — {event['label']}</span>"
            f"{risk_level_pill_html(event['risk_level'])}"
            "</div>"
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)

    st.caption(
        "Alto = FOMC, CPI o reporte de empleo (NFP): históricamente los que más mueven el mercado en general. "
        "Medio = earnings de un símbolo puntual — el impacto real varía mucho por empresa; no hay movimiento "
        "histórico de earnings pasados calibrado por símbolo todavía. Bajo = otros eventos macro que Finnhub "
        "reporta con impacto menor. Earnings vacíos para un símbolo = no se pudo verificar (ver página Watchlist)."
    )

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
st.subheader("📅 Calendario de earnings — toda la watchlist")

earnings_rows = [
    {"Símbolo": symbol, "Próximos earnings": d.isoformat() if d else None, "_sort": d or date.max}
    for symbol, d in earnings_by_symbol.items()
]
earnings_df = pd.DataFrame(sorted(earnings_rows, key=lambda r: r["_sort"]))[["Símbolo", "Próximos earnings"]]
st.dataframe(earnings_df, use_container_width=True, hide_index=True)
st.caption(
    "Ordenado por fecha más próxima primero (Finnhub `/calendar/earnings`). 'None' = no se pudo "
    "verificar la fecha todavía — confirmá manualmente antes de operar ese símbolo."
)
