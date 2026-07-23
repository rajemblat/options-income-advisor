from __future__ import annotations

from datetime import date

import httpx
import pytest

from options_advisor.market_context import kalshi_client

_EVENTS_RESPONSE = {
    "events": [
        {"event_ticker": "KXFED-26SEP", "strike_date": "2026-09-16T18:00:00Z"},
        {"event_ticker": "KXFED-26JUL", "strike_date": "2026-07-29T18:00:00Z"},  # la más próxima
    ]
}

# Réplica simplificada de la ladder real observada en vivo: la Fed probablemente mantiene en
# 3.75% (upper vigente), con algo de probabilidad de suba.
_MARKETS_RESPONSE = {
    "markets": [
        {"floor_strike": 3.50, "yes_bid_dollars": "0.99", "yes_ask_dollars": "1.00"},
        {"floor_strike": 3.75, "yes_bid_dollars": "0.25", "yes_ask_dollars": "0.28"},
        {"floor_strike": 4.00, "yes_bid_dollars": "0.01", "yes_ask_dollars": "0.02"},
    ]
}


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        request = httpx.Request("GET", url, params=params)
        if url.endswith("/events"):
            return httpx.Response(200, json=_EVENTS_RESPONSE, request=request)
        return httpx.Response(200, json=_MARKETS_RESPONSE, request=request)


def test_get_fed_decision_probabilities_picks_nearest_event_and_computes_buckets(monkeypatch):
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    result = kalshi_client.get_fed_decision_probabilities(current_upper_rate=3.75)

    assert result is not None
    assert result.event_ticker == "KXFED-26JUL"
    assert result.meeting_date == date(2026, 7, 29)
    # hold = franja (3.50, 3.75] = 0.995 - 0.265 = 0.73
    assert result.hold_probability == pytest.approx(0.73, abs=0.001)
    # hike = todo lo que termina por encima de 3.75 = 0.265
    assert result.hike_probability == pytest.approx(0.265, abs=0.001)
    assert result.cut_probability == pytest.approx(0.005, abs=0.001)
    total = result.hike_probability + result.hold_probability + result.cut_probability
    assert total == pytest.approx(1.0, abs=0.001)


def test_get_fed_decision_probabilities_returns_none_when_no_open_events(monkeypatch):
    class _EmptyClient(_FakeClient):
        def get(self, url, params=None):
            request = httpx.Request("GET", url, params=params)
            return httpx.Response(200, json={"events": []}, request=request)

    monkeypatch.setattr(httpx, "Client", _EmptyClient)
    assert kalshi_client.get_fed_decision_probabilities(current_upper_rate=3.75) is None


def test_get_fed_decision_probabilities_returns_none_on_network_failure(monkeypatch):
    class _BoomClient(_FakeClient):
        def get(self, url, params=None):
            raise httpx.ConnectError("no network", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "Client", _BoomClient)
    assert kalshi_client.get_fed_decision_probabilities(current_upper_rate=3.75) is None
