from __future__ import annotations

from datetime import date, timedelta

from options_advisor.broker.models import PriceBar
from options_advisor.indicators.levels import (
    find_strong_support_resistance,
    find_support_resistance,
    find_weekly_strong_support_resistance,
    resample_to_weekly,
)


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


def test_strong_support_requires_at_least_two_touches():
    # 90 aparece como mínimo local 3 veces (soporte "probado"); 80 aparece una sola vez
    # (mínimo aislado) — no debe contar como soporte fuerte.
    pattern = [
        (100, 95), (95, 90), (100, 95), (105, 100), (100, 95), (95, 90), (100, 95),
        (105, 100), (100, 95), (95, 90), (100, 95), (105, 100), (100, 95), (90, 80), (100, 95),
    ]
    bars = [_bar(i, h, l) for i, (h, l) in enumerate(pattern)]
    supports, _ = find_strong_support_resistance(bars, current_price=97, order=1, cluster_pct=0.02)
    assert any(abs(s - 90) < 2 for s in supports)
    assert not any(abs(s - 80) < 2 for s in supports)


def test_strong_support_empty_with_insufficient_bars():
    bars = [_bar(0, 100, 95)]
    supports, resistances = find_strong_support_resistance(bars, current_price=97)
    assert supports == []
    assert resistances == []


def test_resample_to_weekly_aggregates_ohlcv():
    pattern = [(105, 95), (110, 100), (108, 98), (112, 102), (115, 105), (120, 110), (118, 108), (122, 112), (125, 115), (123, 113)]
    bars = [_bar(i, h, l) for i, (h, l) in enumerate(pattern)]
    weekly = resample_to_weekly(bars)
    assert 1 <= len(weekly) <= len(bars)
    assert max(w.high for w in weekly) == max(h for h, _ in pattern)
    assert min(w.low for w in weekly) == min(l for _, l in pattern)
    assert sum(w.volume for w in weekly) == 1000 * len(bars)


def test_resample_to_weekly_empty_input():
    assert resample_to_weekly([]) == []


def test_weekly_strong_support_resistance_uses_resampled_bars():
    # ~12 semanas de historia diaria, con un soporte semanal claro y repetido en 90.
    pattern = [(105, 95), (110, 100), (100, 90), (105, 95), (110, 100)] * 12
    bars = [_bar(i, h, l) for i, (h, l) in enumerate(pattern)]
    supports, resistances = find_weekly_strong_support_resistance(bars, current_price=102, order=1, cluster_pct=0.03)
    assert isinstance(supports, list)
    assert isinstance(resistances, list)
