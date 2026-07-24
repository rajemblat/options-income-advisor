from __future__ import annotations

import os
from datetime import date, datetime

import streamlit as st

from options_advisor.dashboard.components import (
    ACCENT,
    get_broker,
    get_connection,
    get_settings,
    get_symbols,
    icon,
    inject_theme,
    render_alert_card,
    render_header,
    render_notification_bell,
)
from options_advisor.scheduler.jobs import job_poll_and_analyze
from options_advisor.storage import repository as repo
from options_advisor.storage.models import InvestorProfile

RISK_LEVEL_LABELS = {"conservador": "Conservador", "moderado": "Normal", "agresivo": "Agresivo"}
RISK_LEVEL_KEYS = ["conservador", "moderado", "agresivo"]

st.set_page_config(page_title="Alertas", page_icon="🔔", layout="wide")
inject_theme()
render_header(icon("bell", size=24, color=ACCENT), "Alertas — Ingreso a Largo Plazo")

conn = get_connection()
render_notification_bell(conn)
settings = get_settings()

col_symbol, col_risk = st.columns(2)
with col_symbol:
    symbols = ["Todos"] + get_symbols()
    selected_symbol = st.selectbox("Símbolo", symbols)
with col_risk:
    risk_options = ["Todos"] + RISK_LEVEL_KEYS
    selected_risk = st.selectbox("Perfil de riesgo", risk_options, format_func=lambda k: RISK_LEVEL_LABELS.get(k, k))

alerts = repo.get_alerts(conn, symbol=None if selected_symbol == "Todos" else selected_symbol, limit=200)
if selected_risk != "Todos":
    alerts = [a for a in alerts if a["risk_profile"] == selected_risk]

if selected_risk != "Todos":
    current_profile = repo.get_investor_profile(conn)
    active_risk_level = current_profile.risk_level if current_profile else settings.investor_profile.risk_level
    st.caption(
        f"Filtrando por perfil **{RISK_LEVEL_LABELS[selected_risk]}** — solo se muestran alertas ya generadas con "
        "ese perfil activo en el momento del análisis (el perfil ajusta qué strikes arma el motor, no es un filtro "
        "visual retroactivo)."
    )
    if st.button(f"🔄 Regenerar alertas con perfil {RISK_LEVEL_LABELS[selected_risk]}", type="primary"):
        base = current_profile.model_dump() if current_profile else settings.investor_profile.model_dump()
        base.pop("updated_at", None)
        base["risk_level"] = selected_risk
        repo.upsert_investor_profile(conn, InvestorProfile(**base, updated_at=datetime.now()))
        broker = get_broker()
        with st.spinner(f"Analizando {len(get_symbols())} símbolos con perfil {RISK_LEVEL_LABELS[selected_risk]}..."):
            job_poll_and_analyze(
                broker,
                conn,
                get_symbols(),
                settings,
                os.environ.get("ANTHROPIC_API_KEY"),
                finnhub_api_key=os.environ.get("FINNHUB_API_KEY"),
                fred_api_key=os.environ.get("FRED_API_KEY"),
            )
        st.success("Listo — recargando.")
        st.rerun()

macro = repo.get_latest_macro_snapshot(conn)
fed_meeting_date = macro["fed_meeting_date"] if macro else None

if not alerts:
    st.info("Todavía no hay alertas generadas para este filtro. Corré el análisis o probá otro perfil.")
else:
    for alert in alerts:
        candidate = conn.execute(
            "SELECT * FROM candidate_contracts WHERE id = ?", (alert["candidate_contract_id"],)
        ).fetchone()
        snapshot = repo.get_indicator_snapshot(conn, alert["symbol"], date.fromisoformat(alert["alert_date"]))
        next_earnings_date = snapshot["next_earnings_date"] if snapshot else None
        render_alert_card(alert, candidate, next_earnings_date=next_earnings_date, fed_meeting_date=fed_meeting_date)
