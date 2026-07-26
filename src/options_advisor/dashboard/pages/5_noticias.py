from __future__ import annotations

import os
from datetime import date

import streamlit as st

from options_advisor.dashboard.components import ACCENT, get_broker, get_connection, get_symbols, icon, inject_theme, render_header, render_news_card, render_notification_bell
from options_advisor.dashboard.news_relevance import find_cross_symbol_news
from options_advisor.dashboard.symbol_search import SymbolSearchResult, search_symbol
from options_advisor.storage import repository as repo


@st.cache_data(ttl=300, show_spinner="Buscando símbolo...")
def _cached_search_symbol(symbol: str, as_of_iso: str) -> SymbolSearchResult:
    """Cacheada 5 min: evita pegarle de nuevo al broker/Finnhub si el usuario repite la misma
    búsqueda mientras navega la página (Streamlit rerenderiza en cada interacción)."""
    return search_symbol(get_broker(), symbol, os.environ.get("FINNHUB_API_KEY"), date.fromisoformat(as_of_iso))


st.set_page_config(page_title="Noticias", page_icon="📰", layout="wide")
inject_theme()
render_header(icon("news", size=24, color=ACCENT), "Noticias por símbolo", "Últimas noticias vía Finnhub, más recientes primero")

conn = get_connection()
render_notification_bell(conn)
symbols_list = get_symbols()

all_recent_news = [dict(r) for r in repo.get_recent_news(conn, limit=200)]
cross_symbol_news = find_cross_symbol_news(all_recent_news, symbols_list)

st.subheader("🔥 Lo más relevante hoy")
st.caption(
    "Noticias que mencionan 2 o más símbolos de tu watchlist — heurística de texto, no sentiment "
    "(tu plan de Finnhub no incluye /news-sentiment, ver Configuración)."
)
if cross_symbol_news:
    for item in cross_symbol_news[:5]:
        render_news_card(item, badge=f"{icon('link', size=13)} Menciona: " + ", ".join(item["mentioned_symbols"]))
else:
    st.caption("Ninguna noticia reciente menciona 2+ símbolos de tu watchlist todavía.")

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)

st.subheader("Buscar cualquier símbolo")
st.caption(
    "Cotización y noticias en vivo para cualquier símbolo, no solo tu watchlist. Se cachea 5 "
    "minutos por símbolo para no golpear el rate limit del broker/Finnhub si repetís la búsqueda."
)
query = st.text_input("Símbolo", placeholder="ej. NFLX", key="news_symbol_search").strip().upper()
if query:
    result = _cached_search_symbol(query, date.today().isoformat())
    if result.error:
        st.error(result.error)
    else:
        q = result.quote
        col1, col2, col3 = st.columns(3)
        col1.metric("Último precio", f"${q.last_price:,.2f}")
        col2.metric("Bid", f"${q.bid:,.2f}")
        col3.metric("Ask", f"${q.ask:,.2f}")
        if result.news:
            for item in result.news:
                render_news_card({**item, "published_at": item["published_at"].isoformat() if item["published_at"] else None})
        else:
            st.info(f"Sin noticias de los últimos 7 días para {query}.")

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)

symbols = ["Todos"] + symbols_list
selected = st.selectbox("Símbolo", symbols)

news = repo.get_recent_news(conn, symbol=None if selected == "Todos" else selected, limit=100)

if not news:
    st.info("Todavía no hay noticias cargadas. Andá a la página principal y corré el análisis (requiere FINNHUB_API_KEY).")
else:
    for item in news:
        render_news_card(item)
