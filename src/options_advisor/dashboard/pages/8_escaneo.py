from __future__ import annotations

import os
import time

import streamlit as st

from options_advisor.dashboard.components import ACCENT, get_broker, get_connection, get_settings, icon, inject_theme, render_header, render_notification_bell
from options_advisor.config import load_symbols, load_universe_symbols
from options_advisor.scheduler.jobs import job_poll_and_analyze

st.set_page_config(page_title="Escaneo de mercado", page_icon="🔍", layout="wide")
inject_theme()
render_header(
    icon("target", size=24, color=ACCENT),
    "Escaneo de mercado amplio",
    "Busca las mejores oportunidades en un universo de cientos de símbolos, no solo tu watchlist fija",
)

conn = get_connection()
render_notification_bell(conn)
settings = get_settings()

watchlist = load_symbols()
universe = load_universe_symbols()
combined_universe = sorted(set(watchlist) | set(universe))

st.markdown(
    f"**Universo de partida**: {len(watchlist)} símbolos de tu watchlist (siempre incluidos, sin importar el ranking) "
    f"+ {len(universe)} large-caps líquidos de referencia = **{len(combined_universe)} símbolos únicos**."
)
st.caption(
    "Fase 1 (gratis, segundos): quotes en batch, filtra por optionable/precio/liquidez y rankea por volatilidad "
    "histórica (rango 52 semanas ÷ precio) — sin esto, la Fase 2 tardaría horas. Fase 2 (cara, varios minutos): "
    "corre el pipeline completo (cadena de opciones, earnings/noticias de Finnhub, narrador de Claude) solo sobre "
    "el shortlist + tu watchlist."
)

if settings.broker.mode != "schwab":
    st.info("Esta página necesita `broker.mode: schwab` — el screen barato usa datos reales de Schwab, no hay equivalente en modo mock.", icon="🔍")
else:
    broker = get_broker()

    if st.button("1. Escanear universo (Fase 1)", type="primary"):
        with st.spinner(f"Pidiendo quotes en batch de {len(combined_universe)} símbolos..."):
            t0 = time.time()
            shortlist = broker.screen_universe(combined_universe)
            elapsed = time.time() - t0
        st.session_state["scan_shortlist"] = shortlist
        st.session_state["scan_elapsed"] = elapsed

    shortlist = st.session_state.get("scan_shortlist")
    if shortlist is not None:
        st.success(f"Fase 1 lista en {st.session_state['scan_elapsed']:.1f}s — {len(shortlist)} candidatos rankeados por volatilidad histórica.")
        st.write(", ".join(shortlist))

        final_symbols = sorted(set(shortlist) | set(watchlist))
        st.markdown(f"<hr class='oia-divider'>", unsafe_allow_html=True)
        st.markdown(f"**Fase 2** correría el análisis completo sobre **{len(final_symbols)} símbolos** (shortlist + tu watchlist).")
        st.caption(
            "Con Finnhub limitado a 60 llamadas/min y narración por Claude en cada alerta nueva, esto puede tardar "
            "varios minutos — quedate en esta página hasta que termine."
        )

        if st.button("2. Analizar candidatos (Fase 2 — tarda varios minutos)", type="primary"):
            with st.spinner(f"Analizando {len(final_symbols)} símbolos... esto puede tardar varios minutos."):
                t0 = time.time()
                job_poll_and_analyze(
                    broker,
                    conn,
                    final_symbols,
                    settings,
                    anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
                    finnhub_api_key=os.environ.get("FINNHUB_API_KEY"),
                    fred_api_key=os.environ.get("FRED_API_KEY"),
                )
                elapsed = time.time() - t0
            st.success(f"Listo en {elapsed:.1f}s. Revisá la página de Alertas — ordená por score para ver las mejores oportunidades del escaneo.")
