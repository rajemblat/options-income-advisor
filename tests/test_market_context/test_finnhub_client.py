from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest

from options_advisor.market_context import finnhub_client

AS_OF = date(2026, 7, 22)


def _mock_response(json_data, status_code=200):
    def _get(*args, **kwargs):
        request = httpx.Request("GET", "https://finnhub.io/api/v1/x")
        return httpx.Response(status_code, json=json_data, request=request)

    return _get


def test_get_next_earnings_date_without_api_key_returns_none():
    assert finnhub_client.get_next_earnings_date("AAPL", AS_OF, api_key=None) is None


def test_get_next_earnings_date_picks_earliest_upcoming(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "get",
        _mock_response({"earningsCalendar": [{"date": "2026-08-15"}, {"date": "2026-07-25"}, {"date": "2026-05-01"}]}),
    )
    result = finnhub_client.get_next_earnings_date("AAPL", AS_OF, api_key="fake-key")
    assert result == date(2026, 7, 25)


def test_get_next_earnings_date_returns_none_on_http_error(monkeypatch):
    def _boom(*args, **kwargs):
        raise httpx.ConnectError("no network", request=httpx.Request("GET", "https://finnhub.io/api/v1/x"))

    monkeypatch.setattr(httpx, "get", _boom)
    assert finnhub_client.get_next_earnings_date("AAPL", AS_OF, api_key="fake-key") is None


def test_get_recent_news_without_api_key_returns_empty_list():
    assert finnhub_client.get_recent_news("AAPL", AS_OF, api_key=None) == []


def test_get_recent_news_sorts_by_datetime_desc(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "get",
        _mock_response(
            [
                {"headline": "old", "datetime": 100, "source": "S", "url": "u1", "summary": "s1"},
                {"headline": "new", "datetime": 200, "source": "S", "url": "u2", "summary": "s2"},
            ]
        ),
    )
    result = finnhub_client.get_recent_news("AAPL", AS_OF, api_key="fake-key")
    assert result[0]["headline"] == "new"
    assert result[1]["headline"] == "old"
    assert result[0]["published_at"] == datetime.fromtimestamp(200, tz=timezone.utc)


def test_get_recent_news_published_at_none_when_missing_datetime(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "get",
        _mock_response([{"headline": "no date", "source": "S", "url": "u1", "summary": "s1"}]),
    )
    result = finnhub_client.get_recent_news("AAPL", AS_OF, api_key="fake-key")
    assert result[0]["published_at"] is None


def test_get_recent_news_returns_empty_on_failure(monkeypatch):
    monkeypatch.setattr(httpx, "get", _mock_response({}, status_code=500))
    assert finnhub_client.get_recent_news("AAPL", AS_OF, api_key="fake-key") == []
