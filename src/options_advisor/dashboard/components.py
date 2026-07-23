from __future__ import annotations

import json
import math
import os
import sqlite3

import streamlit as st
from dotenv import load_dotenv

from options_advisor.alerts.formatting import strategy_label
from options_advisor.broker import get_broker_client
from options_advisor.broker.base import BrokerClient
from options_advisor.config import PROJECT_ROOT, Settings, load_settings, load_symbols
from options_advisor.dashboard.portfolio_summary import summarize_portfolio
from options_advisor.storage import db
from options_advisor.storage import repository as repo

load_dotenv(PROJECT_ROOT / ".env")

# Paleta oscura premium (Sección "estilo oscuro elegante"). Mismos valores que
# .streamlit/config.toml — repetidos acá porque el theme.* de config.toml no es legible
# desde Python en runtime, y estos componentes HTML necesitan los hex directamente.
SURFACE = "#161615"
SURFACE_RAISED = "#1e1e1d"
PAGE_PLANE = "#0d0d0d"
BORDER = "rgba(255,255,255,0.10)"
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#c3c2b7"
TEXT_MUTED = "#898781"
ACCENT = "#3987e5"
GOOD = "#0ca30c"
WARNING = "#fab219"
CRITICAL = "#d03b3b"


@st.cache_resource
def get_settings() -> Settings:
    return load_settings()


@st.cache_resource
def get_symbols() -> list[str]:
    return load_symbols()


@st.cache_resource
def get_connection() -> sqlite3.Connection:
    settings = get_settings()
    return db.connect(settings.database.resolved_path())


@st.cache_resource
def get_broker() -> BrokerClient:
    return get_broker_client(get_settings())


def get_anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def inject_theme() -> None:
    """CSS compartido por las 5 páginas del dashboard: tipografía, tarjetas, separadores y
    limpieza del chrome por defecto de Streamlit, para un look oscuro consistente."""
    st.markdown(
        f"""
        <style>
        #MainMenu, footer, [data-testid="stToolbar"] {{ visibility: hidden; height: 0; }}

        html, body, [class*="css"] {{
            -webkit-font-smoothing: antialiased;
        }}

        [data-testid="stAppViewContainer"] {{
            background: radial-gradient(120% 120% at 50% -10%, #17181c 0%, {PAGE_PLANE} 55%);
        }}

        h1, h2, h3 {{
            letter-spacing: -0.01em;
            font-weight: 700;
        }}

        [data-testid="stMetric"] {{
            background: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 0.75rem;
            padding: 0.9rem 1.1rem;
        }}
        [data-testid="stMetricLabel"] {{ color: {TEXT_MUTED}; }}

        .oia-divider {{
            height: 1px;
            border: none;
            margin: 1.75rem 0;
            background: linear-gradient(90deg, transparent, {BORDER} 20%, {BORDER} 80%, transparent);
        }}

        .oia-card {{
            background: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 1rem;
            padding: 1.25rem 1.4rem;
            margin-bottom: 1rem;
            box-shadow: 0 8px 24px rgba(0,0,0,0.28);
        }}

        .oia-pill {{
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            padding: 0.15rem 0.65rem;
            border-radius: 999px;
            font-size: 0.85rem;
            font-weight: 600;
            border: 1px solid transparent;
        }}

        .oia-leg-row {{
            display: flex;
            justify-content: space-between;
            gap: 0.75rem;
            padding: 0.4rem 0;
            border-bottom: 1px dashed {BORDER};
            font-size: 0.92rem;
            color: {TEXT_SECONDARY};
        }}
        .oia-leg-row:last-child {{ border-bottom: none; }}

        .oia-metric-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 0.6rem;
            margin: 0.9rem 0;
        }}
        .oia-metric-tile {{
            background: {SURFACE_RAISED};
            border: 1px solid {BORDER};
            border-radius: 0.65rem;
            padding: 0.55rem 0.8rem;
        }}
        .oia-metric-tile .label {{ color: {TEXT_MUTED}; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; }}
        .oia-metric-tile .value {{ color: {TEXT_PRIMARY}; font-size: 1.05rem; font-weight: 700; margin-top: 0.15rem; }}

        .oia-comment {{
            color: {TEXT_SECONDARY};
            font-size: 0.95rem;
            line-height: 1.5;
            margin-top: 0.6rem;
        }}

        .oia-caveat {{
            color: {WARNING};
            font-size: 0.82rem;
            margin-top: 0.3rem;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(icon: str, title: str, subtitle: str | None = None) -> None:
    st.markdown(f"## {icon} {title}")
    if subtitle:
        st.markdown(f"<div style='color:{TEXT_MUTED}; margin-top:-0.6rem;'>{subtitle}</div>", unsafe_allow_html=True)


def score_pill_html(score: int) -> str:
    if score >= 80:
        color = GOOD
    elif score >= 65:
        color = WARNING
    else:
        color = CRITICAL
    return f"<span class='oia-pill' style='color:{color}; border-color:{color}44; background:{color}1a;'>● {score}</span>"


_RISK_LEVEL_COLORS = {"alto": CRITICAL, "medio": WARNING, "bajo": GOOD}
_RISK_LEVEL_LABELS = {"alto": "🔴 Alto", "medio": "🟡 Medio", "bajo": "🟢 Bajo"}


def risk_level_pill_html(level: str) -> str:
    color = _RISK_LEVEL_COLORS.get(level, TEXT_MUTED)
    label = _RISK_LEVEL_LABELS.get(level, level)
    return f"<span class='oia-pill' style='color:{color}; border-color:{color}44; background:{color}1a;'>{label}</span>"


def risk_badge(score: int) -> str:
    if score >= 80:
        return f"🟢 {score}"
    if score >= 65:
        return f"🟡 {score}"
    return f"🔴 {score}"


def _leg_row_html(leg: dict) -> str:
    is_sell = leg["side"] == "sell"
    side_color = CRITICAL if is_sell else GOOD
    side_label = "🔴 Venta" if is_sell else "🟢 Compra"
    option_label = "Put" if leg["option_type"] == "put" else "Call"
    quantity = leg.get("quantity", 1)
    return (
        "<div class='oia-leg-row'>"
        f"<span style='color:{side_color}; font-weight:600;'>{side_label} · {quantity} {option_label}</span>"
        f"<span>Strike ${leg['strike']:.2f} · Vence {leg['expiration']} · Prima ${leg['premium']:.2f}</span>"
        "</div>"
    )


def _fmt_pct(value) -> str:
    if value is None:
        return "N/D"
    return f"{value * 100:.0f}%"


def _fmt_money(value) -> str:
    if value is None:
        return "N/D"
    if math.isinf(value):
        return "Ilimitado"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _earnings_caveat_html(next_earnings_date: str | None, expiration_date: str | None) -> str:
    if not next_earnings_date:
        return f"<div class='oia-caveat'>⚠️ No se pudo verificar la fecha de earnings — confirmá manualmente antes de operar.</div>"
    if expiration_date and next_earnings_date <= expiration_date:
        return f"<div class='oia-caveat' style='color:{CRITICAL};'>🚨 Earnings el {next_earnings_date} — CAE DENTRO del vencimiento de esta posición, riesgo de gap.</div>"
    return f"<div style='color:{GOOD}; font-size:0.82rem; margin-top:0.3rem;'>✅ Sin earnings antes del vencimiento (próximo: {next_earnings_date}).</div>"


def _fed_event_caveat_html(fed_meeting_date: str | None, expiration_date: str | None) -> str | None:
    if not fed_meeting_date or not expiration_date or fed_meeting_date > expiration_date:
        return None
    return (
        f"<div class='oia-caveat' style='color:{CRITICAL};'>🚨 Reunión FOMC el {fed_meeting_date} — "
        "CAE DENTRO del vencimiento de esta posición, riesgo de gap por decisión de tasas.</div>"
    )


def render_alert_card(
    alert: sqlite3.Row,
    candidate: sqlite3.Row | None,
    next_earnings_date: str | None = None,
    fed_meeting_date: str | None = None,
) -> None:
    """Tarjeta premium de una alerta: patas, prima, beneficio/pérdida máxima, breakevens,
    probabilidad de beneficio y el comentario del narrador — mismos datos que el bloque de
    texto que arma `alerts/formatting.py` para las notificaciones, con estructura HTML."""
    strategy_type = candidate["strategy_type"] if candidate else None
    label = strategy_label(strategy_type) if strategy_type else "Estrategia desconocida"
    expiration_date = candidate["expiration_date"] if candidate else None

    legs = json.loads(candidate["legs_json"]) if candidate and candidate["legs_json"] else []
    breakevens = json.loads(candidate["breakevens_json"]) if candidate and candidate["breakevens_json"] else []
    underlying_price = candidate["underlying_price"] if candidate else None
    is_estimate = bool(candidate["payoff_is_estimate"]) if candidate else False

    narrative = alert["narrative_text"] or ""
    if "💡 Comentario:" in narrative:
        comment = narrative.split("💡 Comentario:", 1)[1].strip()
    else:
        comment = narrative or "Sin comentario disponible."

    html = ["<div class='oia-card'>"]
    html.append(
        "<div style='display:flex; justify-content:space-between; align-items:flex-start; gap:1rem;'>"
        f"<div><div style='font-size:1.3rem; font-weight:700;'>📌 {alert['symbol']} — {label}</div>"
        f"<div style='color:{TEXT_MUTED}; font-size:0.85rem; margin-top:0.15rem;'>"
        f"{alert['alert_date']} · perfil {alert['risk_profile']} · umbral {alert['threshold_applied']}</div></div>"
        f"<div>{score_pill_html(alert['conviction_score'])}</div>"
        "</div>"
    )
    if underlying_price is not None:
        html.append(f"<div style='margin-top:0.5rem; color:{TEXT_SECONDARY};'>💲 Precio actual del subyacente: ${underlying_price:,.2f}</div>")
    html.append(_earnings_caveat_html(next_earnings_date, expiration_date))
    fed_caveat = _fed_event_caveat_html(fed_meeting_date, expiration_date)
    if fed_caveat:
        html.append(fed_caveat)

    if legs:
        html.append("<div style='margin-top:0.8rem;'>")
        html.extend(_leg_row_html(leg) for leg in legs)
        if strategy_type == "covered_call":
            html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.85rem; margin-top:0.4rem;'>📎 Requiere 100 acciones de {alert['symbol']} en cartera (o asignación previa).</div>")
        html.append("</div>")

        net_premium = candidate["net_premium"]
        premium_kind = "crédito" if (net_premium or 0) >= 0 else "débito"
        breakevens_str = " / ".join(f"${b:,.2f}" for b in breakevens) if breakevens else "N/D"
        pop = candidate["probability_of_profit"]
        pop_str = f"{pop * 100:.0f}%" if pop is not None else "N/D"

        html.append("<div class='oia-metric-grid'>")
        html.append(f"<div class='oia-metric-tile'><div class='label'>💵 Prima neta</div><div class='value'>{_fmt_money(abs(net_premium) if net_premium is not None else None)} <span style='font-size:0.7rem; color:{TEXT_MUTED};'>({premium_kind})</span></div></div>")
        html.append(f"<div class='oia-metric-tile'><div class='label'>🏆 Beneficio máximo</div><div class='value'>{_fmt_money(candidate['max_profit'])}</div></div>")
        html.append(f"<div class='oia-metric-tile'><div class='label'>📉 Pérdida máxima</div><div class='value'>{_fmt_money(candidate['max_loss'])}</div></div>")
        html.append(f"<div class='oia-metric-tile'><div class='label'>⚖️ Breakeven(s)</div><div class='value' style='font-size:0.9rem;'>{breakevens_str}</div></div>")
        html.append(f"<div class='oia-metric-tile'><div class='label'>📊 Prob. de beneficio</div><div class='value'>{pop_str}</div></div>")
        html.append(f"<div class='oia-metric-tile'><div class='label'>⏳ DTE</div><div class='value'>{candidate['dte'] if candidate['dte'] is not None else 'N/D'} días</div></div>")
        html.append("</div>")

        if is_estimate:
            html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.8rem;'>ℹ️ Beneficio máximo, pérdida máxima y breakeven(s) son una estimación por modelo (vencimientos combinados).</div>")
    elif candidate:
        strikes = json.loads(candidate["strikes_json"])
        html.append(f"<div style='margin-top:0.6rem; color:{TEXT_SECONDARY};'>Strikes: {strikes}</div>")
        html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.8rem; margin-top:0.3rem;'>Alerta generada antes de esta actualización — sin datos de P&L calculados.</div>")

    html.append(f"<div class='oia-comment'>💡 <b>Comentario:</b> {comment}</div>")
    html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.75rem; margin-top:0.6rem;'>fuente de la narración: {alert['narrative_source']}</div>")
    html.append("</div>")

    st.markdown("".join(html), unsafe_allow_html=True)


def render_news_card(item: sqlite3.Row) -> None:
    """Tarjeta compacta de una noticia (Finnhub /company-news): headline enlazado, fuente y
    fecha de publicación, resumen corto — mismo lenguaje visual que las tarjetas de alerta."""
    published = item["published_at"][:10] if item["published_at"] else "fecha N/D"
    source = item["source"] or "fuente N/D"
    headline = item["headline"]
    url = item["url"]
    summary = item["summary"] or ""

    html = ["<div class='oia-card'>"]
    html.append(
        "<div style='display:flex; justify-content:space-between; align-items:baseline; gap:1rem;'>"
        f"<a href='{url}' target='_blank' style='color:{TEXT_PRIMARY}; font-size:1.05rem; font-weight:700; text-decoration:none;'>📰 {headline}</a>"
        "</div>"
    )
    html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.8rem; margin-top:0.25rem;'>{source} · {published}</div>")
    if summary:
        html.append(f"<div class='oia-comment'>{summary}</div>")
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def render_macro_panel(conn: sqlite3.Connection) -> None:
    """Contexto macro (Fed, CPI/empleo/PBI, próximos eventos): un dato por día, no por
    símbolo — probabilidad de decisión de la Fed calculada a partir de precios reales de
    mercado (Kalshi), nunca una especulación del narrador de IA."""
    snap = repo.get_latest_macro_snapshot(conn)
    if snap is None:
        st.info("Todavía no hay contexto macro. Corré el análisis para traerlo (Finnhub/FRED/Kalshi).", icon="🏦")
        return

    html = ["<div class='oia-card'>", "<div style='font-size:1.1rem; font-weight:700;'>🏦 Contexto macro</div>"]
    html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.8rem; margin-top:0.1rem;'>Actualizado {snap['snapshot_date']}</div>")

    if snap["fed_funds_upper"] is not None:
        html.append(
            f"<div style='margin-top:0.6rem; color:{TEXT_SECONDARY};'>Tasa de fondos federales vigente: "
            f"<b>{snap['fed_funds_lower']:.2f}% – {snap['fed_funds_upper']:.2f}%</b></div>"
        )

    if snap["fed_meeting_date"] is not None:
        html.append(f"<div style='color:{TEXT_SECONDARY}; margin-top:0.3rem;'>Próxima reunión FOMC: <b>{snap['fed_meeting_date']}</b></div>")
        html.append("<div class='oia-metric-grid'>")
        html.append(f"<div class='oia-metric-tile'><div class='label'>📈 Sube</div><div class='value'>{_fmt_pct(snap['fed_hike_probability'])}</div></div>")
        html.append(f"<div class='oia-metric-tile'><div class='label'>➡️ Mantiene</div><div class='value'>{_fmt_pct(snap['fed_hold_probability'])}</div></div>")
        html.append(f"<div class='oia-metric-tile'><div class='label'>📉 Baja</div><div class='value'>{_fmt_pct(snap['fed_cut_probability'])}</div></div>")
        html.append("</div>")
        html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.75rem;'>Probabilidad calculada a partir de precios reales de mercado (contratos Kalshi sobre la tasa de la Fed) — nunca una estimación de la IA.</div>")
    else:
        html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.85rem; margin-top:0.3rem;'>Probabilidad de la próxima decisión de la Fed no disponible (Kalshi).</div>")

    macro_bits = []
    if snap["cpi_yoy_pct"] is not None:
        macro_bits.append(f"CPI interanual: <b>{snap['cpi_yoy_pct']:.1f}%</b>")
    if snap["unemployment_rate_pct"] is not None:
        macro_bits.append(f"Desempleo: <b>{snap['unemployment_rate_pct']:.1f}%</b>")
    if snap["gdp_growth_annualized_pct"] is not None:
        macro_bits.append(f"PBI (crec. anualizado): <b>{snap['gdp_growth_annualized_pct']:.1f}%</b>")
    if macro_bits:
        html.append(f"<div style='margin-top:0.6rem; color:{TEXT_SECONDARY};'>{' · '.join(macro_bits)}</div>")

    events = json.loads(snap["upcoming_events_json"]) if snap["upcoming_events_json"] else []
    if events:
        html.append(f"<div style='margin-top:0.6rem; color:{TEXT_MUTED}; font-size:0.8rem; text-transform:uppercase; letter-spacing:0.04em;'>Próximos eventos</div>")
        for event in events[:6]:
            html.append(f"<div class='oia-leg-row'><span>{event.get('event', '')}</span><span>{event.get('date', '')}</span></div>")

    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def render_portfolio_summary_panel(conn: sqlite3.Connection, alert_date) -> None:
    """Estado agregado de las alertas de un día: cuántas hay, distribución por estrategia,
    exposición direccional (a partir del delta neto que ya calcula strategy/candidates.py) y
    riesgo total si todas se ejecutaran (Sección 'resumen de portafolio', punto #2)."""
    rows = [dict(r) for r in repo.get_alerts_for_date(conn, alert_date)]
    summary = summarize_portfolio(rows)

    html = ["<div class='oia-card'>", "<div style='font-size:1.1rem; font-weight:700;'>📋 Resumen de oportunidades de hoy</div>"]

    if summary["total_alerts"] == 0:
        html.append(f"<div style='color:{TEXT_MUTED}; margin-top:0.4rem;'>Todavía no hay alertas hoy ({alert_date}).</div>")
        html.append("</div>")
        st.markdown("".join(html), unsafe_allow_html=True)
        return

    html.append("<div class='oia-metric-grid'>")
    html.append(f"<div class='oia-metric-tile'><div class='label'>🔔 Alertas hoy</div><div class='value'>{summary['total_alerts']}</div></div>")

    directional = summary["directional"]
    html.append(f"<div class='oia-metric-tile'><div class='label'>📈 Alcistas</div><div class='value'>{directional['bullish']}</div></div>")
    html.append(f"<div class='oia-metric-tile'><div class='label'>📉 Bajistas</div><div class='value'>{directional['bearish']}</div></div>")
    html.append(f"<div class='oia-metric-tile'><div class='label'>➡️ Neutrales</div><div class='value'>{directional['neutral']}</div></div>")

    net_delta = summary["net_delta"]
    net_delta_str = f"{net_delta:+.2f}" if net_delta is not None else "N/D"
    html.append(f"<div class='oia-metric-tile'><div class='label'>⚖️ Delta neto total</div><div class='value'>{net_delta_str}</div></div>")

    bounded_risk = summary["bounded_risk_total"]
    risk_str = _fmt_money(bounded_risk) if bounded_risk is not None else "N/D"
    html.append(f"<div class='oia-metric-tile'><div class='label'>📉 Riesgo total (acotado)</div><div class='value'>{risk_str}</div></div>")
    html.append("</div>")

    if summary["unbounded_risk_count"] > 0:
        html.append(
            f"<div class='oia-caveat'>⚠️ {summary['unbounded_risk_count']} estrategia(s) de riesgo no acotado (short naked) "
            "entre las alertas de hoy — no están incluidas en el riesgo total de arriba, podrían perder más.</div>"
        )

    strategy_bits = " · ".join(f"{strategy_label(s)}: {n}" for s, n in sorted(summary["by_strategy"].items(), key=lambda kv: -kv[1]))
    html.append(f"<div style='margin-top:0.6rem; color:{TEXT_SECONDARY}; font-size:0.85rem;'>Por estrategia: {strategy_bits}</div>")

    html.append(
        f"<div style='color:{TEXT_MUTED}; font-size:0.75rem; margin-top:0.5rem;'>Delta neto total y clasificación alcista/bajista/neutral "
        "vienen del delta ya calculado por candidato (no una nueva estimación); banda neutral: |delta| ≤ 0.05.</div>"
    )

    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)
