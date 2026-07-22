from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from options_advisor.dashboard.components import get_connection, get_symbols

st.set_page_config(page_title="Indicadores", page_icon="📊", layout="wide")
st.title("📊 Detalle de indicadores por símbolo")

conn = get_connection()
symbol = st.selectbox("Símbolo", get_symbols())

rows = conn.execute(
    "SELECT * FROM indicator_snapshots WHERE symbol = ? ORDER BY snapshot_date ASC", (symbol,)
).fetchall()

if not rows:
    st.info("Todavía no hay historial para este símbolo. Corré el análisis desde la página principal.")
else:
    df = pd.DataFrame([dict(r) for r in rows])
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.5, 0.25, 0.25], vertical_spacing=0.05)
    fig.add_trace(go.Scatter(x=df["snapshot_date"], y=df["price"], name="Precio"), row=1, col=1)
    if df["sma_20"].notna().any():
        fig.add_trace(go.Scatter(x=df["snapshot_date"], y=df["sma_20"], name="SMA20"), row=1, col=1)
    if df["sma_50"].notna().any():
        fig.add_trace(go.Scatter(x=df["snapshot_date"], y=df["sma_50"], name="SMA50"), row=1, col=1)

    fig.add_trace(go.Scatter(x=df["snapshot_date"], y=df["iv_rank"], name="IV Rank"), row=2, col=1)
    fig.add_hline(y=50, line_dash="dot", row=2, col=1)

    fig.add_trace(go.Scatter(x=df["snapshot_date"], y=df["rsi_14"], name="RSI"), row=3, col=1)
    fig.add_hline(y=70, line_dash="dot", row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", row=3, col=1)

    fig.update_layout(height=750, title=f"{symbol} — precio, IV Rank y RSI")
    st.plotly_chart(fig, use_container_width=True)

    latest = df.iloc[-1]
    st.subheader("Último snapshot")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("IV Rank", f"{latest['iv_rank']:.1f}" if pd.notna(latest["iv_rank"]) else "N/D", latest["iv_rank_source"])
    c2.metric("RSI 14", f"{latest['rsi_14']:.1f}" if pd.notna(latest["rsi_14"]) else "N/D")
    c3.metric("ATR 14", f"{latest['atr_14']:.2f}" if pd.notna(latest["atr_14"]) else "N/D")
    c4.metric("Sesiones de IV acumuladas", int((df["iv_atm"].notna()).sum()))
