from __future__ import annotations

import json
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from options_advisor.alerts.risk_calendar import build_risk_calendar
from options_advisor.config import load_priority_watchlist_symbols
from options_advisor.dashboard.components import ACCENT, get_connection, get_symbols, icon, inject_theme, render_header, render_notification_bell, risk_level_pill_html
from options_advisor.market_context import finnhub_client
from options_advisor.storage import repository as repo

LOOKAHEAD_DAYS = 30

st.set_page_config(page_title="Eventos de riesgo", page_icon="⚡", layout="wide")
inject_theme()
render_header(
    icon("zap", size=24, color=ACCENT),
    "Eventos de riesgo",
    f"Eventos de la Fed en los próximos {LOOKAHEAD_DAYS} días: FOMC, CPI y reporte de empleo (NFP). "
    "Earnings de símbolos individuales están en el Calendario de earnings, más abajo.",
)

conn = get_connection()
render_notification_bell(conn)
# "Mi watchlist" acá es la unión de la watchlist fija (config/symbols.yaml, 15 símbolos) y la
# watchlist REAL de thinkorswim (~96 símbolos, config/watchlist_thinkorswim.yaml) — bug real
# encontrado 2026-07-27 (reportado por el usuario: "AMD y MSFT no aparecen"): esta página
# usaba solo get_symbols() (los 15 fijos), así que cualquier símbolo que el usuario realmente
# opera pero no está en esa lista corta (AMD, por ejemplo) nunca aparecía sin marcar "universo
# amplio", aunque ya tuviera un next_earnings_date calculado en la DB. Mismo patrón de unión
# que ya usa pages/8_escaneo.py.
symbols = sorted(set(get_symbols()) | set(load_priority_watchlist_symbols()))
today = date.today()

macro = repo.get_latest_macro_snapshot(conn)
upcoming_events = json.loads(macro["upcoming_events_json"]) if macro and macro["upcoming_events_json"] else []

earnings_by_symbol = {symbol: repo.get_latest_next_earnings_date(conn, symbol) for symbol in symbols}

# Sección de arriba SOLO eventos de la Fed (FOMC/CPI/empleo) — pedido explícito 2026-07-27: no
# mezclar earnings de símbolos individuales acá, esos van solo en el Calendario de earnings de
# más abajo. earnings_by_symbol={} hace que build_risk_calendar no agregue ninguna fila de
# earnings, sin tocar su firma (sigue reusándose para el calendario de rango de fechas abajo).
events = build_risk_calendar(upcoming_events, {}, today, lookahead_days=LOOKAHEAD_DAYS)

if not events:
    st.info(
        "No hay eventos de la Fed detectados en los próximos días. Corré el análisis desde la página "
        "principal si todavía no trajiste el contexto macro (FRED/Kalshi).",
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
        "Medio = otro evento macro con impacto medio según Finnhub (ej. Retail Sales). Bajo = eventos macro "
        "con impacto menor. Earnings de símbolos individuales: ver el Calendario de earnings más abajo."
    )

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
st.subheader("📅 Calendario de earnings por rango de fechas")
st.caption(
    "Buscá earnings dentro de una semana o rango específico — de tu watchlist, o (marcando la "
    "casilla) de cualquier empresa que reporte en esa ventana, no solo las que seguís."
)

preset_col, from_col, to_col = st.columns([1.3, 1, 1])
with preset_col:
    preset = st.selectbox("Atajo", ["Personalizado", "Esta semana", "Próxima semana", "Próximos 30 días"])

if preset == "Esta semana":
    default_from = today - timedelta(days=today.weekday())
    default_to = default_from + timedelta(days=6)
elif preset == "Próxima semana":
    default_from = today - timedelta(days=today.weekday()) + timedelta(days=7)
    default_to = default_from + timedelta(days=6)
elif preset == "Próximos 30 días":
    default_from, default_to = today, today + timedelta(days=30)
else:
    default_from, default_to = today, today + timedelta(days=7)

with from_col:
    range_from = st.date_input("Desde", value=default_from, key=f"earnings_from_{preset}")
with to_col:
    range_to = st.date_input("Hasta", value=default_to, min_value=range_from, key=f"earnings_to_{preset}")

include_universe = st.checkbox("Incluir universo amplio (no solo mi watchlist)", value=False)

if range_from > range_to:
    st.error("La fecha 'Desde' no puede ser posterior a 'Hasta'.")
else:
    watchlist_set = set(symbols)
    rows = [
        {"Fecha": d.isoformat(), "Símbolo": symbol, "En mi watchlist": True, "_sort": d}
        for symbol, d in earnings_by_symbol.items()
        if d and range_from <= d <= range_to
    ]

    if include_universe:
        finnhub_api_key = os.environ.get("FINNHUB_API_KEY")
        with st.spinner("Buscando earnings de todas las empresas en ese rango..."):
            universe_rows = finnhub_client.get_earnings_calendar_range(range_from, range_to, finnhub_api_key)
        already_listed = {r["Símbolo"] for r in rows}
        for row in universe_rows:
            if row["symbol"] in already_listed:
                continue  # ya viene de la watchlist arriba, no duplicar
            rows.append(
                {
                    "Fecha": row["date"],
                    "Símbolo": row["symbol"],
                    "En mi watchlist": row["symbol"] in watchlist_set,
                    "_sort": date.fromisoformat(row["date"]),
                }
            )

    if not rows:
        st.info("No se encontraron earnings en ese rango.", icon="📅")
    else:
        df = pd.DataFrame(sorted(rows, key=lambda r: (r["_sort"], r["Símbolo"])))[["Fecha", "Símbolo", "En mi watchlist"]]
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"{len(df)} resultado(s) entre {range_from.isoformat()} y {range_to.isoformat()}.")
