from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from options_advisor.storage import db
from options_advisor.storage import repository as repo
from options_advisor.storage.models import NewsItem


@pytest.fixture
def conn():
    return db.connect(":memory:")


def _news_item(symbol: str, url: str, headline: str, published_at: datetime | None) -> NewsItem:
    return NewsItem(
        symbol=symbol,
        published_at=published_at,
        headline=headline,
        source="Yahoo",
        url=url,
        summary="resumen",
        fetched_date=date(2026, 7, 23),
    )


def test_insert_and_get_recent_news_orders_by_published_at_desc(conn):
    items = [
        _news_item("AAPL", "https://x/1", "old", datetime(2026, 7, 20, tzinfo=timezone.utc)),
        _news_item("AAPL", "https://x/2", "new", datetime(2026, 7, 22, tzinfo=timezone.utc)),
    ]
    repo.insert_news_items(conn, items)

    result = repo.get_recent_news(conn, symbol="AAPL")
    assert [r["headline"] for r in result] == ["new", "old"]


def test_insert_news_items_dedupes_by_symbol_and_url(conn):
    item = _news_item("AAPL", "https://x/1", "headline", datetime(2026, 7, 20, tzinfo=timezone.utc))
    repo.insert_news_items(conn, [item])
    repo.insert_news_items(conn, [item])  # misma corrida repetida del job, no debe duplicar

    result = repo.get_recent_news(conn, symbol="AAPL")
    assert len(result) == 1


def test_get_recent_news_filters_by_symbol(conn):
    repo.insert_news_items(
        conn,
        [
            _news_item("AAPL", "https://x/1", "aapl news", datetime(2026, 7, 20, tzinfo=timezone.utc)),
            _news_item("MSFT", "https://x/2", "msft news", datetime(2026, 7, 21, tzinfo=timezone.utc)),
        ],
    )
    result = repo.get_recent_news(conn, symbol="MSFT")
    assert len(result) == 1
    assert result[0]["symbol"] == "MSFT"


def test_get_recent_news_without_symbol_returns_all(conn):
    repo.insert_news_items(
        conn,
        [
            _news_item("AAPL", "https://x/1", "aapl news", datetime(2026, 7, 20, tzinfo=timezone.utc)),
            _news_item("MSFT", "https://x/2", "msft news", datetime(2026, 7, 21, tzinfo=timezone.utc)),
        ],
    )
    result = repo.get_recent_news(conn)
    assert len(result) == 2
