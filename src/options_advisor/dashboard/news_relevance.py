from __future__ import annotations

import re


def _symbol_mentioned(symbol: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(symbol)}\b", text, re.IGNORECASE) is not None


def find_cross_symbol_news(items: list[dict], symbols: list[str]) -> list[dict]:
    """Heurística de relevancia cruzada sin sentiment (el plan gratuito de Finnhub no incluye
    `/news-sentiment`, solo el plan pago "All in One"): marca noticias cuyo headline+resumen
    mencionan 2 o más símbolos distintos de la watchlist — proxy de "esto puede importarte
    aunque la hayas traído por otro símbolo". Devuelve copias de los items con
    `mentioned_symbols` agregado, ordenadas por cantidad de símbolos mencionados (desc) y
    luego por fecha de publicación (desc)."""
    results = []
    for item in items:
        text = f"{item.get('headline') or ''} {item.get('summary') or ''}"
        mentioned = sorted({s for s in symbols if _symbol_mentioned(s, text)})
        if len(mentioned) >= 2:
            results.append({**item, "mentioned_symbols": mentioned})
    results.sort(key=lambda r: (len(r["mentioned_symbols"]), r.get("published_at") or ""), reverse=True)
    return results
