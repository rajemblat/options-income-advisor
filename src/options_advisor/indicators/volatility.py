from __future__ import annotations

import math
from datetime import date
from typing import NamedTuple

from options_advisor.broker.models import PriceBar

IvRankSource = str  # "implied_volatility" | "historical_volatility_proxy"


class IvRankResult(NamedTuple):
    iv_rank: float | None
    source: IvRankSource
    sessions_available: int


def compute_historical_volatility(price_bars: list[PriceBar], window_days: int = 20) -> float | None:
    """Volatilidad histórica realizada anualizada (desviación estándar de retornos log diarios)."""
    if len(price_bars) < window_days + 1:
        return None
    closes = [b.close for b in price_bars[-(window_days + 1) :]]
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_std = math.sqrt(variance)
    return round(daily_std * math.sqrt(252), 4)


def _historical_volatility_series(price_bars: list[PriceBar], window_days: int) -> list[float]:
    """Serie de HV rolling, un valor por cada día con suficiente historia previa —
    usada como proxy del rango histórico de IV mientras no hay 12 meses de IV real."""
    series = []
    for i in range(window_days, len(price_bars)):
        window = price_bars[i - window_days : i + 1]
        hv = compute_historical_volatility(window, window_days)
        if hv is not None:
            series.append(hv)
    return series


def _rank_percentile(current_value: float, series: list[float]) -> float:
    lo, hi = min(series), max(series)
    if hi - lo < 1e-9:
        return 50.0
    return round(max(0.0, min(100.0, (current_value - lo) / (hi - lo) * 100)), 2)


def compute_iv_rank(
    iv_snapshot_history: list[tuple[date, float]],
    current_iv: float | None,
    price_bars: list[PriceBar],
    min_sessions_for_real_iv: int = 20,
    full_window_sessions: int = 252,
    hv_window_days: int = 20,
) -> IvRankResult:
    """IV Rank con estrategia de bootstrap de 3 capas (Sección 4 del plan de Fase 1):

    1. Menos de `min_sessions_for_real_iv` snapshots de IV propia acumulados → usa HV
       (volatilidad histórica realizada) como proxy, calculada sobre price_history retroactivo.
    2. Entre el mínimo y `full_window_sessions` → IV Rank real, sobre la ventana disponible.
    3. `full_window_sessions` o más (~12 meses) → IV Rank real de 12 meses completos.
    """
    sessions_available = len(iv_snapshot_history)

    if sessions_available < min_sessions_for_real_iv:
        hv_series = _historical_volatility_series(price_bars, hv_window_days)
        current_hv = compute_historical_volatility(price_bars, hv_window_days)
        if current_hv is None or len(hv_series) < 2:
            return IvRankResult(iv_rank=None, source="historical_volatility_proxy", sessions_available=sessions_available)
        return IvRankResult(
            iv_rank=_rank_percentile(current_hv, hv_series),
            source="historical_volatility_proxy",
            sessions_available=sessions_available,
        )

    window = iv_snapshot_history[-full_window_sessions:]
    values = [v for _, v in window]
    reference_value = current_iv if current_iv is not None else values[-1]
    return IvRankResult(
        iv_rank=_rank_percentile(reference_value, values),
        source="implied_volatility",
        sessions_available=sessions_available,
    )
