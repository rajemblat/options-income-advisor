from __future__ import annotations

from datetime import date

import pytest

from options_advisor.config import load_settings
from options_advisor.market_context import finnhub_client
from options_advisor.scheduler import jobs
from options_advisor.storage import db
from options_advisor.storage import repository as repo
from options_advisor.storage.models import MacroSnapshot

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


def test_job_premarket_digest_saves_dashboard_notification_with_risk_events_and_new_alerts(conn, monkeypatch):
    monkeypatch.setattr(jobs, "is_market_day", lambda d: True)
    monkeypatch.setattr(
        jobs,
        "_run_full_analysis",
        lambda *a, **k: [{"symbol": "AAPL", "strategy_type": "cash_secured_put", "score": 80}],
    )
    repo.upsert_macro_snapshot(
        conn,
        MacroSnapshot(
            snapshot_date=TODAY,
            upcoming_events=[{"date": TODAY.isoformat(), "event": "Decisión de tasas de la Fed (FOMC)", "country": "US", "impact": "high"}],
        ),
    )

    jobs.job_premarket_digest(broker=None, conn=conn, symbols=["AAPL"], settings=load_settings(), anthropic_api_key=None)

    assert repo.get_unread_notification_count(conn) == 1
    notification = repo.get_recent_notifications(conn, limit=1)[0]
    assert notification["kind"] == "premarket_digest"
    assert "FOMC" in notification["body"]
    assert "AAPL" in notification["body"]
    assert "Cash-Secured Put" in notification["body"]


def test_job_premarket_digest_skips_on_non_market_day(conn, monkeypatch):
    monkeypatch.setattr(jobs, "is_market_day", lambda d: False)
    called = []
    monkeypatch.setattr(jobs, "_run_full_analysis", lambda *a, **k: called.append(1))

    jobs.job_premarket_digest(broker=None, conn=conn, symbols=["AAPL"], settings=load_settings(), anthropic_api_key=None)
    assert called == []
    assert repo.get_unread_notification_count(conn) == 0
