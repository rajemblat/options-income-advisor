from __future__ import annotations

import pandas as pd

from options_advisor.broker.models import PriceBar


def _to_frame(price_bars: list[PriceBar]) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "date": [b.trade_date for b in price_bars],
            "open": [b.open for b in price_bars],
            "high": [b.high for b in price_bars],
            "low": [b.low for b in price_bars],
            "close": [b.close for b in price_bars],
        }
    ).sort_values("date")
    return df.set_index("date")


def compute_rsi(price_bars: list[PriceBar], period: int = 14) -> float | None:
    """RSI de Wilder. Devuelve None si no hay suficientes barras."""
    df = _to_frame(price_bars)
    if len(df) < period + 1:
        return None
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    rsi = 100 - (100 / (1 + rs))
    value = rsi.iloc[-1]
    return None if pd.isna(value) else round(float(value), 2)


def compute_atr(price_bars: list[PriceBar], period: int = 14) -> float | None:
    """Average True Range (suavizado de Wilder)."""
    df = _to_frame(price_bars)
    if len(df) < period + 1:
        return None
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    value = atr.iloc[-1]
    return None if pd.isna(value) else round(float(value), 2)


def compute_sma(price_bars: list[PriceBar], period: int) -> float | None:
    df = _to_frame(price_bars)
    if len(df) < period:
        return None
    value = df["close"].rolling(period).mean().iloc[-1]
    return None if pd.isna(value) else round(float(value), 2)


def detect_ma_cross(price_bars: list[PriceBar], short_period: int, long_period: int) -> str | None:
    """Detecta si la media corta cruzó a la larga en la barra más reciente.
    Devuelve p.ej. 'golden_cross_8_20' / 'death_cross_8_20', o None si no hubo cruce hoy."""
    df = _to_frame(price_bars)
    if len(df) < long_period + 1:
        return None
    short_sma = df["close"].rolling(short_period).mean()
    long_sma = df["close"].rolling(long_period).mean()
    diff = short_sma - long_sma
    if diff.iloc[-2:].isna().any():
        return None
    prev_diff, curr_diff = diff.iloc[-2], diff.iloc[-1]
    label_suffix = f"{short_period}_{long_period}"
    if prev_diff <= 0 < curr_diff:
        return f"golden_cross_{label_suffix}"
    if prev_diff >= 0 > curr_diff:
        return f"death_cross_{label_suffix}"
    return None
