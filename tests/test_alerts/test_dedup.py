from __future__ import annotations

from datetime import date

from options_advisor.alerts.dedup import build_dedup_key


def test_same_inputs_produce_same_key():
    key1 = build_dedup_key("AAPL", "cash_secured_put", date(2026, 8, 15), {"short_strike": 195.0}, date(2026, 7, 22))
    key2 = build_dedup_key("AAPL", "cash_secured_put", date(2026, 8, 15), {"short_strike": 195.0}, date(2026, 7, 22))
    assert key1 == key2


def test_different_strikes_produce_different_keys():
    key1 = build_dedup_key("AAPL", "cash_secured_put", date(2026, 8, 15), {"short_strike": 195.0}, date(2026, 7, 22))
    key2 = build_dedup_key("AAPL", "cash_secured_put", date(2026, 8, 15), {"short_strike": 190.0}, date(2026, 7, 22))
    assert key1 != key2


def test_different_day_produces_different_key():
    key1 = build_dedup_key("AAPL", "cash_secured_put", date(2026, 8, 15), {"short_strike": 195.0}, date(2026, 7, 22))
    key2 = build_dedup_key("AAPL", "cash_secured_put", date(2026, 8, 15), {"short_strike": 195.0}, date(2026, 7, 23))
    assert key1 != key2


def test_key_is_order_independent_for_strikes_dict():
    key1 = build_dedup_key("AAPL", "iron_condor", date(2026, 8, 15), {"a": 1, "b": 2}, date(2026, 7, 22))
    key2 = build_dedup_key("AAPL", "iron_condor", date(2026, 8, 15), {"b": 2, "a": 1}, date(2026, 7, 22))
    assert key1 == key2
