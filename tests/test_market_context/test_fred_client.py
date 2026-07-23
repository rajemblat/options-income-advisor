from __future__ import annotations

from datetime import date

import httpx

from options_advisor.market_context import fred_client


def _mock_response(value: str):
    def _get(*args, **kwargs):
        request = httpx.Request("GET", "https://api.stlouisfed.org/x")
        return httpx.Response(200, json={"observations": [{"value": value}]}, request=request)

    return _get


def test_get_fed_funds_target_range_without_api_key_returns_none():
    assert fred_client.get_fed_funds_target_range(api_key=None) is None


def test_get_fed_funds_target_range_parses_upper_and_lower(monkeypatch):
    monkeypatch.setattr(httpx, "get", _mock_response("4.25"))
    result = fred_client.get_fed_funds_target_range(api_key="fake-key")
    assert result == (4.25, 4.25)  # mismo mock devuelve el mismo valor para ambas series


def test_get_fed_funds_target_range_missing_value_returns_none(monkeypatch):
    monkeypatch.setattr(httpx, "get", _mock_response("."))  # FRED usa "." para dato faltante
    assert fred_client.get_fed_funds_target_range(api_key="fake-key") is None


def test_get_macro_snapshot_without_api_key_has_all_none_values():
    snapshot = fred_client.get_macro_snapshot(api_key=None)
    assert snapshot == {"cpi_yoy_pct": None, "unemployment_rate_pct": None, "gdp_growth_annualized_pct": None}


def test_get_macro_snapshot_parses_values(monkeypatch):
    monkeypatch.setattr(httpx, "get", _mock_response("3.1"))
    snapshot = fred_client.get_macro_snapshot(api_key="fake-key")
    assert snapshot["cpi_yoy_pct"] == 3.1
    assert snapshot["unemployment_rate_pct"] == 3.1
    assert snapshot["gdp_growth_annualized_pct"] == 3.1


def test_get_macro_snapshot_returns_none_on_failure(monkeypatch):
    def _boom(*args, **kwargs):
        raise httpx.ConnectError("no network", request=httpx.Request("GET", "https://api.stlouisfed.org/x"))

    monkeypatch.setattr(httpx, "get", _boom)
    snapshot = fred_client.get_macro_snapshot(api_key="fake-key")
    assert snapshot == {"cpi_yoy_pct": None, "unemployment_rate_pct": None, "gdp_growth_annualized_pct": None}


def test_get_upcoming_release_dates_without_api_key_returns_empty_list():
    assert fred_client.get_upcoming_release_dates(api_key=None, as_of=date(2026, 7, 23)) == []


def test_get_upcoming_release_dates_returns_cpi_employment_and_gdp(monkeypatch):
    def _get(url, params, timeout):
        request = httpx.Request("GET", url)
        dates_by_release = {10: "2026-08-12", 50: "2026-08-07", 53: "2026-07-30"}
        return httpx.Response(200, json={"release_dates": [{"date": dates_by_release[params["release_id"]]}]}, request=request)

    monkeypatch.setattr(httpx, "get", _get)
    events = fred_client.get_upcoming_release_dates(api_key="fake-key", as_of=date(2026, 7, 23), lookahead_days=30)

    assert len(events) == 3
    by_event = {e["event"]: e for e in events}
    assert by_event["Publicación de CPI (inflación)"]["date"] == "2026-08-12"
    assert by_event["Publicación de CPI (inflación)"]["impact"] == "high"
    assert by_event["Reporte de empleo (Nonfarm Payrolls)"]["date"] == "2026-08-07"
    assert by_event["Publicación de PBI (GDP)"]["impact"] == "medium"


def test_get_upcoming_release_dates_excludes_dates_outside_window(monkeypatch):
    def _get(url, params, timeout):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"release_dates": [{"date": "2026-12-31"}]}, request=request)

    monkeypatch.setattr(httpx, "get", _get)
    events = fred_client.get_upcoming_release_dates(api_key="fake-key", as_of=date(2026, 7, 23), lookahead_days=30)
    assert events == []


def test_get_upcoming_release_dates_one_release_failing_does_not_block_others(monkeypatch):
    def _get(url, params, timeout):
        if params["release_id"] == 10:
            raise httpx.ConnectError("no network", request=httpx.Request("GET", url))
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"release_dates": [{"date": "2026-08-07"}]}, request=request)

    monkeypatch.setattr(httpx, "get", _get)
    events = fred_client.get_upcoming_release_dates(api_key="fake-key", as_of=date(2026, 7, 23), lookahead_days=30)
    assert len(events) == 2  # empleo y PBI sobreviven, CPI falló
