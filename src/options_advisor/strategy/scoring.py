from __future__ import annotations

from options_advisor.strategy import constants as c

HIGH_IV_STRATEGIES = {c.CASH_SECURED_PUT, c.BULL_PUT_SPREAD, c.IRON_CONDOR, c.SHORT_PUT_NAKED}
LOW_IV_STRATEGIES = {c.CALENDAR_PUT_SPREAD, c.DIAGONAL_PUT_SPREAD}
PUT_SELLING_STRATEGIES = HIGH_IV_STRATEGIES | LOW_IV_STRATEGIES

# Pesos del puntaje de convicción 0-100 (Sección 6.1). Documentados acá porque son una
# decisión de producto, no un detalle de implementación — ajustar con cuidado.
WEIGHT_IV_RANK = 40
WEIGHT_TECHNICAL_LEVEL = 25
WEIGHT_RSI_CONTEXT = 20
WEIGHT_DATA_CONFIDENCE = 15

FULL_CREDIT_DISTANCE_PCT = 0.03
ZERO_CREDIT_DISTANCE_PCT = 0.08


def _score_iv_rank(strategy_type: str, iv_rank: float) -> float:
    if strategy_type in HIGH_IV_STRATEGIES:
        return round(WEIGHT_IV_RANK * (iv_rank / 100), 2)
    if strategy_type in LOW_IV_STRATEGIES:
        return round(WEIGHT_IV_RANK * (1 - iv_rank / 100), 2)
    return round(WEIGHT_IV_RANK * 0.6, 2)  # covered_call: no depende fuertemente de IV Rank


def _distance_score(strike: float, levels: list[float]) -> float:
    if not levels:
        return WEIGHT_TECHNICAL_LEVEL * 0.4  # sin niveles detectados: crédito parcial neutral
    nearest = min(levels, key=lambda lvl: abs(lvl - strike))
    distance_pct = abs(nearest - strike) / strike
    if distance_pct <= FULL_CREDIT_DISTANCE_PCT:
        return float(WEIGHT_TECHNICAL_LEVEL)
    if distance_pct >= ZERO_CREDIT_DISTANCE_PCT:
        return 0.0
    span = ZERO_CREDIT_DISTANCE_PCT - FULL_CREDIT_DISTANCE_PCT
    return round(WEIGHT_TECHNICAL_LEVEL * (1 - (distance_pct - FULL_CREDIT_DISTANCE_PCT) / span), 2)


def _score_technical_level(strategy_type: str, strikes: dict, supports: list[float], resistances: list[float]) -> float:
    if strategy_type == c.IRON_CONDOR:
        put_score = _distance_score(strikes["put_short_strike"], supports)
        call_score = _distance_score(strikes["call_short_strike"], resistances)
        return round((put_score + call_score) / 2, 2)
    if strategy_type == c.COVERED_CALL:
        return _distance_score(strikes["short_strike"], resistances)
    if strategy_type in (c.CALENDAR_PUT_SPREAD, c.DIAGONAL_PUT_SPREAD):
        return _distance_score(strikes["near_strike"], supports)
    return _distance_score(strikes["short_strike"], supports)


def _score_rsi_context(strategy_type: str, rsi: float | None) -> float:
    if rsi is None:
        return WEIGHT_RSI_CONTEXT * 0.5  # sin RSI disponible: crédito parcial neutral
    if strategy_type == c.COVERED_CALL:
        # Sección 4.2: evitar vender calls en medio de un rally sin señales de techo.
        if rsi <= 70:
            return float(WEIGHT_RSI_CONTEXT)
        if rsi >= 85:
            return 0.0
        return round(WEIGHT_RSI_CONTEXT * (1 - (rsi - 70) / 15), 2)
    # Sección 3.3: evitar vender puts en medio de pánico de venta sin señales de agotamiento.
    if rsi >= 30:
        return float(WEIGHT_RSI_CONTEXT)
    if rsi <= 15:
        return 0.0
    return round(WEIGHT_RSI_CONTEXT * (1 - (30 - rsi) / 15), 2)


def _score_data_confidence(iv_rank_source: str) -> float:
    return float(WEIGHT_DATA_CONFIDENCE) if iv_rank_source == "implied_volatility" else WEIGHT_DATA_CONFIDENCE * 0.45


def compute_conviction_score(
    strategy_type: str,
    strikes: dict,
    iv_rank: float,
    iv_rank_source: str,
    rsi: float | None,
    supports: list[float],
    resistances: list[float],
) -> tuple[int, dict]:
    """Puntaje de convicción 0-100, determinístico (Sección 6.1). Devuelve el score y un
    breakdown con el aporte de cada factor, para auditoría y para que el narrador (Sección 6.2)
    tenga insumos concretos sin tener que inventar nada."""
    breakdown = {
        "iv_rank_alignment": _score_iv_rank(strategy_type, iv_rank),
        "technical_level_proximity": _score_technical_level(strategy_type, strikes, supports, resistances),
        "rsi_context": _score_rsi_context(strategy_type, rsi),
        "data_confidence": _score_data_confidence(iv_rank_source),
    }
    total = round(sum(breakdown.values()))
    return max(0, min(100, total)), breakdown
