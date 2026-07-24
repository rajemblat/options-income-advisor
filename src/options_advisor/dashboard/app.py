from __future__ import annotations

import os
from datetime import date

import streamlit as st

from options_advisor.dashboard.components import (
    get_broker,
    get_connection,
    get_settings,
    get_symbols,
    inject_theme,
    render_header,
    render_macro_panel,
    render_notification_bell,
    render_portfolio_summary_panel,
)
from options_advisor.scheduler.jobs import job_poll_and_analyze

st.set_page_config(page_title="Options Income Advisor — Fase 1", page_icon="📈", layout="wide")
inject_theme()

render_header("📈", "Options Income Advisor — Fase 1", "Escenario: Ingreso a Largo Plazo. Motor de reglas determinístico + narración con Claude.")

settings = get_settings()
symbols = get_symbols()
conn = get_connection()
render_notification_bell(conn)

col1, col2, col3 = st.columns(3)
col1.metric("Modo de broker", settings.broker.mode)
col2.metric("Símbolos monitoreados", len(symbols))
col3.metric("Umbral (perfil moderado)", settings.conviction_thresholds.moderado)

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)

if settings.broker.mode == "mock":
    st.info(
        "Corriendo contra **MockBrokerClient** (fixtures locales) — la conexión real a Schwab "
        "está pendiente de aprobación de credenciales. Cambiá `broker.mode` en `config/settings.yaml` "
        "cuando lleguen.",
        icon="🧪",
    )

if st.button("🔄 Correr análisis ahora", type="primary"):
    broker = get_broker()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    finnhub_api_key = os.environ.get("FINNHUB_API_KEY")
    fred_api_key = os.environ.get("FRED_API_KEY")
    with st.spinner(f"Analizando {len(symbols)} símbolos..."):
        job_poll_and_analyze(broker, conn, symbols, settings, api_key, finnhub_api_key=finnhub_api_key, fred_api_key=fred_api_key)
    st.success("Listo. Revisá la página de Alertas.")

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
render_portfolio_summary_panel(conn, date.today())

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
render_macro_panel(conn)

st.markdown(
    """
    Usá el menú de la izquierda para navegar:
    - **Alertas**: oportunidades detectadas, con la explicación narrada.
    - **Watchlist**: último snapshot de indicadores por símbolo.
    - **Indicadores**: detalle histórico de un símbolo (IV Rank, RSI, precio).
    - **Configuración**: perfil de inversor y umbrales de convicción.
    - **Noticias**: últimas noticias por símbolo (Finnhub).
    - **Eventos de riesgo**: calendario de volatilidad esperada (FOMC, CPI, empleo, earnings).
    """
)
