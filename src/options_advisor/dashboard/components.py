from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from options_advisor.alerts.formatting import (
    assess_dividend_risk,
    assess_liquidity,
    compute_coverage,
    share_requirement_line,
    strategy_label,
)
from options_advisor.broker import get_broker_client
from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import Mover, Quote
from options_advisor.config import PROJECT_ROOT, Settings, load_settings, load_symbols
from options_advisor.dashboard.portfolio_summary import summarize_portfolio
from options_advisor.scheduler.market_calendar import MarketSession, market_session
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

# Set de íconos outline (trazo 1.75, estilo Lucide) que reemplaza los emojis en todo lo que se
# renderiza como HTML propio (st.markdown con unsafe_allow_html). Los widgets nativos de
# Streamlit (botones, popover de la campanita, dataframes) no aceptan HTML en sus labels —
# ahí se mantiene emoji, es una limitación de la plataforma, no una inconsistencia de diseño.
_ICON_PATHS = {
    "pin": '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    "dollar": '<line x1="12" y1="2" x2="12" y2="22"/><path d="M17 5.5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
    "trending-up": '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
    "trending-down": '<polyline points="22 17 13.5 8.5 8.5 13.5 2 7"/><polyline points="16 17 22 17 22 11"/>',
    "scale": '<line x1="5" y1="9" x2="19" y2="9"/><line x1="5" y1="15" x2="19" y2="15"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/>',
    "alert-triangle": '<path d="M10.3 3.86 2.3 18a1.8 1.8 0 0 0 1.55 2.7h16.3A1.8 1.8 0 0 0 21.7 18l-8-14.14a1.8 1.8 0 0 0-3.4 0Z"/><line x1="12" y1="9.5" x2="12" y2="13.5"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "check-circle": '<path d="M21 10.5V12a9 9 0 1 1-5.34-8.23"/><polyline points="21 4 12 13.01 9 10.01"/>',
    "help-circle": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 4.9.8c0 1.7-2.4 1.9-2.4 3.7"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "info": '<circle cx="12" cy="12" r="9"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    "lightbulb": '<path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a6.5 6.5 0 0 0-4 11.6c.6.5 1 1.2 1 2.4h6c0-1.2.4-1.9 1-2.4A6.5 6.5 0 0 0 12 2Z"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "bell": '<path d="M6 8a6 6 0 0 1 12 0c0 5.5 2 7 2 7H4s2-1.5 2-7"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    "news": '<path d="M4 4h16v14H8l-4 4Z"/><line x1="8" y1="9" x2="16" y2="9"/><line x1="8" y1="13" x2="13" y2="13"/>',
    "zap": '<path d="M13 2 3 14h8l-1 8 10-12h-8l1-8Z"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    "arrow-up": '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
    "arrow-down": '<line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/>',
    "arrow-right": '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
    "briefcase": '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/>',
    "landmark": '<line x1="3" y1="22" x2="21" y2="22"/><line x1="6" y1="18" x2="6" y2="11"/><line x1="10" y1="18" x2="10" y2="11"/><line x1="14" y1="18" x2="14" y2="11"/><line x1="18" y1="18" x2="18" y2="11"/><polygon points="12 2 20 7 4 7"/>',
    "bar-chart": '<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>',
    "clipboard": '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M9 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-3"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
}


def icon(name: str, size: int = 16, color: str = "currentColor", stroke_width: float = 1.75) -> str:
    """SVG inline de un ícono outline — sin librería ni CDN, ~200-400 bytes cada uno. Solo sirve
    dentro de HTML propio (st.markdown con unsafe_allow_html=True); los widgets nativos de
    Streamlit no lo renderizan."""
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{stroke_width}" stroke-linecap="round" stroke-linejoin="round" '
        f'style="vertical-align:-3px; flex-shrink:0;">{_ICON_PATHS[name]}</svg>'
    )


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


@st.cache_data(ttl=60, show_spinner=False)
def cached_quotes(symbols: tuple[str, ...]) -> dict[str, Quote]:
    """Cotizaciones para el carrusel estilo CNBC — cacheadas 60s para no pegarle a la API de
    Schwab en cada rerun de Streamlit (cualquier click o widget dispara uno)."""
    return get_broker().get_quotes(list(symbols))


@st.cache_data(ttl=60, show_spinner=False)
def cached_movers(index: str, sort: str) -> list[Mover]:
    """Top movers para la sección Market Movers — mismo motivo de cache que `cached_quotes`."""
    return get_broker().get_movers(index, sort)


def get_anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def inject_theme() -> None:
    """CSS compartido por las 5 páginas del dashboard: tipografía, tarjetas, separadores y
    limpieza del chrome por defecto de Streamlit, para un look oscuro consistente."""
    st.markdown(
        f"""
        <style>
        /* Ocultamos el menú/footer/toolbar por defecto de Streamlit, pero NO el botón nativo
        de expandir el sidebar (stExpandSidebarButton) — vive dentro de stToolbar, y ocultar
        todo el contenedor con visibility:hidden lo tapaba a él también, dejando el sidebar
        colapsado sin ninguna forma de reabrirlo (bug reportado 2026-07-26). */
        #MainMenu, footer, [data-testid="stToolbar"] {{ visibility: hidden; height: 0; }}
        [data-testid="stExpandSidebarButton"] {{
            visibility: visible !important;
            height: 28px !important;
            overflow: visible !important;
        }}

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

        .oia-session-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
        }}
        .oia-session-dot.oia-pulse {{
            animation: oia-pulse-anim 1.6s ease-in-out infinite;
        }}
        @keyframes oia-pulse-anim {{
            0% {{ box-shadow: 0 0 0 0 rgba(12,163,12,0.55); }}
            70% {{ box-shadow: 0 0 0 6px rgba(12,163,12,0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(12,163,12,0); }}
        }}

        .oia-ticker {{
            overflow: hidden;
            white-space: nowrap;
            border: 1px solid {BORDER};
            border-radius: 0.75rem;
            background: {SURFACE};
            padding: 0.65rem 0;
            margin-bottom: 1rem;
        }}
        .oia-ticker-track {{
            display: inline-block;
            white-space: nowrap;
            animation: oia-ticker-scroll 45s linear infinite;
        }}
        .oia-ticker:hover .oia-ticker-track {{
            animation-play-state: paused;
        }}
        .oia-ticker-item {{
            display: inline-block;
            padding: 0 1.5rem;
            font-size: 0.92rem;
            color: {TEXT_PRIMARY};
            border-right: 1px dashed {BORDER};
        }}
        @keyframes oia-ticker-scroll {{
            from {{ transform: translateX(0); }}
            to {{ transform: translateX(-50%); }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    _render_sidebar_toggle()


def _render_sidebar_toggle() -> None:
    """Ícono ☰ fijo arriba a la izquierda para abrir/cerrar el sidebar de forma confiable
    (bug reportado 2026-07-26: el CSS de arriba oculta [data-testid="stToolbar"] completo
    para limpiar el chrome de Streamlit, y eso se llevaba puesto al botón nativo de expandir
    el sidebar — quedaba colapsado sin ninguna forma de reabrirlo). En vez de depender de que
    ese control nativo siga existiendo con el mismo data-testid en la próxima versión de
    Streamlit, este botón vive fuera del iframe del componente (inyectado en
    window.parent.document) y solo delega el toggle real en los botones nativos de Streamlit
    (stExpandSidebarButton / stSidebarCollapseButton), así seguimos usando su manejo de estado
    interno en vez de mover el sidebar a mano con CSS."""
    components.html(
        """
        <script>
        (function() {
            const doc = window.parent.document;
            if (doc.getElementById('oia-sidebar-toggle')) { return; }

            const style = doc.createElement('style');
            style.textContent = `
                #oia-sidebar-toggle {
                    position: fixed;
                    top: 14px;
                    left: 14px;
                    z-index: 999999;
                    width: 34px;
                    height: 34px;
                    border-radius: 8px;
                    border: 1px solid rgba(255,255,255,0.15);
                    background: #1c1d22;
                    color: #e6e6e6;
                    font-size: 18px;
                    line-height: 1;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    cursor: pointer;
                    user-select: none;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.35);
                }
                #oia-sidebar-toggle:hover { background: #26272e; }
            `;
            doc.head.appendChild(style);

            const btn = doc.createElement('div');
            btn.id = 'oia-sidebar-toggle';
            btn.title = 'Abrir/cerrar menú';
            btn.textContent = '☰';
            function clickable(container) {
                if (!container) { return null; }
                return container.tagName === 'BUTTON' ? container : (container.querySelector('button') || container);
            }
            // stExpandSidebarButton es un <button> directo, pero stSidebarCollapseButton es un
            // <div> que ENVUELVE el <button> real (asimetría del DOM de Streamlit, encontrada
            // 2026-07-27 investigando el bug reportado "no responde al primer clic, solo
            // funciona con refresh completo") — target.click() nativo alcanza igual en ambos
            // casos porque el evento sintético hace bubbling hasta el listener de React en la
            // raíz, PERO el clic REAL del usuario en el ícono ☰ (mousedown/mouseup en vez de
            // solo 'click') no siempre viaja completo por el árbol de eventos de React si el
            // navegador ya movió el foco/hover al mismo tiempo que el rerun de Streamlit
            // reemplaza ese botón interno — disparar la secuencia completa de eventos de puntero
            // (pointerdown/mousedown/pointerup/mouseup/click) en vez de un solo .click() imita
            // un clic real de principio a fin y evita que React lo pierda a mitad de camino.
            function fireClick(target) {
                if (!target) { return; }
                const opts = { bubbles: true, cancelable: true, view: window };
                ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach(function (type) {
                    const EventClass = type.startsWith("pointer") ? PointerEvent : MouseEvent;
                    target.dispatchEvent(new EventClass(type, opts));
                });
            }
            btn.addEventListener('click', function() {
                const sidebar = doc.querySelector('[data-testid="stSidebar"]');
                const expanded = sidebar && sidebar.getAttribute('aria-expanded') === 'true';
                const target = expanded
                    ? clickable(doc.querySelector('[data-testid="stSidebarCollapseButton"]'))
                    : clickable(doc.querySelector('[data-testid="stExpandSidebarButton"]'));
                fireClick(target);
            });
            doc.body.appendChild(btn);
        })();
        </script>
        """,
        height=0,
    )


def render_header(icon_html: str, title: str, subtitle: str | None = None) -> None:
    st.markdown(
        f"<h2 style='display:flex; align-items:center; gap:0.55rem; margin:0;'>{icon_html}<span>{title}</span></h2>",
        unsafe_allow_html=True,
    )
    if subtitle:
        st.markdown(f"<div style='color:{TEXT_MUTED}; margin-top:0.4rem;'>{subtitle}</div>", unsafe_allow_html=True)


_SESSION_LABELS: dict[MarketSession, str] = {
    "abierto": "Mercado abierto",
    "pre-market": "Pre-market",
    "after-hours": "After-hours",
    "cerrado": "Mercado cerrado",
}


def render_market_session_badge() -> None:
    """Indicador de sesión estilo CNBC (punto pulsante verde con el mercado abierto, ámbar en
    pre/after-market por la menor liquidez, gris cuando está cerrado) — Sección 'Rediseño de
    página principal estilo CNBC' 2026-07-26."""
    session = market_session()
    label = _SESSION_LABELS[session]
    if session == "abierto":
        color, pulse_class = GOOD, " oia-pulse"
    elif session in ("pre-market", "after-hours"):
        color, pulse_class = WARNING, ""
    else:
        color, pulse_class = TEXT_MUTED, ""
    dot = f"<span class='oia-session-dot{pulse_class}' style='background:{color};'></span>"
    st.markdown(
        f"<div class='oia-pill' style='color:{color}; border-color:{color}44; background:{color}1a; "
        f"font-size:0.85rem;'>{dot} {label}</div>",
        unsafe_allow_html=True,
    )


def render_quote_ticker(quotes: dict[str, Quote]) -> None:
    """Cinta de cotizaciones estilo CNBC — scroll horizontal infinito en CSS puro (sin JS),
    verde/rojo según el signo de `net_change_pct`. La fila se duplica una vez para que el loop
    de la animación (traslada -50%) no muestre un salto/corte al llegar al final."""
    items = list(quotes.values())
    if not items:
        return

    def _item_html(q: Quote) -> str:
        color = GOOD if q.net_change_pct >= 0 else CRITICAL
        arrow = "trending-up" if q.net_change_pct >= 0 else "trending-down"
        return (
            "<span class='oia-ticker-item'>"
            f"<b>{q.symbol}</b> ${q.last_price:,.2f} "
            f"<span style='color:{color};'>{icon(arrow, size=13, color=color)} {q.net_change_pct:+.2f}%</span>"
            "</span>"
        )

    row_html = "".join(_item_html(q) for q in items)
    st.markdown(f"<div class='oia-ticker'><div class='oia-ticker-track'>{row_html}{row_html}</div></div>", unsafe_allow_html=True)


def render_market_movers_panel(index: str = "$SPX") -> None:
    """Ganadoras/perdedoras del índice de referencia, estilo CNBC — endpoint `/movers`
    confirmado en vivo 2026-07-25 (ver NOTES.md). Vacío fuera de horario de mercado (el
    endpoint real no tiene datos que devolver) o en modo mock sin fixture cargada."""
    gainers = cached_movers(index, "PERCENT_CHANGE_UP")
    losers = cached_movers(index, "PERCENT_CHANGE_DOWN")

    html = ["<div class='oia-card'>", f"<div style='font-size:1.1rem; font-weight:700;'>{icon('bar-chart', size=18, color=ACCENT)} Market Movers · {index}</div>"]

    if not gainers and not losers:
        html.append(f"<div style='color:{TEXT_MUTED}; margin-top:0.4rem;'>Sin datos de movers ahora mismo (fuera de horario de mercado, o sin fixture en modo mock).</div>")
        html.append("</div>")
        st.markdown("".join(html), unsafe_allow_html=True)
        return

    def _rows(movers: list[Mover], color: str) -> str:
        if not movers:
            return f"<div style='color:{TEXT_MUTED}; font-size:0.85rem;'>Sin datos.</div>"
        return "".join(
            "<div class='oia-leg-row'>"
            f"<span><b>{m.symbol}</b> · {m.description}</span>"
            f"<span style='color:{color}; font-weight:700;'>{m.change_pct:+.2f}% · ${m.last_price:,.2f}</span>"
            "</div>"
            for m in movers[:8]
        )

    html.append("<div style='display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:1.5rem; margin-top:0.8rem;'>")
    html.append(f"<div><div style='color:{GOOD}; font-weight:700; margin-bottom:0.4rem;'>{icon('trending-up', size=15, color=GOOD)} Ganadoras</div>{_rows(gainers, GOOD)}</div>")
    html.append(f"<div><div style='color:{CRITICAL}; font-weight:700; margin-bottom:0.4rem;'>{icon('trending-down', size=15, color=CRITICAL)} Perdedoras</div>{_rows(losers, CRITICAL)}</div>")
    html.append("</div></div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def score_pill_html(score: int) -> str:
    """Verde ≥70%, amarillo por debajo — no hay tercer nivel porque el motor ya filtra
    cualquier score bajo el umbral mínimo antes de generar la alerta (alerts/engine.py), no
    debería llegar a mostrarse una alerta de score realmente bajo."""
    color = GOOD if score >= 70 else WARNING
    return f"<span class='oia-pill' style='color:{color}; border-color:{color}44; background:{color}1a;'>● {score}%</span>"


def pop_badge_html(probability_of_profit: float | None) -> str:
    """POP como badge grande y destacado — es el número que más importa de un vistazo al
    vender prima, no debería competir visualmente con el resto de las métricas chicas."""
    if probability_of_profit is None:
        return f"<span class='oia-pill' style='font-size:0.95rem; color:{TEXT_MUTED}; border-color:{TEXT_MUTED}44;'>{icon('target', size=15)} POP N/D</span>"
    pct = probability_of_profit * 100
    color = GOOD if pct >= 70 else WARNING if pct >= 50 else CRITICAL
    return (
        f"<span class='oia-pill' style='font-size:1.1rem; font-weight:800; padding:0.3rem 0.9rem; "
        f"color:{color}; border-color:{color}44; background:{color}1a;'>{icon('target', size=17, color=color)} POP {pct:.0f}%</span>"
    )


def coverage_badge_html(coverage: list[dict]) -> str:
    """% que el subyacente necesita moverse para llegar al strike vendido, con el mismo
    tratamiento visual destacado que el POP (pedido 2026-07-24) — es el otro número que
    importa de un vistazo antes de vender prima. Iron Condor arma dos badges (baja/alza),
    el resto de las estrategias del MVP arma uno solo."""
    if not coverage:
        return ""
    badges = []
    for c in coverage:
        pct = c["coverage_pct"] * 100
        color = GOOD if pct >= 15 else WARNING if pct >= 7 else CRITICAL
        arrow_icon = "trending-down" if c["option_type"] == "put" else "trending-up"
        badges.append(
            f"<span class='oia-pill' style='font-size:0.95rem; font-weight:700; padding:0.3rem 0.8rem; "
            f"color:{color}; border-color:{color}44; background:{color}1a;'>{icon(arrow_icon, size=15, color=color)} "
            f"Cobertura {pct:.1f}%</span>"
        )
    return "".join(badges)


def _fmt_time(alert_ts: str) -> str:
    try:
        return datetime.fromisoformat(alert_ts).strftime("%H:%M")
    except ValueError:
        return "N/D"


_RISK_LEVEL_COLORS = {"alto": CRITICAL, "medio": WARNING, "bajo": GOOD}
_RISK_LEVEL_LABELS = {"alto": "Alto", "medio": "Medio", "bajo": "Bajo"}


def risk_level_pill_html(level: str) -> str:
    """Punto de color sólido en vez del emoji 🔴🟡🟢 — mismo semáforo, trazo consistente con
    el resto de los íconos outline."""
    color = _RISK_LEVEL_COLORS.get(level, TEXT_MUTED)
    label = _RISK_LEVEL_LABELS.get(level, level)
    dot = f"<span style='width:8px; height:8px; border-radius:50%; background:{color}; display:inline-block;'></span>"
    return f"<span class='oia-pill' style='color:{color}; border-color:{color}44; background:{color}1a;'>{dot} {label}</span>"


def risk_badge(score: int) -> str:
    if score >= 80:
        return f"🟢 {score}"
    if score >= 65:
        return f"🟡 {score}"
    return f"🔴 {score}"


def _leg_row_html(leg: dict) -> str:
    is_sell = leg["side"] == "sell"
    side_color = CRITICAL if is_sell else GOOD
    side_icon = icon("arrow-down", size=14, color=side_color) if is_sell else icon("arrow-up", size=14, color=side_color)
    side_label = "Venta" if is_sell else "Compra"
    option_label = "Put" if leg["option_type"] == "put" else "Call"
    quantity = leg.get("quantity", 1)
    return (
        "<div class='oia-leg-row'>"
        f"<span style='color:{side_color}; font-weight:600;'>{side_icon} {side_label} · {quantity} {option_label}</span>"
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
        return f"<div class='oia-caveat'>{icon('help-circle', size=15)} No se pudo verificar la fecha de earnings — confirmá manualmente antes de operar.</div>"
    if expiration_date and next_earnings_date <= expiration_date:
        return (
            f"<div class='oia-caveat' style='color:{CRITICAL};'>{icon('alert-triangle', size=15, color=CRITICAL)} "
            f"Earnings el {next_earnings_date} — CAE DENTRO del vencimiento de esta posición, riesgo de gap.</div>"
        )
    return (
        f"<div style='color:{GOOD}; font-size:0.82rem; margin-top:0.3rem;'>{icon('check-circle', size=14, color=GOOD)} "
        f"Sin earnings antes del vencimiento (próximo: {next_earnings_date}).</div>"
    )


def _fed_event_caveat_html(fed_meeting_date: str | None, expiration_date: str | None) -> str | None:
    if not fed_meeting_date or not expiration_date or fed_meeting_date > expiration_date:
        return None
    return (
        f"<div class='oia-caveat' style='color:{CRITICAL};'>{icon('alert-triangle', size=15, color=CRITICAL)} Reunión FOMC el {fed_meeting_date} — "
        "CAE DENTRO del vencimiento de esta posición, riesgo de gap por decisión de tasas.</div>"
    )


def _liquidity_caveat_html(warnings: list[dict]) -> str:
    if not warnings:
        return ""
    parts = [
        f"{'Put' if w['option_type'] == 'put' else 'Call'} ${w['strike']:.2f} ({w['spread_pct'] * 100:.0f}% del precio medio)"
        for w in warnings
    ]
    return (
        f"<div class='oia-caveat' style='color:{WARNING};'>{icon('alert-triangle', size=15, color=WARNING)} "
        f"Spread bid/ask ancho en {', '.join(parts)} — confirmá el precio real antes de operar.</div>"
    )


def _dividend_caveat_html(warnings: list[dict]) -> str:
    if not warnings:
        return ""
    strikes = ", ".join(f"${w['strike']:.2f}" for w in warnings)
    ex_date = warnings[0]["ex_dividend_date"]
    return (
        f"<div class='oia-caveat' style='color:{WARNING};'>{icon('alert-triangle', size=15, color=WARNING)} "
        f"Ex-dividendo el {ex_date} — antes del vencimiento de la Call {strikes} vendida, riesgo de asignación "
        "anticipada (perder las acciones y el dividendo antes de lo planeado).</div>"
    )


def _capital_at_risk_caveat_html(max_loss: float | None, capital_available: float | None) -> str:
    """Riesgo real en dólares de ESTA posición sola vs. el capital configurado en
    Configuración. Pedido explícito 2026-07-26 al sumar índices (SPX/RUT/NDX): el motor no
    filtra por tamaño de posición (cash_secured_put/short_put_naked sobre un índice pueden
    arriesgar cientos de miles de dólares de notional, spread muy distinto a una acción de
    $50-500), así que se muestra el riesgo explícito en vez de excluir la estrategia —
    decisión del usuario, no del motor. `max_loss` en `inf` (riesgo no acotado) ya se muestra
    como "Ilimitado" en la tarjeta de Pérdida máxima; ese caso no necesita este cálculo."""
    if max_loss is None or math.isinf(max_loss) or not capital_available:
        return ""
    pct = max_loss / capital_available
    if pct >= 1.0:
        return (
            f"<div class='oia-caveat' style='color:{CRITICAL};'>{icon('alert-triangle', size=15, color=CRITICAL)} "
            f"Esta posición sola arriesga {_fmt_money(max_loss)} — {pct * 100:.0f}% de tu capital configurado "
            f"({_fmt_money(capital_available)}). Muy por encima de tu tamaño de cuenta.</div>"
        )
    if pct >= 0.25:
        return (
            f"<div class='oia-caveat' style='color:{WARNING};'>{icon('alert-triangle', size=15, color=WARNING)} "
            f"Riesgo real: {_fmt_money(max_loss)} ({pct * 100:.0f}% de tu capital configurado, "
            f"{_fmt_money(capital_available)}) si esta posición sola llega a la pérdida máxima.</div>"
        )
    return ""


def render_alert_card(
    alert: sqlite3.Row,
    candidate: sqlite3.Row | None,
    next_earnings_date: str | None = None,
    fed_meeting_date: str | None = None,
    next_ex_dividend_date: str | None = None,
    capital_available: float | None = None,
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
    net_premium = candidate["net_premium"] if candidate else None
    probability_of_profit = candidate["probability_of_profit"] if candidate else None

    narrative = alert["narrative_text"] or ""
    if "✎ Comentario:" in narrative:
        comment = narrative.split("✎ Comentario:", 1)[1].strip()
    else:
        comment = narrative or "Sin comentario disponible."

    coverage = compute_coverage(legs, underlying_price)

    html = ["<div class='oia-card'>"]
    # Línea principal compacta: símbolo/estrategia a la izquierda, POP + cobertura + score a la
    # derecha — son los números que más importan de un vistazo al vender prima.
    html.append(
        "<div style='display:flex; justify-content:space-between; align-items:flex-start; gap:1rem;'>"
        f"<div style='font-size:1.3rem; font-weight:700;'>{icon('pin', size=19, color=ACCENT)} {alert['symbol']} — {label}</div>"
        f"<div style='display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap; justify-content:flex-end;'>"
        f"{pop_badge_html(probability_of_profit)}{coverage_badge_html(coverage)}{score_pill_html(alert['conviction_score'])}</div>"
        "</div>"
    )
    meta_bits = [f"{icon('clock', size=14)} {_fmt_time(alert['alert_ts'])}"]
    if underlying_price is not None:
        meta_bits.append(f"{icon('dollar', size=14)} ${underlying_price:,.2f}")
    if breakevens:
        meta_bits.append(f"{icon('scale', size=14)} BE " + "/".join(f"${b:,.2f}" for b in breakevens))
    if net_premium is not None:
        premium_kind = "crédito" if net_premium >= 0 else "débito"
        meta_bits.append(f"{icon('dollar', size=14)} {_fmt_money(abs(net_premium))} {premium_kind}")
    html.append(f"<div style='color:{TEXT_SECONDARY}; font-size:0.85rem; margin-top:0.2rem;'>{' · '.join(meta_bits)}</div>")
    html.append(
        f"<div style='color:{TEXT_MUTED}; font-size:0.78rem; margin-top:0.1rem;'>"
        f"perfil {alert['risk_profile']} · umbral {alert['threshold_applied']}</div>"
    )

    html.append(_earnings_caveat_html(next_earnings_date, expiration_date))
    fed_caveat = _fed_event_caveat_html(fed_meeting_date, expiration_date)
    if fed_caveat:
        html.append(fed_caveat)
    liquidity_caveat = _liquidity_caveat_html(assess_liquidity(legs))
    if liquidity_caveat:
        html.append(liquidity_caveat)
    dividend_caveat = _dividend_caveat_html(assess_dividend_risk(legs, next_ex_dividend_date))
    if dividend_caveat:
        html.append(dividend_caveat)
    capital_caveat = _capital_at_risk_caveat_html(candidate["max_loss"] if candidate else None, capital_available)
    if capital_caveat:
        html.append(capital_caveat)

    if legs:
        html.append("<div style='margin-top:0.8rem;'>")
        html.extend(_leg_row_html(leg) for leg in legs)
        share_line = share_requirement_line(strategy_type, alert["symbol"], underlying_price)
        if share_line:
            html.append(
                f"<div style='color:{TEXT_MUTED}; font-size:0.85rem; margin-top:0.4rem;'>{icon('info', size=14)} {share_line}</div>"
            )
        html.append("</div>")

        html.append("<div class='oia-metric-grid'>")
        html.append(
            f"<div class='oia-metric-tile'><div class='label'>{icon('trending-up', size=13, color=GOOD)} Beneficio máximo</div>"
            f"<div class='value'>{_fmt_money(candidate['max_profit'])}</div></div>"
        )
        html.append(
            f"<div class='oia-metric-tile'><div class='label'>{icon('trending-down', size=13, color=CRITICAL)} Pérdida máxima</div>"
            f"<div class='value'>{_fmt_money(candidate['max_loss'])}</div></div>"
        )
        html.append(
            f"<div class='oia-metric-tile'><div class='label'>{icon('clock', size=13)} DTE</div>"
            f"<div class='value'>{candidate['dte'] if candidate['dte'] is not None else 'N/D'} días</div></div>"
        )
        annualized_return_pct = candidate["annualized_return_pct"]
        if annualized_return_pct is not None:
            html.append(
                f"<div class='oia-metric-tile'><div class='label'>{icon('bar-chart', size=13, color=ACCENT)} Rend. anualizado</div>"
                f"<div class='value'>{annualized_return_pct:.1f}%</div></div>"
            )
        html.append("</div>")

        early_close_json = candidate["early_close_projection_json"]
        early_close_projection = json.loads(early_close_json) if early_close_json else []
        if early_close_projection:
            parts = [f"{p['pct']}% en {p['days']}d" if p["days"] is not None else f"{p['pct']}%: no alcanzado" for p in early_close_projection]
            html.append(
                f"<div style='color:{TEXT_SECONDARY}; font-size:0.82rem; margin-top:0.4rem;'>{icon('clock', size=13)} "
                f"Cierre anticipado (si precio/IV no cambian): {' · '.join(parts)}</div>"
            )

        if is_estimate:
            html.append(
                f"<div style='color:{TEXT_MUTED}; font-size:0.8rem;'>{icon('info', size=14)} Beneficio máximo, pérdida "
                "máxima y breakeven(s) son una estimación por modelo (vencimientos combinados).</div>"
            )
    elif candidate:
        strikes = json.loads(candidate["strikes_json"])
        html.append(f"<div style='margin-top:0.6rem; color:{TEXT_SECONDARY};'>Strikes: {strikes}</div>")
        html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.8rem; margin-top:0.3rem;'>Alerta generada antes de esta actualización — sin datos de P&L calculados.</div>")

    html.append(f"<div class='oia-comment'>{icon('lightbulb', size=16, color=WARNING)} <b>Comentario:</b> {comment}</div>")
    html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.75rem; margin-top:0.6rem;'>fuente de la narración: {alert['narrative_source']}</div>")
    html.append("</div>")

    st.markdown("".join(html), unsafe_allow_html=True)

    with st.expander("📋 Copiar alerta (para WhatsApp/Telegram)", key=f"copy_alert_expander_{alert['id']}"):
        st.code(alert["narrative_text"] or "Sin texto disponible.", language=None)


def render_real_trade_card(
    trade: sqlite3.Row,
    next_earnings_date: str | None = None,
    fed_meeting_date: str | None = None,
    next_ex_dividend_date: str | None = None,
    capital_available: float | None = None,
) -> None:
    """Tarjeta de una operación REAL ya ejecutada (Pestaña Operaciones, réplica automática de
    operaciones reales, pedido 2026-07-25) — mismos datos y mismos helpers que
    `render_alert_card` (patas, prima, P&L, breakevens, POP, avisos de earnings/liquidez/
    dividendo/riesgo de capital), sin conviction_score/risk_profile/threshold_applied (nada se
    puntuó acá, la operación ya se hizo) y con cantidad/precio de entrada/cuenta en su lugar."""
    strategy_type = trade["strategy_type"]
    label = strategy_label(strategy_type)
    expiration_date = trade["expiration_date"]

    legs = json.loads(trade["legs_json"]) if trade["legs_json"] else []
    breakevens = json.loads(trade["breakevens_json"]) if trade["breakevens_json"] else []
    underlying_price = trade["underlying_price"]
    is_estimate = bool(trade["payoff_is_estimate"])
    net_premium = trade["net_premium"]
    probability_of_profit = trade["probability_of_profit"]

    narrative = trade["narrative_text"] or ""
    if "✎ Comentario:" in narrative:
        comment = narrative.split("✎ Comentario:", 1)[1].strip()
    else:
        comment = narrative or "Sin comentario disponible."

    coverage = compute_coverage(legs, underlying_price)

    html = ["<div class='oia-card'>"]
    html.append(
        "<div style='display:flex; justify-content:space-between; align-items:flex-start; gap:1rem;'>"
        f"<div style='font-size:1.3rem; font-weight:700;'>{icon('check-circle', size=19, color=GOOD)} {trade['symbol']} — {label}</div>"
        f"<div style='display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap; justify-content:flex-end;'>"
        f"{pop_badge_html(probability_of_profit)}{coverage_badge_html(coverage)}"
        f"<span class='oia-pill' style='color:{GOOD}; border-color:{GOOD}44; background:{GOOD}1a;'>● Operación real</span></div>"
        "</div>"
    )
    meta_bits = [f"{icon('clock', size=14)} {_fmt_time(trade['trade_ts'])}", f"{icon('clipboard', size=14)} {trade['quantity']} contrato(s)"]
    if trade["entry_price"] is not None:
        meta_bits.append(f"{icon('dollar', size=14)} Entrada ${trade['entry_price']:,.2f}")
    if underlying_price is not None:
        meta_bits.append(f"{icon('dollar', size=14)} ${underlying_price:,.2f}")
    if breakevens:
        meta_bits.append(f"{icon('scale', size=14)} BE " + "/".join(f"${b:,.2f}" for b in breakevens))
    if net_premium is not None:
        premium_kind = "crédito" if net_premium >= 0 else "débito"
        meta_bits.append(f"{icon('dollar', size=14)} {_fmt_money(abs(net_premium))} {premium_kind}")
    html.append(f"<div style='color:{TEXT_SECONDARY}; font-size:0.85rem; margin-top:0.2rem;'>{' · '.join(meta_bits)}</div>")
    html.append(
        f"<div style='color:{TEXT_MUTED}; font-size:0.78rem; margin-top:0.1rem;'>"
        f"cuenta {trade['account_number']} · {trade['occ_symbol']}</div>"
    )

    html.append(_earnings_caveat_html(next_earnings_date, expiration_date))
    fed_caveat = _fed_event_caveat_html(fed_meeting_date, expiration_date)
    if fed_caveat:
        html.append(fed_caveat)
    liquidity_caveat = _liquidity_caveat_html(assess_liquidity(legs))
    if liquidity_caveat:
        html.append(liquidity_caveat)
    dividend_caveat = _dividend_caveat_html(assess_dividend_risk(legs, next_ex_dividend_date))
    if dividend_caveat:
        html.append(dividend_caveat)
    capital_caveat = _capital_at_risk_caveat_html(trade["max_loss"], capital_available)
    if capital_caveat:
        html.append(capital_caveat)

    if legs:
        html.append("<div style='margin-top:0.8rem;'>")
        html.extend(_leg_row_html(leg) for leg in legs)
        share_line = share_requirement_line(strategy_type, trade["symbol"], underlying_price)
        if share_line:
            html.append(
                f"<div style='color:{TEXT_MUTED}; font-size:0.85rem; margin-top:0.4rem;'>{icon('info', size=14)} {share_line}</div>"
            )
        html.append("</div>")

        html.append("<div class='oia-metric-grid'>")
        html.append(
            f"<div class='oia-metric-tile'><div class='label'>{icon('trending-up', size=13, color=GOOD)} Beneficio máximo</div>"
            f"<div class='value'>{_fmt_money(trade['max_profit'])}</div></div>"
        )
        html.append(
            f"<div class='oia-metric-tile'><div class='label'>{icon('trending-down', size=13, color=CRITICAL)} Pérdida máxima</div>"
            f"<div class='value'>{_fmt_money(trade['max_loss'])}</div></div>"
        )
        html.append(
            f"<div class='oia-metric-tile'><div class='label'>{icon('clock', size=13)} DTE</div>"
            f"<div class='value'>{trade['dte'] if trade['dte'] is not None else 'N/D'} días</div></div>"
        )
        annualized_return_pct = trade["annualized_return_pct"]
        if annualized_return_pct is not None:
            html.append(
                f"<div class='oia-metric-tile'><div class='label'>{icon('bar-chart', size=13, color=ACCENT)} Rend. anualizado</div>"
                f"<div class='value'>{annualized_return_pct:.1f}%</div></div>"
            )
        html.append("</div>")

        early_close_json = trade["early_close_projection_json"]
        early_close_projection = json.loads(early_close_json) if early_close_json else []
        if early_close_projection:
            parts = [f"{p['pct']}% en {p['days']}d" if p["days"] is not None else f"{p['pct']}%: no alcanzado" for p in early_close_projection]
            html.append(
                f"<div style='color:{TEXT_SECONDARY}; font-size:0.82rem; margin-top:0.4rem;'>{icon('clock', size=13)} "
                f"Cierre anticipado (si precio/IV no cambian): {' · '.join(parts)}</div>"
            )

        if is_estimate:
            html.append(
                f"<div style='color:{TEXT_MUTED}; font-size:0.8rem;'>{icon('info', size=14)} Beneficio máximo, pérdida "
                "máxima y breakeven(s) son una estimación por modelo (vencimientos combinados).</div>"
            )

    html.append(f"<div class='oia-comment'>{icon('lightbulb', size=16, color=WARNING)} <b>Comentario:</b> {comment}</div>")
    html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.75rem; margin-top:0.6rem;'>fuente de la narración: {trade['narrative_source']}</div>")
    html.append("</div>")

    st.markdown("".join(html), unsafe_allow_html=True)

    with st.expander("📋 Copiar operación (para WhatsApp/Telegram)", key=f"copy_real_trade_expander_{trade['id']}"):
        st.code(trade["narrative_text"] or "Sin texto disponible.", language=None)


def render_news_card(item: sqlite3.Row | dict, badge: str | None = None) -> None:
    """Tarjeta compacta de una noticia (Finnhub /company-news): headline enlazado, fuente y
    fecha de publicación, resumen corto — mismo lenguaje visual que las tarjetas de alerta.
    `badge` opcional (p.ej. símbolos mencionados) para la sección de relevancia cruzada."""
    published = item["published_at"][:10] if item["published_at"] else "fecha N/D"
    source = item["source"] or "fuente N/D"
    headline = item["headline"]
    url = item["url"]
    summary = item["summary"] or ""

    html = ["<div class='oia-card'>"]
    html.append(
        "<div style='display:flex; justify-content:space-between; align-items:baseline; gap:1rem;'>"
        f"<a href='{url}' target='_blank' style='color:{TEXT_PRIMARY}; font-size:1.05rem; font-weight:700; text-decoration:none;'>{icon('news', size=17)} {headline}</a>"
        "</div>"
    )
    html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.8rem; margin-top:0.25rem;'>{source} · {published}</div>")
    if badge:
        html.append(f"<div style='margin-top:0.4rem;'><span class='oia-pill' style='color:{ACCENT}; border-color:{ACCENT}44; background:{ACCENT}1a;'>{badge}</span></div>")
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

    html = ["<div class='oia-card'>", f"<div style='font-size:1.1rem; font-weight:700;'>{icon('landmark', size=18, color=ACCENT)} Contexto macro</div>"]
    html.append(f"<div style='color:{TEXT_MUTED}; font-size:0.8rem; margin-top:0.1rem;'>Actualizado {snap['snapshot_date']}</div>")

    if snap["fed_funds_upper"] is not None:
        html.append(
            f"<div style='margin-top:0.6rem; color:{TEXT_SECONDARY};'>Tasa de fondos federales vigente: "
            f"<b>{snap['fed_funds_lower']:.2f}% – {snap['fed_funds_upper']:.2f}%</b></div>"
        )

    if snap["fed_meeting_date"] is not None:
        html.append(f"<div style='color:{TEXT_SECONDARY}; margin-top:0.3rem;'>Próxima reunión FOMC: <b>{snap['fed_meeting_date']}</b></div>")
        html.append("<div class='oia-metric-grid'>")
        html.append(f"<div class='oia-metric-tile'><div class='label'>{icon('trending-up', size=13, color=GOOD)} Sube</div><div class='value'>{_fmt_pct(snap['fed_hike_probability'])}</div></div>")
        html.append(f"<div class='oia-metric-tile'><div class='label'>{icon('arrow-right', size=13)} Mantiene</div><div class='value'>{_fmt_pct(snap['fed_hold_probability'])}</div></div>")
        html.append(f"<div class='oia-metric-tile'><div class='label'>{icon('trending-down', size=13, color=CRITICAL)} Baja</div><div class='value'>{_fmt_pct(snap['fed_cut_probability'])}</div></div>")
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

    html = ["<div class='oia-card'>", f"<div style='font-size:1.1rem; font-weight:700;'>{icon('clipboard', size=18, color=ACCENT)} Resumen de oportunidades de hoy</div>"]

    if summary["total_alerts"] == 0:
        html.append(f"<div style='color:{TEXT_MUTED}; margin-top:0.4rem;'>Todavía no hay alertas hoy ({alert_date}).</div>")
        html.append("</div>")
        st.markdown("".join(html), unsafe_allow_html=True)
        return

    html.append("<div class='oia-metric-grid'>")
    html.append(f"<div class='oia-metric-tile'><div class='label'>{icon('bell', size=13)} Alertas hoy</div><div class='value'>{summary['total_alerts']}</div></div>")

    directional = summary["directional"]
    html.append(f"<div class='oia-metric-tile'><div class='label'>{icon('trending-up', size=13, color=GOOD)} Alcistas</div><div class='value'>{directional['bullish']}</div></div>")
    html.append(f"<div class='oia-metric-tile'><div class='label'>{icon('trending-down', size=13, color=CRITICAL)} Bajistas</div><div class='value'>{directional['bearish']}</div></div>")
    html.append(f"<div class='oia-metric-tile'><div class='label'>{icon('arrow-right', size=13)} Neutrales</div><div class='value'>{directional['neutral']}</div></div>")

    net_delta = summary["net_delta"]
    net_delta_str = f"{net_delta:+.2f}" if net_delta is not None else "N/D"
    html.append(f"<div class='oia-metric-tile'><div class='label'>{icon('scale', size=13)} Delta neto total</div><div class='value'>{net_delta_str}</div></div>")

    bounded_risk = summary["bounded_risk_total"]
    risk_str = _fmt_money(bounded_risk) if bounded_risk is not None else "N/D"
    html.append(f"<div class='oia-metric-tile'><div class='label'>{icon('trending-down', size=13, color=CRITICAL)} Riesgo total (acotado)</div><div class='value'>{risk_str}</div></div>")
    html.append("</div>")

    if summary["unbounded_risk_count"] > 0:
        html.append(
            f"<div class='oia-caveat'>{icon('alert-triangle', size=15, color=WARNING)} {summary['unbounded_risk_count']} estrategia(s) de riesgo no acotado (short naked) "
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


def render_notification_bell(conn: sqlite3.Connection) -> None:
    """Campanita de notificaciones en el sidebar — llamar al inicio de cada página, mismo
    patrón que inject_theme(). Hoy la única fuente es el digest pre-apertura
    (scheduler/jobs.py::job_premarket_digest), pensada genérica para sumar otros `kind` después
    sin tocar esto. Reemplaza el plan original de notificar por Telegram."""
    unread_count = repo.get_unread_notification_count(conn)
    label = f"🔔 {unread_count}" if unread_count > 0 else "🔔"

    with st.sidebar:
        with st.popover(label, use_container_width=True):
            notifications = repo.get_recent_notifications(conn, limit=20)
            if not notifications:
                st.caption("Sin notificaciones todavía.")
                return

            if unread_count > 0 and st.button("Marcar todas como leídas", key="mark_notifications_read", use_container_width=True):
                repo.mark_all_notifications_read(conn)
                st.rerun()

            for notification in notifications:
                marker = "🔴 " if not notification["is_read"] else ""
                title = f"{marker}{notification['title']}"
                with st.expander(title):
                    st.text(notification["body"])
