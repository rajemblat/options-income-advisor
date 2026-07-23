from __future__ import annotations

from datetime import date

import httpx

from options_advisor.market_context import economic_calendar

AS_OF = date(2026, 7, 1)


def test_without_api_key_falls_back_to_fomc_schedule():
    events = economic_calendar.get_upcoming_macro_events(api_key=None, as_of=AS_OF, lookahead_days=45)
    assert any("FOMC" in e["event"] for e in events)
    assert all(date.fromisoformat(e["date"]) >= AS_OF for e in events)


def test_finnhub_success_returns_filtered_us_events(monkeypatch):
    def _get(*args, **kwargs):
        request = httpx.Request("GET", "https://finnhub.io/api/v1/x")
        payload = {
            "economicCalendar": [
                {"date": "2026-07-15", "event": "CPI m/m", "country": "US", "impact": "high"},
                {"date": "2026-07-10", "event": "Retail sales revision (low impact)", "country": "US", "impact": "low"},
                {"date": "2026-07-12", "event": "German CPI", "country": "DE", "impact": "high"},
            ]
        }
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx, "get", _get)
    events = economic_calendar.get_upcoming_macro_events(api_key="fake-key", as_of=AS_OF, lookahead_days=30)
    assert len(events) == 1
    assert events[0]["event"] == "CPI m/m"


def test_falls_back_to_fomc_schedule_when_finnhub_fails(monkeypatch):
    def _boom(*args, **kwargs):
        raise httpx.ConnectError("no network", request=httpx.Request("GET", "https://finnhub.io/api/v1/x"))

    monkeypatch.setattr(httpx, "get", _boom)
    events = economic_calendar.get_upcoming_macro_events(api_key="fake-key", as_of=AS_OF, lookahead_days=45)
    assert any("FOMC" in e["event"] for e in events)


def test_falls_back_to_fomc_schedule_when_finnhub_returns_no_relevant_rows(monkeypatch):
    def _get(*args, **kwargs):
        request = httpx.Request("GET", "https://finnhub.io/api/v1/x")
        return httpx.Response(200, json={"economicCalendar": []}, request=request)

    monkeypatch.setattr(httpx, "get", _get)
    events = economic_calendar.get_upcoming_macro_events(api_key="fake-key", as_of=AS_OF, lookahead_days=45)
    assert any("FOMC" in e["event"] for e in events)
