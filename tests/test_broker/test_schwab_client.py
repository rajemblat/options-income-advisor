from __future__ import annotations

from datetime import date

import httpx
import pytest

from options_advisor.broker.schwab_client import SchwabBrokerClient

TODAY = date.today()


class _FakeAuth:
    def get_valid_access_token(self) -> str:
        return "fake-token"


@pytest.fixture
def client():
    return SchwabBrokerClient(auth=_FakeAuth(), risk_free_rate=0.045)


def _mock_get(json_payload):
    def _get(self, path, params=None, headers=None):
        request = httpx.Request("GET", f"https://api.schwabapi.com/marketdata/v1{path}")
        return httpx.Response(200, json=json_payload, request=request)

    return _get


def _raw_contract(**overrides):
    base = {
        "bid": 11.65,
        "ask": 12.15,
        "last": 12.0,
        "volatility": 28.36,
        "delta": 0.537,
        "gamma": 0.016,
        "theta": -0.172,
        "vega": 0.359,
        "rho": 0.122,
        "openInterest": 25201,
        "totalVolume": 5176,
        "strikePrice": 320.0,
        "expirationDate": "2026-08-21T20:00:00.000+00:00",
    }
    base.update(overrides)
    return base


def test_get_quote_parses_real_fields(client, monkeypatch):
    payload = {"AAPL": {"quote": {"lastPrice": 321.1, "bidPrice": 321.1, "askPrice": 321.48}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quote = client.get_quote("AAPL")
    assert quote.last_price == 321.1
    assert quote.bid == 321.1
    assert quote.ask == 321.48


def test_get_price_history_sorts_and_truncates_to_lookback(client, monkeypatch):
    candles = [
        {"datetime": 1700000000000 + i * 86_400_000, "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i, "volume": 1000}
        for i in range(10)
    ]
    monkeypatch.setattr(httpx.Client, "get", _mock_get({"candles": list(reversed(candles))}))
    bars = client.get_price_history("AAPL", lookback_days=3)
    assert len(bars) == 3
    assert bars[0].trade_date < bars[1].trade_date < bars[2].trade_date


def test_option_chain_uses_broker_greeks_when_present(client, monkeypatch):
    payload = {
        "underlyingPrice": 321.1,
        "interestRate": 4.5,
        "dividendYield": 0.5,
        "callExpDateMap": {"2026-08-21:29": {"320.0": [_raw_contract()]}},
        "putExpDateMap": {},
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    chain = client.get_option_chain("AAPL")
    assert len(chain.contracts) == 1
    contract = chain.contracts[0]
    assert contract.greeks.source == "broker"
    assert contract.greeks.delta == 0.537
    assert contract.open_interest == 25201
    assert contract.volume == 5176
    assert contract.implied_volatility == pytest.approx(0.2836)


def test_option_chain_falls_back_to_calculated_greeks_using_live_rate_and_dividend(client, monkeypatch):
    raw = _raw_contract(delta=None, gamma=None, theta=None, vega=None, rho=None)
    payload = {
        "underlyingPrice": 321.1,
        "interestRate": 4.5,
        "dividendYield": 0.5,
        "callExpDateMap": {"2026-08-21:29": {"320.0": [raw]}},
        "putExpDateMap": {},
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    chain = client.get_option_chain("AAPL")
    contract = chain.contracts[0]
    assert contract.greeks.source == "calculated"
    # Sanity: un fallback calculado para un contrato ~ATM da delta razonable (no 0, no 1)
    assert 0.3 < contract.greeks.delta < 0.7


def test_option_chain_falls_back_to_configured_rate_when_schwab_rate_missing(client, monkeypatch):
    """Sin interestRate de Schwab (0 o ausente), usa self.risk_free_rate — mismo comportamiento
    que antes de threadear la tasa en vivo."""
    raw = _raw_contract(delta=None, gamma=None, theta=None, vega=None, rho=None)
    payload_missing_rate = {
        "underlyingPrice": 321.1,
        "callExpDateMap": {"2026-08-21:29": {"320.0": [raw]}},
        "putExpDateMap": {},
    }
    payload_zero_rate = {**payload_missing_rate, "interestRate": 0, "dividendYield": 0}

    for payload in (payload_missing_rate, payload_zero_rate):
        monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
        chain = client.get_option_chain("AAPL")
        assert chain.contracts[0].greeks.source == "calculated"  # no lanza pese a faltar interestRate/dividendYield
