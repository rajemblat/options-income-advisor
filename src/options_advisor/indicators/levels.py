from __future__ import annotations

import pandas as pd

from options_advisor.broker.models import PriceBar


def _pivot_levels(price_bars: list[PriceBar], order: int) -> list[float]:
    """Máximos/mínimos locales: `order` = cuántas barras a cada lado debe superar un pivote
    para contar como extremo local. Extraído de `find_support_resistance` para poder
    reusarlo también en `find_strong_support_resistance` (mismo criterio de detección de
    pivotes, distinto criterio de agrupamiento/filtrado después)."""
    if len(price_bars) < 2 * order + 1:
        return []
    highs = [b.high for b in price_bars]
    lows = [b.low for b in price_bars]
    n = len(price_bars)

    pivot_levels: list[float] = []
    for i in range(order, n - order):
        window_highs = highs[i - order : i + order + 1]
        if highs[i] == max(window_highs):
            pivot_levels.append(highs[i])
        window_lows = lows[i - order : i + order + 1]
        if lows[i] == min(window_lows):
            pivot_levels.append(lows[i])
    return pivot_levels


def find_support_resistance(
    price_bars: list[PriceBar],
    current_price: float,
    order: int = 3,
    cluster_pct: float = 0.01,
    max_levels: int = 3,
) -> tuple[list[float], list[float]]:
    """Identifica soportes y resistencias como máximos/mínimos locales agrupados
    (Sección 4.2 de la hoja de ruta). `order` = cuántas barras a cada lado debe
    superar un pivote para contar como extremo local.

    Devuelve (soportes, resistencias), cada uno ordenado por cercanía al precio actual.
    """
    pivot_levels = _pivot_levels(price_bars, order)
    if not pivot_levels:
        return [], []

    clustered = _cluster_levels(sorted(pivot_levels), cluster_pct)

    supports = sorted([lvl for lvl in clustered if lvl < current_price], reverse=True)[:max_levels]
    resistances = sorted([lvl for lvl in clustered if lvl > current_price])[:max_levels]
    return supports, resistances


def find_strong_support_resistance(
    price_bars: list[PriceBar],
    current_price: float,
    order: int = 3,
    cluster_pct: float = 0.01,
    max_levels: int = 5,
    min_touches: int = 2,
) -> tuple[list[float], list[float]]:
    """Igual que `find_support_resistance`, pero solo devuelve niveles "probados" — un cluster
    de pivotes agrupados que se tocó al menos `min_touches` veces, no un mínimo/máximo aislado
    que apareció una sola vez (Simulador de Trading Automático, criterio 1: "soporte fuerte").
    Usada tal cual sobre velas diarias, y sobre velas semanales vía
    `find_weekly_strong_support_resistance` — mismo criterio de "fuerza" en ambos timeframes."""
    pivot_levels = _pivot_levels(price_bars, order)
    if not pivot_levels:
        return [], []

    clustered = _cluster_levels_with_touches(sorted(pivot_levels), cluster_pct)
    strong = [lvl for lvl, touches in clustered if touches >= min_touches]

    supports = sorted([lvl for lvl in strong if lvl < current_price], reverse=True)[:max_levels]
    resistances = sorted([lvl for lvl in strong if lvl > current_price])[:max_levels]
    return supports, resistances


def resample_to_weekly(price_bars: list[PriceBar]) -> list[PriceBar]:
    """Agrega velas diarias a velas semanales (open=primera del período, high=máximo,
    low=mínimo, close=última, volume=suma) — base para evaluar soporte/resistencia en el
    gráfico semanal (Simulador de Trading Automático, criterio 1) sin necesitar una fuente de
    datos semanales separada del broker."""
    if not price_bars:
        return []
    symbol = price_bars[0].symbol
    df = pd.DataFrame(
        {
            "date": [pd.Timestamp(b.trade_date) for b in price_bars],
            "open": [b.open for b in price_bars],
            "high": [b.high for b in price_bars],
            "low": [b.low for b in price_bars],
            "close": [b.close for b in price_bars],
            "volume": [b.volume for b in price_bars],
        }
    ).sort_values("date").set_index("date")
    weekly = df.resample("W").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return [
        PriceBar(
            symbol=symbol,
            trade_date=idx.date(),
            open=round(float(row["open"]), 4),
            high=round(float(row["high"]), 4),
            low=round(float(row["low"]), 4),
            close=round(float(row["close"]), 4),
            volume=int(row["volume"]),
        )
        for idx, row in weekly.iterrows()
    ]


def find_weekly_strong_support_resistance(
    daily_price_bars: list[PriceBar],
    current_price: float,
    order: int = 2,
    cluster_pct: float = 0.02,
    max_levels: int = 5,
    min_touches: int = 2,
) -> tuple[list[float], list[float]]:
    """`find_strong_support_resistance` sobre velas semanales, resampleadas a partir de las
    diarias que ya trae el pipeline — evita pedirle al broker una fuente de datos semanal
    aparte. `order`/`cluster_pct` por defecto son más chicos/anchos que la versión diaria: hay
    muchas menos velas semanales en la misma ventana de historia, y cada una se mueve un rango
    más grande."""
    weekly_bars = resample_to_weekly(daily_price_bars)
    return find_strong_support_resistance(weekly_bars, current_price, order, cluster_pct, max_levels, min_touches)


def _cluster_levels(sorted_levels: list[float], cluster_pct: float) -> list[float]:
    return [lvl for lvl, _touches in _cluster_levels_with_touches(sorted_levels, cluster_pct)]


def _cluster_levels_with_touches(sorted_levels: list[float], cluster_pct: float) -> list[tuple[float, int]]:
    if not sorted_levels:
        return []
    clusters: list[list[float]] = [[sorted_levels[0]]]
    for level in sorted_levels[1:]:
        if abs(level - clusters[-1][-1]) / clusters[-1][-1] <= cluster_pct:
            clusters[-1].append(level)
        else:
            clusters.append([level])
    return [(round(sum(c) / len(c), 2), len(c)) for c in clusters]
