from __future__ import annotations

from datetime import date

import pytest

from options_advisor.market_context import finnhub_client
from options_advisor.scheduler import jobs
from options_advisor.storage import db
from options_advisor.storage import repository as repo

TODAY = date(2026, 7, 23)


@pytest.fixture
def conn():
    return db.connect(":memory:")


def test_refresh_news_for_symbol_persists_items(conn, monkeypatch):
    monkeypatch.setattr(
        finnhub_client,
        "get_recent_news",
        lambda symbol, as_of, api_key, **kwargs: [
            {"headline": "AAPL news", "source": "Yahoo", "url": "https://x/1", "summary": "s", "published_at": None}
        ],
    )
    jobs._refresh_news_for_symbol(conn, "AAPL", TODAY, "fake-key")

    result = repo.get_recent_news(conn, symbol="AAPL")
    assert len(result) == 1
    assert result[0]["headline"] == "AAPL news"


def test_refresh_news_for_symbol_never_raises_on_finnhub_failure(conn, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("Finnhub caído")

    monkeypatch.setattr(finnhub_client, "get_recent_news", _boom)
    jobs._refresh_news_for_symbol(conn, "AAPL", TODAY, "fake-key")  # no debe lanzar

    assert repo.get_recent_news(conn, symbol="AAPL") == []


def test_refresh_news_for_symbol_skips_items_without_url(conn, monkeypatch):
    monkeypatch.setattr(
        finnhub_client,
        "get_recent_news",
        lambda symbol, as_of, api_key, **kwargs: [
            {"headline": "sin url", "source": "Yahoo", "url": None, "summary": "s", "published_at": None}
        ],
    )
    jobs._refresh_news_for_symbol(conn, "AAPL", TODAY, "fake-key")
    assert repo.get_recent_news(conn, symbol="AAPL") == []
