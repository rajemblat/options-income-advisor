from __future__ import annotations

from options_advisor.strategy import constants as c
from options_advisor.strategy.profiles import allowed_strategies


def select_candidate_strategies(
    iv_rank: float | None,
    risk_level: str,
    has_open_assigned_position: bool = False,
) -> list[str]:
    """Matriz de selección de estrategia para el escenario Ingreso a Largo Plazo
    (Sección 5 de la hoja de ruta), filtrada por lo que el perfil de riesgo tiene habilitado.

    Devuelve una lista de candidatos a evaluar — no una única estrategia — porque el
    puntaje de convicción (Sección 6.1) es quien decide después cuál, si alguna, alertar.
    """
    if iv_rank is None:
        return []  # sin IV Rank calculable todavía, no hay base para decidir (Sección 4, gap conocido)

    allowed = allowed_strategies(risk_level)
    candidates: list[str] = []

    if iv_rank >= c.IV_RANK_HIGH_THRESHOLD:
        candidates += [c.CASH_SECURED_PUT, c.BULL_PUT_SPREAD, c.IRON_CONDOR, c.SHORT_PUT_NAKED]
    else:
        candidates += [c.CALENDAR_PUT_SPREAD, c.DIAGONAL_PUT_SPREAD]

    if has_open_assigned_position:
        candidates.append(c.COVERED_CALL)

    return [s for s in candidates if s in allowed]
