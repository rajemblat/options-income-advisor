from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import Quote
from options_advisor.market_context import finnhub_client


@dataclass
class SymbolSearchResult:
    quote: Quote | None
    news: list[dict]
    error: str | None


def search_symbol(broker: BrokerClient, symbol: str, api_key: str | None, as_of: date) -> SymbolSearchResult:
    """Cotización en vivo + noticias recientes para CUALQUIER símbolo, no solo la watchlist
    (pedido 2026-07-24, página Noticias). A diferencia de `finnhub_client.get_recent_news`
    (nunca excepciona), `broker.get_quote` sí puede tirar excepción con un símbolo inválido
    (ver `SchwabBrokerClient.get_quote`) — la capturamos acá para dar un error claro en vez de
    romper la página."""
    normalized = symbol.strip().upper()
    try:
        quote = broker.get_quote(normalized)
    except Exception:
        return SymbolSearchResult(quote=None, news=[], error=f"No se encontró el símbolo '{normalized}' o no se pudo obtener su cotización.")
    news = finnhub_client.get_recent_news(normalized, as_of, api_key, lookback_days=7, limit=10)
    return SymbolSearchResult(quote=quote, news=news, error=None)
