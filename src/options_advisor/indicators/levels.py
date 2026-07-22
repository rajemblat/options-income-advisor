from __future__ import annotations

from options_advisor.broker.models import PriceBar


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
    if len(price_bars) < 2 * order + 1:
        return [], []

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

    clustered = _cluster_levels(sorted(pivot_levels), cluster_pct)

    supports = sorted([lvl for lvl in clustered if lvl < current_price], reverse=True)[:max_levels]
    resistances = sorted([lvl for lvl in clustered if lvl > current_price])[:max_levels]
    return supports, resistances


def _cluster_levels(sorted_levels: list[float], cluster_pct: float) -> list[float]:
    if not sorted_levels:
        return []
    clusters: list[list[float]] = [[sorted_levels[0]]]
    for level in sorted_levels[1:]:
        if abs(level - clusters[-1][-1]) / clusters[-1][-1] <= cluster_pct:
            clusters[-1].append(level)
        else:
            clusters.append([level])
    return [round(sum(c) / len(c), 2) for c in clusters]
