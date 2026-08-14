from __future__ import annotations

import json
from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from options_advisor.alerts.formatting import strategy_label
from options_advisor.config import load_priority_watchlist_symbols
from options_advisor.dashboard.chart_overlays import build_alert_strike_levels, build_simulator_overlay
from options_advisor.dashboard.components import (
    ACCENT,
    BORDER,
    CRITICAL,
    GOOD,
    SURFACE,
    TEXT_PRIMARY,
    cached_intraday_bars,
    get_connection,
    get_symbols,
    icon,
    inject_theme,
    render_header,
    render_notification_bell,
)
from options_advisor.indicators.intraday import compute_vwap
from options_advisor.scheduler.market_calendar import latest_trading_session
from options_advisor.storage import repository as repo

# VWAP como serie derivada distinta de las velas (que ya usan GOOD/CRITICAL para suba/baja) —
# mismo naranja que la línea de indicador overlay en Indicadores (pages/3_indicadores.py).
VWAP_COLOR = "#d95926"
# Mismo violeta que la línea de RSI en Indicadores — un solo color para todos los niveles de
# alerta (candidato u operación real, put o call) en vez de sumar más hues: la identidad de
# cada línea ya la lleva la etiqueta de texto, no hace falta codificarla también en color.
STRIKE_LEVEL_COLOR = "#9085e9"

INTERVAL_OPTIONS = {"1 min": 1, "5 min": 5, "10 min": 10, "15 min": 15, "30 min": 30}

st.set_page_config(page_title="Lokshn · Gráfico", page_icon="🕯️", layout="wide", initial_sidebar_state="expanded")
inject_theme()
render_header(
    icon("bar-chart", size=24, color=ACCENT),
    "Gráfico de velas",
    "Precio intradía + VWAP de la sesión regular (9:30-16:00 ET)",
)

conn = get_connection()
render_notification_bell(conn)

latest_session = latest_trading_session()

# Unión de la watchlist configurada + la watchlist real de thinkorswim + los símbolos que ya
# tienen operaciones reales detectadas — mismo bug ya encontrado y corregido antes en Eventos
# de riesgo (2026-07-27) y en el filtro de Operaciones (2026-07-27): la cuenta real opera
# símbolos (EWY, HOOD, GDX, etc.) que no están en la watchlist corta de 15 del motor de
# sugerencias, así que restringirse a `get_symbols()` sola dejaría a este gráfico sin poder
# mostrar la mayoría de las posiciones reales.
available_symbols = sorted(
    set(get_symbols())
    | set(load_priority_watchlist_symbols())
    | {row["symbol"] for row in repo.get_real_trade_alerts(conn, limit=500)}
)

col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    symbol = st.selectbox("Símbolo", available_symbols)
with col2:
    interval_label = st.selectbox("Intervalo", list(INTERVAL_OPTIONS.keys()), index=1)
    interval_minutes = INTERVAL_OPTIONS[interval_label]
with col3:
    session_date = st.date_input("Sesión", value=latest_session, max_value=latest_session)

# Compartir datos con el Simulador (usuario 2026-08-06): dibujar sobre el gráfico lo que el robot
# usó para abrir la posición de este símbolo — strike, TODOS los soportes, cobertura y 1σ.
_tg1, _tg2 = st.columns(2)
with _tg1:
    show_sim = st.checkbox(
        "📐 Mostrar datos del simulador (strike · soportes · cobertura · 1σ)", value=True,
        help="Si el robot tiene una posición abierta de este símbolo, dibuja el strike elegido, todos los "
             "soportes que usó, la banda de cobertura (colchón hasta el strike) y el movimiento esperado de 1σ.")
with _tg2:
    # Los strikes de alertas (candidatos/reales) pueden ser MUCHÍSIMOS (un Iron Condor tiene ~20
    # patas) y tapaban el gráfico (usuario 2026-08-06). Por eso ahora van APAGADOS por defecto.
    show_alerts = st.checkbox(
        "🎯 Mostrar strikes de alertas (candidatos y operaciones reales)", value=False,
        help="Dibuja los strikes de las alertas/candidatos del símbolo. Puede ser mucho ruido si hay "
             "Iron Condors (muchas patas); por eso viene apagado.")
sim_overlay = None
if show_sim:
    _sim_pos = repo.get_open_simulated_positions(conn, symbol)
    if _sim_pos:
        _dec = repo.get_latest_open_decision_for_symbol(conn, symbol)
        try:
            _ctx = json.loads(_dec["context_json"]) if _dec and _dec["context_json"] else {}
        except (ValueError, TypeError):
            _ctx = {}
        sim_overlay = build_simulator_overlay(_sim_pos[0], _ctx)

bars = cached_intraday_bars(symbol, session_date, interval_minutes)

if not bars:
    st.info(
        f"Sin datos intradía para **{symbol}** en la sesión del {session_date.strftime('%d/%m/%Y')} "
        "— puede ser que ese día no haya sido hábil, o que esté fuera del rango que retiene Schwab "
        "para ese intervalo."
    )
else:
    vwap = compute_vwap(bars)
    # timestamp de IntradayBar es UTC (correcto para guardar/calcular) — para el eje del
    # gráfico se muestra en ET, la referencia horaria que espera cualquier trader de acciones
    # de EE.UU. (Plotly/pandas grafican tz-aware tal cual, sin convertir).
    timestamps_et = [b.timestamp.astimezone(ZoneInfo("America/New_York")) for b in bars]
    df = pd.DataFrame(
        {
            "timestamp": timestamps_et,
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
            "vwap": vwap,
        }
    )

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03)
    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name=symbol,
            increasing_line_color=GOOD,
            decreasing_line_color=CRITICAL,
            increasing_fillcolor=GOOD,
            decreasing_fillcolor=CRITICAL,
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["timestamp"], y=df["vwap"], name="VWAP", line=dict(color=VWAP_COLOR, width=2)),
        row=1,
        col=1,
    )

    # Niveles de strike de alertas activas del símbolo (pedido 2026-07-31, "conectar el
    # gráfico con alertas") — candidatos sugeridos y operaciones reales abiertas, para ver el
    # precio en contexto de la posición.
    if show_alerts:
        today = date.today()
        candidate_rows = repo.get_active_candidate_alerts_with_legs(conn, symbol, today)
        real_trade_rows = repo.get_real_trade_alerts(conn, symbol)
        strike_levels = build_alert_strike_levels(candidate_rows, real_trade_rows, as_of=today)
        for level in strike_levels:
            # Leg.side es "sell"/"buy" (ver strategy/candidates.py::Leg), no "short"/"long".
            side_es = "vendido" if level.side == "sell" else "comprado"
            label = f"{strategy_label(level.strategy_type)} · {level.option_type.upper()} {side_es} ${level.strike:.2f} ({level.source})"
            # Etiqueta DENTRO del área del gráfico (no "right", que la posiciona fuera del área de
            # ploteo y queda cortada por el contenedor responsive de use_container_width) — con
            # fondo semitransparente para que se lea encima de las velas.
            fig.add_hline(
                y=level.strike,
                row=1,
                col=1,
                line_dash="dot",
                line_color=STRIKE_LEVEL_COLOR,
                annotation_text=label,
                annotation_position="top left",
                annotation_font_color=STRIKE_LEVEL_COLOR,
                annotation_font_size=10,
                annotation_bgcolor="rgba(22,22,21,0.85)",
            )
    # Overlay del SIMULADOR (usuario 2026-08-06): dibuja lo que el robot usó para abrir la posición
    # de este símbolo, para que Gráfico y Simulador compartan datos — cobertura (colchón), 1σ,
    # todos los soportes y el strike elegido.
    if sim_overlay:
        ov = sim_overlay
        # Cobertura + strike JUNTOS (usuario 2026-08-06): banda sombreada entre el strike y el
        # subyacente (el colchón) — la etiqueta va sobre la línea del strike, abajo, para que se lean
        # como una sola cosa.
        if ov.strike and ov.underlying and ov.underlying > ov.strike:
            fig.add_hrect(y0=ov.strike, y1=ov.underlying, row=1, col=1,
                          fillcolor="rgba(56,189,248,0.12)", line_width=0)
        # 1σ: banda del movimiento esperado alrededor del subyacente (± sigma).
        if ov.underlying and ov.sigma_move:
            fig.add_hrect(y0=ov.underlying - ov.sigma_move, y1=ov.underlying + ov.sigma_move, row=1, col=1,
                          fillcolor="rgba(148,133,233,0.07)", line_width=0,
                          annotation_text=f"±1σ (${ov.sigma_move:.2f})", annotation_position="bottom right",
                          annotation_font_size=10, annotation_font_color="#9085e9")
            for _y in (ov.underlying - ov.sigma_move, ov.underlying + ov.sigma_move):
                fig.add_hline(y=_y, row=1, col=1, line_dash="dot", line_color="#9085e9", line_width=1)
        # Soportes: TODOS los que usó (el fuerte, más grueso y con estrella).
        for s in ov.supports:
            is_strong = ov.strong_support is not None and abs(s - ov.strong_support) < 1e-6
            fig.add_hline(y=s, row=1, col=1, line_dash="dash", line_color="#3fb950",
                          line_width=2.5 if is_strong else 1,
                          annotation_text=(f"★ Sop. fuerte ${s:.2f}" if is_strong else f"Sop. ${s:.2f}"),
                          annotation_position="top left", annotation_font_size=10,
                          annotation_font_color="#3fb950", annotation_bgcolor="rgba(22,22,21,0.85)")
        # Strike elegido por el simulador (línea sólida amarilla) — con la cobertura JUNTO al strike.
        if ov.strike:
            cov_lbl = f"  ·  cobertura {ov.coverage_pct * 100:.1f}%" if ov.coverage_pct else ""
            fig.add_hline(y=ov.strike, row=1, col=1, line_dash="solid", line_color="#f0b429", line_width=2.5,
                          annotation_text=f"◆ Strike ${ov.strike:.2f}{cov_lbl}",
                          annotation_position="bottom right", annotation_font_size=11,
                          annotation_font_color="#f0b429", annotation_bgcolor="rgba(22,22,21,0.9)")

    # Volumen coloreado por la misma dirección que su propia vela (up/down), no una escala
    # aparte — evita sumar una tercera leyenda para una barra que ya se lee por su vecina.
    volume_colors = [GOOD if c >= o else CRITICAL for o, c in zip(df["open"], df["close"])]
    fig.add_trace(
        go.Bar(x=df["timestamp"], y=df["volume"], name="Volumen", marker_color=volume_colors, showlegend=False),
        row=2,
        col=1,
    )

    fig.update_layout(
        height=650,
        title=f"{symbol} — {session_date.strftime('%d/%m/%Y')} ({interval_label})",
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(color=TEXT_PRIMARY, family="system-ui, -apple-system, 'Segoe UI', sans-serif"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, bgcolor="rgba(0,0,0,0)"),
        margin=dict(t=60, b=20),
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(gridcolor=BORDER, zerolinecolor=BORDER)
    fig.update_yaxes(gridcolor=BORDER, zerolinecolor=BORDER)
    fig.update_xaxes(title_text="Hora (ET)", tickformat="%H:%M", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)

    latest = df.iloc[-1]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Último", f"${latest['close']:.2f}")
    c2.metric("VWAP", f"${latest['vwap']:.2f}" if pd.notna(latest["vwap"]) else "N/D")
    c3.metric("Máximo de la sesión", f"${df['high'].max():.2f}")
    c4.metric("Mínimo de la sesión", f"${df['low'].min():.2f}")

    # Resumen numérico de lo que dibujó el simulador (que Gráfico y Simulador coincidan a la vista).
    if sim_overlay:
        ov = sim_overlay
        parts = []
        if ov.strike:
            parts.append(f"🟡 **Strike** ${ov.strike:.2f}")
        if ov.coverage_pct:
            parts.append(f"🔵 **Cobertura** {ov.coverage_pct * 100:.1f}%")
        if ov.sigma_move:
            parts.append(f"🟣 **1σ** ±${ov.sigma_move:.2f}")
        if ov.supports:
            parts.append("🟢 **Soportes** " + ", ".join(f"${s:.2f}" for s in ov.supports))
        st.caption("📐 Datos del simulador (posición abierta de este símbolo) dibujados arriba: " + " · ".join(parts))
    elif show_sim:
        st.caption("📐 El simulador no tiene una posición abierta de este símbolo para dibujar.")
