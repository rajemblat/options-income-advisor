from __future__ import annotations

import os
from datetime import date

import streamlit as st

from options_advisor.dashboard.components import (
    ACCENT,
    MARKET_MOVERS_INDICES,
    cached_fear_greed,
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
    render_fear_greed_gauge,
    render_portfolio_summary_panel,
    render_quote_ticker,
    render_volatility_semaphore,
)
from options_advisor.config import load_scan_symbols
from options_advisor.scheduler.jobs import job_poll_and_analyze, job_robot_scan


def render_general_page() -> None:
    """Sección "Ajustes estéticos de la página General" (pedido 2026-07-28): título/subtítulo/
    métricas/texto de navegación de la versión anterior removidos a pedido explícito del
    usuario — layout reorganizado alrededor de lo que queda (sesión + ticker + acción + los 3
    paneles de datos) para que se vea limpio sin esos elementos, no solo "los mismos huecos"."""
    st.set_page_config(page_title="Lokshn · Stock Market Overview", page_icon="📈", layout="wide", initial_sidebar_state="expanded")
    inject_theme()

    settings = get_settings()
    symbols = get_symbols()
    conn = get_connection()
    render_notification_bell(conn)

    header_col, session_col = st.columns([4, 1])
    with header_col:
        render_header(icon("trending-up", size=24, color=ACCENT), "Stock Market Overview")
    with session_col:
        render_market_session_badge()
        render_volatility_semaphore(cached_quotes(("$VIX",)).get("$VIX"))

    render_quote_ticker(cached_quotes(tuple(symbols)))

    if settings.broker.mode == "mock":
        st.info(
            "Corriendo contra **MockBrokerClient** (fixtures locales) — la conexión real a Schwab "
            "está pendiente de aprobación de credenciales. Cambiá `broker.mode` en `config/settings.yaml` "
            "cuando lleguen.",
            icon="🧪",
        )

    scan_symbols = load_scan_symbols(settings.simulator.scan_full_universe)
    st.caption(
        f"🤖 El robot escanea {len(scan_symbols)} símbolos. El botón rápido salta las alertas/IA "
        "(mucho más veloz); el completo agrega las alertas narradas por Claude."
    )
    col_fast, col_full = st.columns(2)
    with col_fast:
        if st.button("⚡ Correr robot ahora (rápido)", type="primary", use_container_width=True):
            broker = get_broker()
            finnhub_api_key = os.environ.get("FINNHUB_API_KEY")
            fred_api_key = os.environ.get("FRED_API_KEY")
            with st.spinner(f"Escaneando {len(scan_symbols)} símbolos para el robot..."):
                job_robot_scan(broker, conn, scan_symbols, settings, finnhub_api_key=finnhub_api_key, fred_api_key=fred_api_key, force=True)
            st.success("Listo. Revisá el Simulador (Decisiones/Posiciones).")
    with col_full:
        if st.button("🔬 Análisis completo (alertas + IA)", use_container_width=True):
            broker = get_broker()
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            finnhub_api_key = os.environ.get("FINNHUB_API_KEY")
            fred_api_key = os.environ.get("FRED_API_KEY")
            with st.spinner(f"Analizando {len(scan_symbols)} símbolos (completo, tarda más)..."):
                job_poll_and_analyze(broker, conn, scan_symbols, settings, api_key, finnhub_api_key=finnhub_api_key, fred_api_key=fred_api_key, force=True)
            st.success("Listo. Revisá Alertas y el Simulador.")

    st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
    # Pedido 2026-07-29: cubrir más que solo $SPX — pestañas en vez de los 3 paneles en
    # paralelo (mucho espacio visual, cada uno ya trae ganadoras+perdedoras con hasta 8 filas).
    movers_tabs = st.tabs(list(MARKET_MOVERS_INDICES.values()))
    for tab, index_code in zip(movers_tabs, MARKET_MOVERS_INDICES):
        with tab:
            render_market_movers_panel(index_code)

    st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
    # Índice de Miedo y Codicia (CNN, con espejo de respaldo) — sentimiento del mercado (usuario 2026-08-07).
    _fg = cached_fear_greed()
    _fg_col, _ = st.columns([2, 3])
    with _fg_col:
        if _fg:
            render_fear_greed_gauge(_fg)
        else:
            st.caption("📊 **Índice de Miedo y Codicia** — no se pudo cargar la fuente ahora. "
                       "Se reintenta solo al refrescar (no necesita API key).")

    st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
    render_portfolio_summary_panel(conn, date.today())

    st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
    render_macro_panel(conn)


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
        st.Page("pages/4_configuracion.py", title="Perfil y Simulación"),
        st.Page("pages/5_noticias.py", title="Noticias"),
        st.Page("pages/6_eventos_riesgo.py", title="Eventos de riesgo"),
        st.Page("pages/8_escaneo.py", title="Escaneo"),
        st.Page("pages/9_operaciones.py", title="Operaciones"),
        st.Page("pages/10_screener.py", title="Screener"),
        st.Page("pages/11_grafico.py", title="Gráfico"),
        st.Page("pages/12_simulador.py", title="Simulador"),
        st.Page("pages/13_backtesting.py", title="Backtesting"),
        st.Page("pages/14_real_market.py", title="Real Market 🔴"),
        st.Page("pages/15_asesor.py", title="Moshe 🤖"),
        st.Page("pages/16_estres.py", title="Prueba de estrés 🧪"),
    ]
)
pg.run()
