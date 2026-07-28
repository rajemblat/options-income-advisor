from __future__ import annotations

import pandas as pd
import streamlit as st

from options_advisor.dashboard.components import ACCENT, get_broker, get_connection, get_settings, icon, inject_theme, render_header, render_notification_bell
from options_advisor.dashboard.scanner_table import build_scanner_rows
from options_advisor.dashboard.screener_filters import (
    DELTA_BUCKETS,
    MONEYNESS_BUCKETS,
    OPEN_INTEREST_BUCKETS,
    VOLUME_BUCKETS,
    apply_filters,
    filter_by_strategy_group,
)
from options_advisor.storage import repository as repo

_STRATEGY_CHOICE_TO_GROUP = {"Naked Put": "naked_put", "Covered Call": "covered_call", "Ambas": None}

st.set_page_config(page_title="Screener", page_icon="🔎", layout="wide")
inject_theme()
render_header(
    icon("target", size=24, color=ACCENT),
    "Screener",
    "Buscador de opciones con filtros ajustables — combiná varios criterios a la vez sobre los "
    "candidatos recientes de Naked Put / Covered Call.",
)

conn = get_connection()
render_notification_bell(conn)
settings = get_settings()

candidates = repo.get_recent_single_leg_candidates(conn)
symbols = sorted({c["symbol"] for c in candidates})

instrument_types: dict[str, str | None] = {}
if symbols and settings.broker.mode == "schwab":
    broker = get_broker()
    quotes = broker.get_quotes(symbols)
    instrument_types = {symbol: q.instrument_type for symbol, q in quotes.items()}

all_rows = build_scanner_rows(candidates, risk_free_rate=settings.market.risk_free_rate, instrument_types=instrument_types)

if not all_rows:
    st.info(
        "Todavía no hay candidatos de una sola pata para filtrar. Corré el análisis desde la página "
        "principal o Escaneo para generarlos.",
        icon="🔎",
    )
else:
    st.markdown("#### Estrategia")
    strategy_choice = st.radio(
        "Estrategia", list(_STRATEGY_CHOICE_TO_GROUP), horizontal=True, label_visibility="collapsed"
    )
    strategy_rows = filter_by_strategy_group(all_rows, _STRATEGY_CHOICE_TO_GROUP[strategy_choice])

    st.markdown("#### Filtros")
    col1, col2, col3 = st.columns(3)
    with col1:
        dte_values = [r["DTE"] for r in strategy_rows if r["DTE"] is not None]
        dte_min, dte_max = (min(dte_values), max(dte_values)) if dte_values else (0, 60)
        dte_range = st.slider("Días a expiración (DTE)", min_value=0, max_value=max(dte_max, 60), value=(dte_min, dte_max))
    with col2:
        strike_values = [r["Strike"] for r in strategy_rows if r["Strike"] is not None]
        strike_min, strike_max = (min(strike_values), max(strike_values)) if strike_values else (0.0, 1000.0)
        strike_range = st.slider("Strike Price", min_value=0.0, max_value=float(strike_max), value=(float(strike_min), float(strike_max)))
    with col3:
        min_prob_otm = st.slider("Probabilidad OTM mínima (%)", min_value=0, max_value=100, value=0)
        if not settings.broker.mode == "schwab":
            st.caption("Requiere modo Schwab (necesita implied volatility real por pata).")

    col4, col5, col6 = st.columns(3)
    with col4:
        delta_buckets = st.multiselect("Delta", list(DELTA_BUCKETS), default=[])
    with col5:
        moneyness_buckets = st.multiselect("Moneyness", list(MONEYNESS_BUCKETS), default=[])
    with col6:
        instrument_type_options = sorted({t for t in instrument_types.values() if t is not None})
        instrument_type_labels = {"stock": "Acción", "etf": "ETF", "index": "Índice"}
        selected_instrument_types = st.multiselect(
            "Tipo de instrumento",
            instrument_type_options,
            default=[],
            format_func=lambda t: instrument_type_labels.get(t, t),
        )
        if settings.broker.mode != "schwab":
            st.caption("Requiere modo Schwab (Quote.instrument_type no está disponible en modo mock).")

    # Volume/Open Interest solo se persisten en candidatos generados DESPUÉS del fix del
    # 2026-07-27 (ver BACKLOG.md #28) — bug real reportado por el usuario esa misma noche:
    # "Screener muestra 0 resultados incluso quitando todos los filtros restrictivos". Root
    # cause: candidatos VIEJOS (persistidos antes del fix) tienen Volume/Open Interest en None
    # SIEMPRE, así que filtrar por cualquier balde de Volumen/OI excluye TODO el dataset actual
    # (0 de 412 candidatos tienen este dato hoy) — no era un bug en la lógica de filtrado (ya
    # verificado: sin filtros da 412/412), sino un dato todavía no poblado. Aviso explícito acá
    # para que no parezca que el screener está roto.
    rows_with_volume = sum(1 for r in strategy_rows if r["Volume"] is not None)
    rows_with_oi = sum(1 for r in strategy_rows if r["Open Interest"] is not None)

    col7, col8 = st.columns(2)
    with col7:
        volume_buckets = st.multiselect("Volumen", list(VOLUME_BUCKETS), default=[])
        if rows_with_volume < len(strategy_rows):
            st.caption(f"⚠️ Solo {rows_with_volume}/{len(strategy_rows)} candidatos tienen este dato — se va poblando con cada análisis nuevo.")
    with col8:
        open_interest_buckets = st.multiselect("Open Interest", list(OPEN_INTEREST_BUCKETS), default=[])
        if rows_with_oi < len(strategy_rows):
            st.caption(f"⚠️ Solo {rows_with_oi}/{len(strategy_rows)} candidatos tienen este dato — se va poblando con cada análisis nuevo.")

    filtered_rows = apply_filters(
        strategy_rows,
        dte_range=dte_range,
        strike_range=strike_range,
        delta_buckets=delta_buckets or None,
        volume_buckets=volume_buckets or None,
        open_interest_buckets=open_interest_buckets or None,
        moneyness_buckets=moneyness_buckets or None,
        instrument_types=selected_instrument_types or None,
        min_probability_otm=min_prob_otm if min_prob_otm > 0 else None,
    )

    st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
    st.markdown(f"**{len(filtered_rows)}** de {len(strategy_rows)} candidato(s) cumplen todos los filtros activos.")

    if not strategy_rows:
        st.info(f"Ningún candidato de {strategy_choice} generado todavía — probá con otra estrategia o corré el análisis de nuevo.", icon="🔎")
    elif not filtered_rows:
        hint = ""
        if volume_buckets and rows_with_volume == 0:
            hint = " Ningún candidato actual tiene dato de Volumen todavía — probá sin ese filtro."
        elif open_interest_buckets and rows_with_oi == 0:
            hint = " Ningún candidato actual tiene dato de Open Interest todavía — probá sin ese filtro."
        st.info(f"Ningún candidato cumple esta combinación de filtros — probá aflojar alguno.{hint}", icon="🔎")
    else:
        df = pd.DataFrame(filtered_rows)
        st.dataframe(
            df,
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
                "Probabilidad OTM (%)": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
        st.caption(
            "Probabilidad OTM ≠ POP: Probabilidad OTM mide contra el STRIKE, POP mide contra el BREAKEVEN "
            "(ya incluye el colchón de la prima cobrada) — Probabilidad OTM siempre es menor o igual al POP "
            "para la misma posición. Moneyness/%BE positivo = del lado OTM (a favor del vendedor)."
        )
