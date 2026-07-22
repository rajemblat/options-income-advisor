from __future__ import annotations

import math
import random
from datetime import date, timedelta

from options_advisor.indicators.volatility import compute_historical_volatility, compute_iv_rank


def _random_walk_closes(n: int, seed: int = 42) -> list[float]:
    rng = random.Random(seed)
    price = 100.0
    closes = [price]
    for _ in range(n - 1):
        price *= math.exp(rng.gauss(0, 0.01))
        closes.append(price)
    return closes


def test_historical_volatility_insufficient_data_returns_none(price_bars_factory):
    assert compute_historical_volatility(price_bars_factory([100, 101]), window_days=20) is None


def test_historical_volatility_is_positive_and_annualized(price_bars_factory):
    bars = price_bars_factory(_random_walk_closes(40))
    hv = compute_historical_volatility(bars, window_days=20)
    assert hv is not None
    assert 0.0 < hv < 2.0  # rango razonable para vol anualizada de un random walk de baja vol


def test_iv_rank_uses_hv_proxy_below_min_sessions(price_bars_factory):
    bars = price_bars_factory(_random_walk_closes(60))
    result = compute_iv_rank(
        iv_snapshot_history=[],  # sin snapshots de IV propia acumulados todavía
        current_iv=None,
        price_bars=bars,
        min_sessions_for_real_iv=20,
        full_window_sessions=252,
        hv_window_days=20,
    )
    assert result.source == "historical_volatility_proxy"
    assert result.sessions_available == 0
    assert result.iv_rank is not None
    assert 0 <= result.iv_rank <= 100


def test_iv_rank_uses_real_iv_once_min_sessions_reached(price_bars_factory):
    start = date(2026, 1, 1)
    history = [(start + timedelta(days=i), 0.10 + 0.20 * (i / 30)) for i in range(30)]  # IV subiendo de 0.10 a 0.30
    bars = price_bars_factory(_random_walk_closes(60))

    result = compute_iv_rank(
        iv_snapshot_history=history,
        current_iv=0.30,  # el valor más alto de la serie
        price_bars=bars,
        min_sessions_for_real_iv=20,
        full_window_sessions=252,
        hv_window_days=20,
    )
    assert result.source == "implied_volatility"
    assert result.sessions_available == 30
    assert result.iv_rank == 100.0  # el IV actual es el máximo del historial disponible


def test_iv_rank_flat_history_returns_neutral_50(price_bars_factory):
    start = date(2026, 1, 1)
    history = [(start + timedelta(days=i), 0.20) for i in range(30)]  # IV constante, sin rango
    bars = price_bars_factory(_random_walk_closes(60))

    result = compute_iv_rank(
        iv_snapshot_history=history,
        current_iv=0.20,
        price_bars=bars,
        min_sessions_for_real_iv=20,
        full_window_sessions=252,
        hv_window_days=20,
    )
    assert result.iv_rank == 50.0
