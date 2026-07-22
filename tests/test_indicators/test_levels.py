from __future__ import annotations

from datetime import date, timedelta

from options_advisor.broker.models import PriceBar
from options_advisor.indicators.levels import find_support_resistance


def _bar(day_offset: int, high: float, low: float) -> PriceBar:
    return PriceBar(
        symbol="TEST",
        trade_date=date(2026, 1, 1) + timedelta(days=day_offset),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
        volume=1000,
    )


def test_finds_a_clear_support_and_resistance():
    # Precio rebota repetidamente en 95 (soporte) y se frena en 105 (resistencia)
    pattern = [(100, 90), (105, 100), (100, 90), (95, 90), (100, 95), (105, 100), (100, 90), (95, 90)]
    bars = [_bar(i, h, l) for i, (h, l) in enumerate(pattern)]
    supports, resistances = find_support_resistance(bars, current_price=97, order=1, cluster_pct=0.02)
    assert any(abs(s - 90) < 3 for s in supports) or any(abs(s - 95) < 3 for s in supports)


def test_insufficient_bars_returns_empty():
    bars = [_bar(0, 100, 95)]
    supports, resistances = find_support_resistance(bars, current_price=97)
    assert supports == []
    assert resistances == []
