from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from options_advisor.broker import get_broker_client
from options_advisor.config import load_settings
from options_advisor.dashboard.components import (
    ACCENT,
    BORDER,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    get_connection,
    icon,
    inject_theme,
    render_header,
    render_notification_bell,
)
from options_advisor.dashboard.simulator_table import (
    build_closed_position_rows,
    build_equity_curve_rows,
    build_open_position_rows,
)
from options_advisor.storage import repository as repo

st.set_page_config(page_title="Simulador", page_icon="🧪", layout="wide")
inject_theme()
render_header(
    icon("trending-up", size=24, color=ACCENT),
    "Simulador de Trading Automático",
    "Cuenta simulada de $100,000 con datos REALES de mercado — nunca opera con dinero real. "
    "Evalúa la watchlist todos los días según 8 criterios estrictos y abre/cierra Naked Put "
    "automáticamente, cerrando al 30% de ganancia sobre la prima cobrada.",
)

conn = get_connection()
render_notification_bell(conn)

settings = load_settings()
account = repo.get_simulated_account(conn)
if account is None:
    st.info(
        "El simulador todavía no corrió ninguna vez — se inicializa automáticamente en la "
        "próxima corrida del scheduler (o desde 'Correr análisis ahora' en la página General)."
    )
    st.stop()

open_rows = repo.get_open_simulated_positions(conn)
closed_rows = repo.get_closed_simulated_positions(conn)
equity_history = repo.get_simulated_equity_history(conn)
stats = repo.get_simulated_performance_stats(conn)

committed = sum(r["collateral"] for r in open_rows)
last_marked_unrealized = sum(r["last_unrealized_pnl"] or 0.0 for r in open_rows)
equity = account["cash"] + committed + last_marked_unrealized
total_return_pct = round((equity - settings.simulator.initial_capital) / settings.simulator.initial_capital * 100, 2)

dollar_col1, dollar_col2, dollar_col3 = st.columns(3)
dollar_col1.metric("Equity total", f"${equity:,.2f}", f"{total_return_pct:+.2f}%")
dollar_col2.metric("Cash disponible", f"${account['cash']:,.2f}")
dollar_col3.metric("P&L realizado total", f"${stats['total_realized_pnl']:,.2f}")

count_col1, count_col2 = st.columns(2)
count_col1.metric("Posiciones abiertas", len(open_rows))
count_col2.metric("Win rate", f"{stats['win_rate_pct']:.1f}%" if stats["win_rate_pct"] is not None else "N/D")

st.subheader("Curva de equity")
if equity_history:
    df = pd.DataFrame(build_equity_curve_rows(equity_history))
    df["Fecha"] = pd.to_datetime(df["Fecha"])
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["Fecha"], y=df["Equity"], name="Equity", mode="lines",
            line=dict(color=ACCENT, width=2), fill="tozeroy", fillcolor="rgba(57,135,229,0.08)",
        )
    )
    fig.add_hline(
        y=settings.simulator.initial_capital, line_dash="dot", line_color=TEXT_MUTED,
        annotation_text="Capital inicial", annotation_position="top left",
    )
    fig.update_layout(
        height=350,
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(color=TEXT_PRIMARY, family="system-ui, -apple-system, 'Segoe UI', sans-serif"),
        margin=dict(t=20, b=20),
        showlegend=False,
    )
    fig.update_xaxes(gridcolor=BORDER, zerolinecolor=BORDER)
    fig.update_yaxes(gridcolor=BORDER, zerolinecolor=BORDER, tickprefix="$")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.caption("Todavía no hay historial de equity — se registra en la primera corrida del scheduler.")

st.subheader("Posiciones abiertas")
if open_rows:
    # P&L EN VIVO (pedido explícito, no solo el último marcado del cron): se pide precio/cadena
    # una sola vez por símbolo distinto entre las posiciones abiertas, nunca se escribe nada acá
    # — mirar el dashboard nunca debe cerrar una posición, eso es exclusivo del scheduler.
    broker = get_broker_client(settings)
    live_data: dict = {}
    for row in open_rows:
        symbol = row["symbol"]
        if symbol in live_data:
            continue
        try:
            quote = broker.get_quote(symbol)
            chain = broker.get_option_chain(symbol, expiration_range_days=(1, 60))
            live_data[symbol] = (quote.last_price, chain)
        except Exception:
            live_data[symbol] = (None, None)

    open_table = build_open_position_rows(open_rows, live_data)
    st.dataframe(
        pd.DataFrame(open_table),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Strike": st.column_config.NumberColumn(format="$%.2f"),
            "Prima cobrada": st.column_config.NumberColumn(format="$%.2f"),
            "Valor actual": st.column_config.NumberColumn(format="$%.2f"),
            "P&L no realizado": st.column_config.NumberColumn(format="$%.2f"),
            "% s/prima": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )
else:
    st.caption("Sin posiciones abiertas.")

st.subheader("Posiciones cerradas")
if closed_rows:
    closed_table = build_closed_position_rows(closed_rows)
    st.dataframe(
        pd.DataFrame(closed_table),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Strike": st.column_config.NumberColumn(format="$%.2f"),
            "Prima cobrada": st.column_config.NumberColumn(format="$%.2f"),
            "Prima de cierre": st.column_config.NumberColumn(format="$%.2f"),
            "P&L realizado": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    stat_col1, stat_col2 = st.columns(2)
    stat_col1.metric("Ganancia promedio", f"${stats['avg_win']:,.2f}" if stats["avg_win"] is not None else "N/D")
    stat_col2.metric("Pérdida promedio", f"${stats['avg_loss']:,.2f}" if stats["avg_loss"] is not None else "N/D")
else:
    st.caption("Todavía no se cerró ninguna posición.")
