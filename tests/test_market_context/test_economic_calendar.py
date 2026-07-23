from __future__ import annotations

from datetime import date

import httpx

from options_advisor.market_context import economic_calendar

AS_OF = date(2026, 7, 1)


def test_without_any_api_key_falls_back_to_fomc_schedule():
    events = economic_calendar.get_upcoming_macro_events(finnhub_api_key=None, fred_api_key=None, as_of=AS_OF, lookahead_days=45)
    assert any("FOMC" in e["event"] for e in events)
    assert all(date.fromisoformat(e["date"]) >= AS_OF for e in events)


def test_finnhub_success_returns_us_events_including_low_impact(monkeypatch):
    """Impacto 'low' se incluye (Sección 'Eventos de riesgo' #1: panorama completo, aunque con
    menor prioridad visual); solo país != US se filtra."""

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
    events = economic_calendar.get_upcoming_macro_events(finnhub_api_key="fake-key", fred_api_key=None, as_of=AS_OF, lookahead_days=30)
    labels = {e["event"] for e in events}
    assert labels == {"CPI m/m", "Retail sales revision (low impact)"}


def test_falls_back_when_finnhub_fails(monkeypatch):
    def _boom(*args, **kwargs):
        raise httpx.ConnectError("no network", request=httpx.Request("GET", "https://finnhub.io/api/v1/x"))

    monkeypatch.setattr(httpx, "get", _boom)
    events = economic_calendar.get_upcoming_macro_events(finnhub_api_key="fake-key", fred_api_key=None, as_of=AS_OF, lookahead_days=45)
    assert any("FOMC" in e["event"] for e in events)


def test_falls_back_when_finnhub_returns_no_relevant_rows(monkeypatch):
    def _get(*args, **kwargs):
        request = httpx.Request("GET", "https://finnhub.io/api/v1/x")
        return httpx.Response(200, json={"economicCalendar": []}, request=request)

    monkeypatch.setattr(httpx, "get", _get)
    events = economic_calendar.get_upcoming_macro_events(finnhub_api_key="fake-key", fred_api_key=None, as_of=AS_OF, lookahead_days=45)
    assert any("FOMC" in e["event"] for e in events)


def test_fallback_merges_fomc_and_fred_release_dates(monkeypatch):
    """El caso real de hoy: Finnhub /calendar/economic da 403 en el plan free (confirmado con
    la key real), así que el fallback tiene que traer CPI/empleo/PBI desde FRED, no solo FOMC."""

    def _get(*args, **kwargs):
        url = args[0] if args else kwargs.get("url", "")
        request = httpx.Request("GET", url)
        if "stlouisfed" in url:
            release_id = kwargs["params"]["release_id"]
            dates_by_release = {10: "2026-07-14", 50: "2026-07-08", 53: "2026-07-30"}
            return httpx.Response(200, json={"release_dates": [{"date": dates_by_release[release_id]}]}, request=request)
        return httpx.Response(403, json={"error": "no access"}, request=request)

    monkeypatch.setattr(httpx, "get", _get)
    events = economic_calendar.get_upcoming_macro_events(finnhub_api_key="fake-key", fred_api_key="fake-fred-key", as_of=AS_OF, lookahead_days=45)

    labels = {e["event"] for e in events}
    assert any("FOMC" in label for label in labels)
    assert any("CPI" in label for label in labels)
    assert any("empleo" in label for label in labels)
    assert any("PBI" in label for label in labels)
    assert [e["date"] for e in events] == sorted(e["date"] for e in events)


def test_fallback_works_without_fred_key_using_only_fomc():
    events = economic_calendar.get_upcoming_macro_events(finnhub_api_key=None, fred_api_key=None, as_of=AS_OF, lookahead_days=45)
    assert all("FOMC" in e["event"] for e in events)
