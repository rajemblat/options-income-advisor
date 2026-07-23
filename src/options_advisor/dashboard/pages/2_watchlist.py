from __future__ import annotations

import pandas as pd
import streamlit as st

from options_advisor.dashboard.components import get_connection, get_symbols, inject_theme, render_header

st.set_page_config(page_title="Watchlist", page_icon="👀", layout="wide")
inject_theme()
render_header("👀", "Watchlist", "Último snapshot de indicadores por símbolo")

conn = get_connection()
symbols = get_symbols()

rows = []
for symbol in symbols:
    row = conn.execute(
        "SELECT * FROM indicator_snapshots WHERE symbol = ? ORDER BY snapshot_date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row:
        rows.append(dict(row))

if not rows:
    st.info("Todavía no hay snapshots. Andá a la página principal y corré el análisis.")
else:
    df = pd.DataFrame(rows)[
        [
            "symbol",
            "snapshot_date",
            "price",
            "iv_rank",
            "iv_rank_source",
            "rsi_14",
            "atr_14",
            "sma_20",
            "sma_50",
            "ma_cross_signal",
        ]
    ]
    df.columns = ["Símbolo", "Fecha", "Precio", "IV Rank", "Fuente IV Rank", "RSI", "ATR", "SMA20", "SMA50", "Cruce MA"]
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Precio": st.column_config.NumberColumn(format="$%.2f"),
            "IV Rank": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
            "RSI": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
            "SMA20": st.column_config.NumberColumn(format="$%.2f"),
            "SMA50": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    st.caption(
        "iv_rank_source = 'historical_volatility_proxy' significa que todavía no hay 12 meses "
        "de historial de IV real acumulado; se usa volatilidad histórica realizada como aproximación."
    )
