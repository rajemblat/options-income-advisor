from __future__ import annotations

from options_advisor.strategy import constants as c
from options_advisor.strategy.profiles import allowed_strategies


def _directional_bias(ma_cross_signal: str | None, rsi: float | None) -> str:
    """Sesgo direccional simple a partir de señales que el motor ya calcula (Sección 5): un
    cruce de medias manda primero; sin cruce, RSI en zona de sobrecompra/sobreventa lo suple.
    Sin ninguna de las dos, "neutral" — no se inventa un indicador nuevo para esto."""
    if ma_cross_signal:
        if "golden_cross" in ma_cross_signal:
            return "bullish"
        if "death_cross" in ma_cross_signal:
            return "bearish"
    if rsi is not None:
        if rsi <= c.RSI_OVERSOLD:
            return "bullish"
        if rsi >= c.RSI_OVERBOUGHT:
            return "bearish"
    return "neutral"


def select_candidate_strategies(
    iv_rank: float | None,
    risk_level: str,
    ma_cross_signal: str | None = None,
    rsi: float | None = None,
    has_open_assigned_position: bool = False,
) -> list[str]:
    """Matriz de selección de estrategia para el escenario Ingreso a Largo Plazo, filtrada por
    lo que el perfil de riesgo tiene habilitado.

    Regla (Sección 5): IV Rank alto → estrategias de crédito neto (venden prima cara); IV Rank
    bajo → calendar/diagonal + debit spreads (compran optionalidad barata). Dentro de cada
    régimen, el sesgo direccional (`_directional_bias`) decide qué lado (puts/calls) ofrecer;
    "neutral" ofrece las estrategias sin sesgo (Iron Condor, los Condors direccionales,
    ambos calendarios). Covered Call y Collar solo aparecen con una posición ya asignada.

    Devuelve una lista de candidatos a evaluar — no una única estrategia — porque el
    puntaje de convicción (Sección 6.1) es quien decide después cuál, si alguna, alertar.
    """
    if iv_rank is None:
        return []  # sin IV Rank calculable todavía, no hay base para decidir (Sección 4, gap conocido)

    allowed = allowed_strategies(risk_level)
    bias = _directional_bias(ma_cross_signal, rsi)
    candidates: list[str] = []

    if iv_rank >= c.IV_RANK_HIGH_THRESHOLD:
        if bias != "bearish":
            candidates += [c.CASH_SECURED_PUT, c.SHORT_PUT_NAKED, c.BULL_PUT_SPREAD, c.PUT_RATIO_SPREAD]
        if bias != "bullish":
            candidates += [c.SHORT_CALL_NAKED, c.BEAR_CALL_SPREAD, c.CALL_RATIO_SPREAD]
        if bias == "neutral":
            candidates += [c.IRON_CONDOR, c.SHORT_CALL_CONDOR, c.SHORT_PUT_CONDOR]
    else:
        candidates += [c.CALENDAR_PUT_SPREAD, c.CALENDAR_CALL_SPREAD, c.DIAGONAL_PUT_SPREAD, c.DIAGONAL_CALL_SPREAD]
        if bias != "bearish":
            candidates += [c.BULL_CALL_SPREAD, c.CALL_RATIO_BACKSPREAD]
        if bias != "bullish":
            candidates.append(c.BEAR_PUT_SPREAD)

    if has_open_assigned_position:
        candidates += [c.COVERED_CALL, c.COLLAR]

    return [s for s in candidates if s in allowed]
