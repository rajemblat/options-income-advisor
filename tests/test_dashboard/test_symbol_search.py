from __future__ import annotations

from datetime import date

from options_advisor.broker.models import Quote
from options_advisor.dashboard.symbol_search import search_symbol


class _FakeBroker:
    def __init__(self, quote: Quote | None = None, raises: bool = False):
        self._quote = quote
        self._raises = raises

    def get_quote(self, symbol: str) -> Quote:
        if self._raises:
            raise ValueError(f"unknown symbol {symbol}")
        assert self._quote is not None
        return self._quote


def test_search_symbol_returns_quote_and_news(monkeypatch):
    quote = Quote(symbol="NFLX", as_of=date(2026, 7, 24), last_price=500.0, bid=499.5, ask=500.5)
    broker = _FakeBroker(quote=quote)
    monkeypatch.setattr(
        "options_advisor.dashboard.symbol_search.finnhub_client.get_recent_news",
        lambda symbol, as_of, api_key, lookback_days=7, limit=10: [{"headline": "x"}],
    )

    result = search_symbol(broker, "nflx", "fake-key", date(2026, 7, 24))

    assert result.error is None
    assert result.quote == quote
    assert result.news == [{"headline": "x"}]


def test_search_symbol_normalizes_symbol_before_lookup(monkeypatch):
    quote = Quote(symbol="NFLX", as_of=date(2026, 7, 24), last_price=500.0, bid=499.5, ask=500.5)
    broker = _FakeBroker(quote=quote)
    seen = {}

    def fake_get_recent_news(symbol, as_of, api_key, lookback_days=7, limit=10):
        seen["symbol"] = symbol
        return []

    monkeypatch.setattr("options_advisor.dashboard.symbol_search.finnhub_client.get_recent_news", fake_get_recent_news)

    search_symbol(broker, "  nflx  ", None, date(2026, 7, 24))

    assert seen["symbol"] == "NFLX"


def test_search_symbol_invalid_symbol_returns_clear_error():
    broker = _FakeBroker(raises=True)

    result = search_symbol(broker, "ZZZINVALID", None, date(2026, 7, 24))

    assert result.quote is None
    assert result.news == []
    assert "ZZZINVALID" in result.error
