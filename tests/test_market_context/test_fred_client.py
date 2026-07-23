from __future__ import annotations

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
