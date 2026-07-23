from __future__ import annotations

import math

DELTA_NEUTRAL_BAND = 0.05  # |delta neto de la posición| por debajo de esto se cuenta como neutral


def summarize_portfolio(rows: list[dict]) -> dict:
    """Agrega las alertas de un día en: distribución por estrategia, exposición direccional
    (a partir del delta neto ya calculado por cada candidato en strategy/candidates.py) y
    riesgo total si todas se ejecutaran (suma de max_loss, separando estrategias de riesgo
    no acotado — no tiene sentido sumarlas a un total finito)."""
    by_strategy: dict[str, int] = {}
    bullish = bearish = neutral = 0
    delta_known = 0
    net_delta = 0.0
    bounded_risk_total = 0.0
    risk_known = 0
    unbounded_risk_count = 0

    for row in rows:
        strategy = row.get("strategy_type") or "desconocida"
        by_strategy[strategy] = by_strategy.get(strategy, 0) + 1

        delta = row.get("delta")
        if delta is not None:
            delta_known += 1
            net_delta += delta
            if delta > DELTA_NEUTRAL_BAND:
                bullish += 1
            elif delta < -DELTA_NEUTRAL_BAND:
                bearish += 1
            else:
                neutral += 1

        max_loss = row.get("max_loss")
        if max_loss is not None:
            risk_known += 1
            if math.isinf(max_loss):
                unbounded_risk_count += 1
            else:
                bounded_risk_total += max_loss

    total = len(rows)
    return {
        "total_alerts": total,
        "by_strategy": by_strategy,
        "directional": {
            "bullish": bullish,
            "bearish": bearish,
            "neutral": neutral,
            "unknown": total - delta_known,
        },
        "net_delta": net_delta if delta_known else None,
        "bounded_risk_total": bounded_risk_total if risk_known else None,
        "unbounded_risk_count": unbounded_risk_count,
    }
