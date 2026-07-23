from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from options_advisor.storage import db
from options_advisor.storage import repository as repo
from options_advisor.storage.models import Alert, CandidateContract, NewsItem


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


def _insert_alert_with_candidate(conn, symbol: str, alert_date: date, strategy_type: str, delta: float, max_loss: float) -> None:
    candidate_id = repo.insert_candidate_contract(
        conn,
        CandidateContract(
            symbol=symbol,
            snapshot_date=alert_date,
            strategy_type=strategy_type,
            expiration_date=date(2026, 8, 15),
            strikes={"short": 100},
            delta=delta,
            greeks_source="calculated",
            conviction_score=80,
            scoring_breakdown={},
            max_loss=max_loss,
        ),
    )
    repo.insert_alert(
        conn,
        Alert(
            symbol=symbol,
            alert_date=alert_date,
            alert_ts=datetime(2026, 7, 23, 10, 0),
            candidate_contract_id=candidate_id,
            conviction_score=80,
            risk_profile="moderado",
            threshold_applied=65,
            was_notified=True,
            narrative_text="texto",
            narrative_source="fallback_template",
            dedup_key=f"{symbol}-{strategy_type}-{alert_date}",
        ),
    )


def test_get_alerts_for_date_joins_candidate_fields(conn):
    today = date(2026, 7, 23)
    _insert_alert_with_candidate(conn, "AAPL", today, "cash_secured_put", delta=0.3, max_loss=500.0)
    _insert_alert_with_candidate(conn, "MSFT", today, "iron_condor", delta=0.0, max_loss=300.0)
    _insert_alert_with_candidate(conn, "TSLA", today - timedelta(days=1), "covered_call", delta=0.2, max_loss=200.0)

    rows = repo.get_alerts_for_date(conn, today)
    assert {r["symbol"] for r in rows} == {"AAPL", "MSFT"}
    aapl = next(r for r in rows if r["symbol"] == "AAPL")
    assert aapl["strategy_type"] == "cash_secured_put"
    assert aapl["delta"] == 0.3
    assert aapl["max_loss"] == 500.0
