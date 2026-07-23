from __future__ import annotations

import streamlit as st

from options_advisor.dashboard.components import get_connection, get_symbols, inject_theme, render_header, render_news_card
from options_advisor.storage import repository as repo

st.set_page_config(page_title="Noticias", page_icon="📰", layout="wide")
inject_theme()
render_header("📰", "Noticias por símbolo", "Últimas noticias vía Finnhub, más recientes primero")

conn = get_connection()
symbols = ["Todos"] + get_symbols()
selected = st.selectbox("Símbolo", symbols)

news = repo.get_recent_news(conn, symbol=None if selected == "Todos" else selected, limit=100)

if not news:
    st.info("Todavía no hay noticias cargadas. Andá a la página principal y corré el análisis (requiere FINNHUB_API_KEY).")
else:
    for item in news:
        render_news_card(item)
