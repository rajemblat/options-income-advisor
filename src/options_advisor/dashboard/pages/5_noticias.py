from __future__ import annotations

import streamlit as st

from options_advisor.dashboard.components import ACCENT, get_connection, get_symbols, icon, inject_theme, render_header, render_news_card, render_notification_bell
from options_advisor.dashboard.news_relevance import find_cross_symbol_news
from options_advisor.storage import repository as repo

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

symbols = ["Todos"] + symbols_list
selected = st.selectbox("Símbolo", symbols)

news = repo.get_recent_news(conn, symbol=None if selected == "Todos" else selected, limit=100)

if not news:
    st.info("Todavía no hay noticias cargadas. Andá a la página principal y corré el análisis (requiere FINNHUB_API_KEY).")
else:
    for item in news:
        render_news_card(item)
