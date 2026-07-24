from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from options_advisor.dashboard.components import (
    ACCENT,
    BORDER,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    get_connection,
    get_symbols,
    icon,
    inject_theme,
    render_header,
    render_notification_bell,
)

# Paleta categórica oscura (mismo orden que .streamlit/config.toml chartCategoricalColors),
# asignada por identidad de serie, nunca por rango — ver skill de dataviz.
SERIES_BLUE = "#3987e5"
SERIES_ORANGE = "#d95926"
SERIES_AQUA = "#199e70"
SERIES_VIOLET = "#9085e9"

st.set_page_config(page_title="Indicadores", page_icon="📊", layout="wide")
inject_theme()
render_header(icon("bar-chart", size=24, color=ACCENT), "Detalle de indicadores por símbolo")

conn = get_connection()
render_notification_bell(conn)
symbol = st.selectbox("Símbolo", get_symbols())

rows = conn.execute(
    "SELECT * FROM indicator_snapshots WHERE symbol = ? ORDER BY snapshot_date ASC", (symbol,)
).fetchall()

if not rows:
    st.info("Todavía no hay historial para este símbolo. Corré el análisis desde la página principal.")
else:
    df = pd.DataFrame([dict(r) for r in rows])
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    has_std = df["price_std_20"].notna().any() and df["sma_20"].notna().any()
    if has_std:
        df["band_1_upper"] = df["sma_20"] + df["price_std_20"]
        df["band_1_lower"] = df["sma_20"] - df["price_std_20"]
        df["band_2_upper"] = df["sma_20"] + 2 * df["price_std_20"]
        df["band_2_lower"] = df["sma_20"] - 2 * df["price_std_20"]

    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.4, 0.2, 0.2, 0.2], vertical_spacing=0.04)
    fig.add_trace(go.Scatter(x=df["snapshot_date"], y=df["price"], name="Precio", line=dict(color=SERIES_BLUE, width=2)), row=1, col=1)
    if df["sma_20"].notna().any():
        fig.add_trace(go.Scatter(x=df["snapshot_date"], y=df["sma_20"], name="SMA20", line=dict(color=SERIES_ORANGE, width=2)), row=1, col=1)
    if df["sma_50"].notna().any():
        fig.add_trace(go.Scatter(x=df["snapshot_date"], y=df["sma_50"], name="SMA50", line=dict(color=SERIES_AQUA, width=2)), row=1, col=1)
    if has_std:
        for col, label, dash in (("band_1_upper", "+1σ", "dot"), ("band_1_lower", "-1σ", "dot"), ("band_2_upper", "+2σ", "dash"), ("band_2_lower", "-2σ", "dash")):
            fig.add_trace(
                go.Scatter(x=df["snapshot_date"], y=df[col], name=label, line=dict(color=TEXT_MUTED, width=1, dash=dash), showlegend=False),
                row=1, col=1,
            )

    fig.add_trace(go.Scatter(x=df["snapshot_date"], y=df["iv_rank"], name="IV Rank", line=dict(color=SERIES_BLUE, width=2)), row=2, col=1)
    fig.add_hline(y=50, line_dash="dot", line_color=TEXT_MUTED, row=2, col=1)

    fig.add_trace(go.Scatter(x=df["snapshot_date"], y=df["rsi_14"], name="RSI", line=dict(color=SERIES_VIOLET, width=2)), row=3, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color=TEXT_MUTED, row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color=TEXT_MUTED, row=3, col=1)

    fig.add_trace(go.Scatter(x=df["snapshot_date"], y=df["net_gex"], name="Net GEX", line=dict(color=SERIES_ORANGE, width=2)), row=4, col=1)
    fig.add_hline(y=0, line_dash="dot", line_color=TEXT_MUTED, row=4, col=1)

    fig.update_layout(
        height=900,
        title=f"{symbol} — precio (con bandas 1σ/2σ), IV Rank, RSI y Net GEX",
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(color=TEXT_PRIMARY, family="system-ui, -apple-system, 'Segoe UI', sans-serif"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, bgcolor="rgba(0,0,0,0)"),
        margin=dict(t=60, b=20),
    )
    fig.update_xaxes(gridcolor=BORDER, zerolinecolor=BORDER)
    fig.update_yaxes(gridcolor=BORDER, zerolinecolor=BORDER)
    st.plotly_chart(fig, use_container_width=True)

    latest = df.iloc[-1]
    st.subheader("Último snapshot")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("IV Rank", f"{latest['iv_rank']:.1f}" if pd.notna(latest["iv_rank"]) else "N/D", latest["iv_rank_source"])
    c2.metric("RSI 14", f"{latest['rsi_14']:.1f}" if pd.notna(latest["rsi_14"]) else "N/D")
    c3.metric("ATR 14", f"{latest['atr_14']:.2f}" if pd.notna(latest["atr_14"]) else "N/D")
    c4.metric("Std Dev (20d)", f"{latest['price_std_20']:.2f}" if pd.notna(latest["price_std_20"]) else "N/D")
    c5.metric("Net GEX", f"{latest['net_gex']:.2e}" if pd.notna(latest["net_gex"]) else "N/D")
    c6.metric("Sesiones de IV acumuladas", int((df["iv_atm"].notna()).sum()))
    if pd.notna(latest.get("next_earnings_date")):
        st.caption(f"📅 Próxima fecha de earnings conocida: {latest['next_earnings_date']}")
