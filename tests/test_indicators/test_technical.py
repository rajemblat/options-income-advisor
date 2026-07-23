from __future__ import annotations

from datetime import date, timedelta

from options_advisor.broker.models import PriceBar
from options_advisor.indicators.technical import compute_atr, compute_rsi, compute_sma, compute_stddev, detect_ma_cross


def test_rsi_trending_up_is_high(price_bars_factory):
    closes = [100 + i for i in range(30)]  # sube todos los días
    rsi = compute_rsi(price_bars_factory(closes))
    assert rsi > 90


def test_rsi_trending_down_is_low(price_bars_factory):
    closes = [130 - i for i in range(30)]  # baja todos los días
    rsi = compute_rsi(price_bars_factory(closes))
    assert rsi < 10


def test_rsi_insufficient_data_returns_none(price_bars_factory):
    assert compute_rsi(price_bars_factory([100, 101, 102])) is None


def test_atr_converges_to_constant_true_range():
    start = date(2026, 1, 1)
    bars = [
        PriceBar(symbol="TEST", trade_date=start + timedelta(days=i), open=100, high=105, low=95, close=100, volume=1000)
        for i in range(20)
    ]
    atr = compute_atr(bars)
    assert 9.5 <= atr <= 10.5  # true range = 10 todos los días (high-low), debería converger cerca de 10


def test_sma_matches_simple_average(price_bars_factory):
    closes = [10, 20, 30, 40, 50]
    sma = compute_sma(price_bars_factory(closes), period=5)
    assert sma == 30.0


def test_sma_insufficient_data_returns_none(price_bars_factory):
    assert compute_sma(price_bars_factory([10, 20]), period=5) is None


def test_detect_golden_cross(price_bars_factory):
    # precio plano y después una subida fuerte que en algún punto hace que SMA8 cruce por
    # encima de SMA20 — se chequea día a día porque el cruce exacto puede no caer en el último día.
    closes = [100] * 20 + [105 + 5 * i for i in range(15)]
    bars = price_bars_factory(closes)
    signals = {detect_ma_cross(bars[: i + 1], short_period=8, long_period=20) for i in range(20, len(bars))}
    assert "golden_cross_8_20" in signals


def test_detect_no_cross_when_flat(price_bars_factory):
    closes = [100] * 30
    assert detect_ma_cross(price_bars_factory(closes), short_period=8, long_period=20) is None


def test_stddev_zero_for_constant_price(price_bars_factory):
    closes = [100] * 25
    assert compute_stddev(price_bars_factory(closes), period=20) == 0.0


def test_stddev_positive_for_varying_price(price_bars_factory):
    closes = [100, 105, 95, 110, 90] * 5
    stddev = compute_stddev(price_bars_factory(closes), period=20)
    assert stddev > 0


def test_stddev_insufficient_data_returns_none(price_bars_factory):
    assert compute_stddev(price_bars_factory([100, 101, 102]), period=20) is None
