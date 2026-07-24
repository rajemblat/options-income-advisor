from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from options_advisor.dashboard.components import get_connection, get_symbols, inject_theme, render_header, render_notification_bell
from options_advisor.storage import repository as repo

WARNING_WINDOW_DAYS = 7  # ventana para marcar earnings/reunión Fed como "próximos" (Sección 4 del pedido)

st.set_page_config(page_title="Watchlist", page_icon="👀", layout="wide")
inject_theme()
render_header("👀", "Watchlist", "Último snapshot de indicadores por símbolo")

conn = get_connection()
render_notification_bell(conn)
symbols = get_symbols()

macro = repo.get_latest_macro_snapshot(conn)
fed_meeting_date = date.fromisoformat(macro["fed_meeting_date"]) if macro and macro["fed_meeting_date"] else None
today = date.today()
warning_cutoff = today + timedelta(days=WARNING_WINDOW_DAYS)


def _warning_icon(next_earnings_date: str | None) -> str:
    flags = []
    if next_earnings_date and today <= date.fromisoformat(next_earnings_date) <= warning_cutoff:
        flags.append(f"📅 Earnings {next_earnings_date}")
    if fed_meeting_date and today <= fed_meeting_date <= warning_cutoff:
        flags.append(f"🏦 FOMC {fed_meeting_date.isoformat()}")
    return "⚠️ " + " · ".join(flags) if flags else ""


rows = []
for symbol in symbols:
    row = conn.execute(
        "SELECT * FROM indicator_snapshots WHERE symbol = ? ORDER BY snapshot_date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row:
        row_dict = dict(row)
        row_dict["warning"] = _warning_icon(row_dict.get("next_earnings_date"))
        rows.append(row_dict)

if not rows:
    st.info("Todavía no hay snapshots. Andá a la página principal y corré el análisis.")
else:
    df = pd.DataFrame(rows)[
        [
            "symbol",
            "warning",
            "snapshot_date",
            "price",
            "iv_rank",
            "iv_rank_source",
            "rsi_14",
            "atr_14",
            "price_std_20",
            "net_gex",
            "sma_20",
            "sma_50",
            "ma_cross_signal",
            "next_earnings_date",
        ]
    ]
    df.columns = [
        "Símbolo", "⚠️ Próximo evento", "Fecha", "Precio", "IV Rank", "Fuente IV Rank", "RSI", "ATR", "Std Dev (20d)",
        "Net GEX", "SMA20", "SMA50", "Cruce MA", "Próx. Earnings",
    ]
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Precio": st.column_config.NumberColumn(format="$%.2f"),
            "IV Rank": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
            "RSI": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
            "Std Dev (20d)": st.column_config.NumberColumn(format="$%.2f"),
            "Net GEX": st.column_config.NumberColumn(format="%.2e"),
            "SMA20": st.column_config.NumberColumn(format="$%.2f"),
            "SMA50": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    st.caption(
        "iv_rank_source = 'historical_volatility_proxy' significa que todavía no hay 12 meses "
        "de historial de IV real acumulado; se usa volatilidad histórica realizada como aproximación. "
        "Net GEX positivo sugiere que los dealers amortiguan movimiento (compran en bajas/venden en "
        "subas); negativo, lo contrario. Próx. Earnings vacío = no se pudo verificar (ver Finnhub). "
        "En modo mock, Net GEX da 0.00 para todos los símbolos: el generador de fixtures asigna el "
        "mismo open interest a call y put de un mismo strike, que por paridad de gamma se cancelan "
        "exactamente — es una limitación del dato simulado, no del cálculo (ver indicators/gex.py, "
        "probado con gamma/OI asimétricos). Con datos reales de Schwab, call y put OI difieren y el "
        "número deja de ser trivial."
    )
