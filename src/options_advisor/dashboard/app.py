from __future__ import annotations

import os
from datetime import date

import streamlit as st

from options_advisor.dashboard.components import (
    ACCENT,
    cached_quotes,
    get_broker,
    get_connection,
    get_settings,
    get_symbols,
    icon,
    inject_theme,
    render_header,
    render_macro_panel,
    render_market_movers_panel,
    render_market_session_badge,
    render_notification_bell,
    render_portfolio_summary_panel,
    render_quote_ticker,
)
from options_advisor.scheduler.jobs import job_poll_and_analyze


def render_general_page() -> None:
    st.set_page_config(page_title="Options Income Advisor — Fase 1", page_icon="📈", layout="wide")
    inject_theme()

    settings = get_settings()
    symbols = get_symbols()
    conn = get_connection()
    render_notification_bell(conn)

    header_col, session_col = st.columns([4, 1])
    with header_col:
        render_header(
            icon("trending-up", size=24, color=ACCENT),
            "Options Income Advisor — Fase 1",
            "Escenario: Ingreso a Largo Plazo. Motor de reglas determinístico + narración con Claude.",
        )
    with session_col:
        render_market_session_badge()

    render_quote_ticker(cached_quotes(tuple(symbols)))

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
    render_market_movers_panel()

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
        - **Portafolio**: posiciones reales de tus cuentas Schwab (símbolo, cantidad, P&L).
        - **Escaneo**: busca oportunidades en un universo amplio (cientos de símbolos), no solo tu watchlist fija.
        """
    )


# st.navigation reemplaza la detección automática de la carpeta pages/ (Sección "Rediseño de
# página principal estilo CNBC" 2026-07-26, pedido: renombrar "app" a "General" en el menú —
# la detección automática siempre etiqueta el script principal con su nombre de archivo, "App",
# sin forma de sobreescribirlo). Los demás scripts de pages/ se referencian por ruta, sin
# modificarlos: cada uno sigue llamando a su propio st.set_page_config()/inject_theme() como
# antes, Streamlit solo ejecuta el script de la página seleccionada en cada rerun.
pg = st.navigation(
    [
        st.Page(render_general_page, title="General", default=True),
        st.Page("pages/1_alertas.py", title="Alertas"),
        st.Page("pages/2_watchlist.py", title="Watchlist"),
        st.Page("pages/3_indicadores.py", title="Indicadores"),
        st.Page("pages/4_configuracion.py", title="Perfil y Simulación"),
        st.Page("pages/5_noticias.py", title="Noticias"),
        st.Page("pages/6_eventos_riesgo.py", title="Eventos de riesgo"),
        st.Page("pages/7_portafolio.py", title="Portafolio"),
        st.Page("pages/8_escaneo.py", title="Escaneo"),
    ]
)
pg.run()
