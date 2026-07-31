from __future__ import annotations

from datetime import datetime, timedelta, timezone

from options_advisor.broker.models import IntradayBar
from options_advisor.indicators.intraday import compute_vwap


def _bar(ts: datetime, high: float, low: float, close: float, volume: int) -> IntradayBar:
    return IntradayBar(symbol="TST", timestamp=ts, open=close, high=high, low=low, close=close, volume=volume)


def test_compute_vwap_empty_bars_returns_empty_list():
    assert compute_vwap([]) == []


def test_compute_vwap_single_bar_equals_its_typical_price():
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    bars = [_bar(start, high=102, low=98, close=100, volume=1000)]
    vwap = compute_vwap(bars)
    assert vwap == [round((102 + 98 + 100) / 3, 4)]


def test_compute_vwap_is_cumulative_not_a_moving_window():
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    bars = [
        _bar(start, high=101, low=99, close=100, volume=1000),
        _bar(start + timedelta(minutes=1), high=111, low=109, close=110, volume=1000),
    ]
    vwap = compute_vwap(bars)
    typical_1 = (101 + 99 + 100) / 3
    typical_2 = (111 + 109 + 110) / 3
    assert vwap[0] == round(typical_1, 4)
    assert vwap[1] == round((typical_1 * 1000 + typical_2 * 1000) / 2000, 4)


def test_compute_vwap_weights_by_volume():
    """Una barra con volumen 10x debe pesar 10x más en el acumulado — verificado con un caso
    donde el resultado NO sería el promedio simple de los precios típicos."""
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    bars = [
        _bar(start, high=101, low=99, close=100, volume=9000),  # típico 100, peso grande
        _bar(start + timedelta(minutes=1), high=201, low=199, close=200, volume=1000),  # típico 200, peso chico
    ]
    vwap = compute_vwap(bars)
    simple_average = (100 + 200) / 2
    assert vwap[1] < simple_average  # el volumen alto de la primera barra domina el resultado
    assert vwap[1] == round((100 * 9000 + 200 * 1000) / 10000, 4)


def test_compute_vwap_returns_series_aligned_with_bars():
    start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    bars = [_bar(start + timedelta(minutes=i), high=100 + i, low=98 + i, close=99 + i, volume=500) for i in range(5)]
    vwap = compute_vwap(bars)
    assert len(vwap) == len(bars)
    assert all(v is not None for v in vwap)
