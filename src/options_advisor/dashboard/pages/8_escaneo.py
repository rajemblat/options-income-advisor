from __future__ import annotations

import os
import time

import pandas as pd
import streamlit as st

from options_advisor.dashboard.components import ACCENT, get_broker, get_connection, get_settings, icon, inject_theme, render_header, render_notification_bell
from options_advisor.config import load_priority_watchlist_symbols, load_symbols, load_universe_symbols
from options_advisor.dashboard.scanner_table import build_scanner_rows
from options_advisor.scheduler.jobs import job_poll_and_analyze
from options_advisor.storage import repository as repo

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
priority_watchlist = load_priority_watchlist_symbols()
universe = load_universe_symbols()
always_included = sorted(set(watchlist) | set(priority_watchlist))
combined_universe = sorted(set(always_included) | set(universe))

st.markdown(
    f"**Universo de partida**: {len(watchlist)} símbolos de tu watchlist fija + {len(priority_watchlist)} de tu "
    f"watchlist real de thinkorswim (siempre incluidos, sin importar el ranking) "
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

        final_symbols = sorted(set(shortlist) | set(always_included))
        st.markdown(f"<hr class='oia-divider'>", unsafe_allow_html=True)
        st.markdown(f"**Fase 2** correría el análisis completo sobre **{len(final_symbols)} símbolos** (shortlist + tu watchlist fija + tu watchlist real de thinkorswim).")
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

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
st.subheader("📊 Vista tabla (ordenable)")
st.caption(
    "Estilo Barchart — todos los candidatos recientes de Naked Put / Covered Call en una sola tabla plana, "
    "hacé clic en cualquier columna para ordenar (Return, IV Rank, POP, etc.). Solo estrategias de una sola "
    "pata vendida (Cash-Secured Put, Short Put, Covered Call, Short Call) — los spreads/Iron Condor tienen "
    "más de un strike y no entran en una fila plana como esta."
)

scanner_rows = build_scanner_rows(repo.get_recent_single_leg_candidates(conn))
if not scanner_rows:
    st.info(
        "Todavía no hay candidatos de una sola pata para mostrar acá. Corré el análisis (Fase 2 arriba, o "
        "desde la página principal) para generarlos.",
        icon="📊",
    )
else:
    scanner_df = pd.DataFrame(scanner_rows)
    st.dataframe(
        scanner_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Price": st.column_config.NumberColumn(format="$%.2f"),
            "Strike": st.column_config.NumberColumn(format="$%.2f"),
            "Moneyness (%)": st.column_config.NumberColumn(format="%.2f%%"),
            "Bid": st.column_config.NumberColumn(format="$%.2f"),
            "Breakeven": st.column_config.NumberColumn(format="$%.2f"),
            "%BE": st.column_config.NumberColumn(format="%.2f%%"),
            "IV Rank": st.column_config.NumberColumn(format="%.0f"),
            "Delta": st.column_config.NumberColumn(format="%.3f"),
            "Return (%)": st.column_config.NumberColumn(format="%.2f%%"),
            "Rendimiento Anualizado (%)": st.column_config.NumberColumn(format="%.1f%%"),
            "POP (%)": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )
    st.caption(
        f"{len(scanner_df)} candidato(s). Moneyness/%BE positivo = del lado OTM (a favor del vendedor), "
        "mismo sentido que la 'Cobertura' de las tarjetas de Alertas. Return (%) es el retorno del PERÍODO "
        "sobre el capital en riesgo (prima / pérdida máxima) — no anualizado, esa es la columna de al lado."
    )
