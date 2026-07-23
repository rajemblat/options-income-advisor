from __future__ import annotations

from options_advisor.strategy import constants as c

# Clasificación por régimen de IV Rank (Sección 6.1): "alto" favorece estrategias de crédito
# neto (venden prima cara); "bajo" favorece estrategias que compran optionalidad barata
# (calendar/diagonal, debit spreads, backspread). Covered Call y Collar no dependen
# fuertemente de IV Rank (la tesis es la posición en acciones, no el timing de volatilidad).
HIGH_IV_STRATEGIES = {
    c.CASH_SECURED_PUT,
    c.SHORT_PUT_NAKED,
    c.SHORT_CALL_NAKED,
    c.BULL_PUT_SPREAD,
    c.BEAR_CALL_SPREAD,
    c.IRON_CONDOR,
    c.SHORT_CALL_CONDOR,
    c.SHORT_PUT_CONDOR,
    c.CALL_RATIO_SPREAD,
    c.PUT_RATIO_SPREAD,
}
LOW_IV_STRATEGIES = {
    c.CALENDAR_PUT_SPREAD,
    c.CALENDAR_CALL_SPREAD,
    c.DIAGONAL_PUT_SPREAD,
    c.DIAGONAL_CALL_SPREAD,
    c.BULL_CALL_SPREAD,
    c.BEAR_PUT_SPREAD,
    c.CALL_RATIO_BACKSPREAD,
}
PUT_SELLING_STRATEGIES = HIGH_IV_STRATEGIES | LOW_IV_STRATEGIES

# Sesgo direccional de cada estrategia, para elegir qué lectura de RSI aplicarle (Sección
# 4.2): "bullish" evita vender puts en medio de pánico sin agotamiento; "bearish" evita
# vender calls en medio de un rally sin señales de techo; "neutral" (condors/calendars,
# que ganan con precio en rango o con vencimientos combinados) prefiere RSI ni sobrecomprado
# ni sobrevendido.
_BULLISH_RSI_STRATEGIES = {
    c.CASH_SECURED_PUT,
    c.SHORT_PUT_NAKED,
    c.BULL_PUT_SPREAD,
    c.BULL_CALL_SPREAD,
    c.CALL_RATIO_BACKSPREAD,
    c.PUT_RATIO_SPREAD,
}
_BEARISH_RSI_STRATEGIES = {
    c.COVERED_CALL,
    c.SHORT_CALL_NAKED,
    c.BEAR_CALL_SPREAD,
    c.BEAR_PUT_SPREAD,
    c.CALL_RATIO_SPREAD,
    c.COLLAR,
}
_NEUTRAL_RSI_STRATEGIES = {
    c.IRON_CONDOR,
    c.SHORT_CALL_CONDOR,
    c.SHORT_PUT_CONDOR,
    c.CALENDAR_PUT_SPREAD,
    c.CALENDAR_CALL_SPREAD,
    c.DIAGONAL_PUT_SPREAD,
    c.DIAGONAL_CALL_SPREAD,
}

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
    return round(WEIGHT_IV_RANK * 0.6, 2)  # covered_call / collar: no dependen fuertemente de IV Rank


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
    if strategy_type in (c.SHORT_CALL_CONDOR, c.SHORT_PUT_CONDOR):
        # Rango de "cuerpo" comprado (strike_2/strike_3): la tesis es un movimiento grande
        # que lo abandone, así que la referencia técnica es la cercanía a CUALQUIER nivel
        # fuerte (soporte o resistencia), no un lado en particular.
        body_mid = (strikes["strike_2"] + strikes["strike_3"]) / 2
        return _distance_score(body_mid, supports + resistances)
    if strategy_type == c.COLLAR:
        return _distance_score(strikes["call_strike"], resistances)
    if strategy_type in (c.COVERED_CALL, c.SHORT_CALL_NAKED, c.BEAR_CALL_SPREAD, c.CALL_RATIO_SPREAD, c.CALL_RATIO_BACKSPREAD):
        return _distance_score(strikes["short_strike"], resistances)
    if strategy_type in (c.CALENDAR_PUT_SPREAD, c.DIAGONAL_PUT_SPREAD):
        return _distance_score(strikes["near_strike"], supports)
    if strategy_type in (c.CALENDAR_CALL_SPREAD, c.DIAGONAL_CALL_SPREAD):
        return _distance_score(strikes["near_strike"], resistances)
    if strategy_type == c.BULL_CALL_SPREAD:
        # Debit spread bullish: la pata comprada (cerca del dinero) es la referencia — entrar
        # cerca de un soporte con margen para subir es lo técnicamente favorable.
        return _distance_score(strikes["long_strike"], supports)
    if strategy_type == c.BEAR_PUT_SPREAD:
        return _distance_score(strikes["long_strike"], resistances)
    if strategy_type == c.PUT_RATIO_SPREAD:
        return _distance_score(strikes["long_strike"], supports)
    # CSP, short_put_naked, bull_put_spread: short_strike es un put, referencia = soportes
    return _distance_score(strikes["short_strike"], supports)


def _score_rsi_context(strategy_type: str, rsi: float | None) -> float:
    if rsi is None:
        return WEIGHT_RSI_CONTEXT * 0.5  # sin RSI disponible: crédito parcial neutral
    if strategy_type in _BEARISH_RSI_STRATEGIES:
        # Sección 4.2: evitar vender calls en medio de un rally sin señales de techo.
        if rsi <= 70:
            return float(WEIGHT_RSI_CONTEXT)
        if rsi >= 85:
            return 0.0
        return round(WEIGHT_RSI_CONTEXT * (1 - (rsi - 70) / 15), 2)
    if strategy_type in _NEUTRAL_RSI_STRATEGIES:
        if 30 <= rsi <= 70:
            return float(WEIGHT_RSI_CONTEXT)
        if rsi < 30:
            if rsi <= 15:
                return 0.0
            return round(WEIGHT_RSI_CONTEXT * (1 - (30 - rsi) / 15), 2)
        if rsi >= 85:
            return 0.0
        return round(WEIGHT_RSI_CONTEXT * (1 - (rsi - 70) / 15), 2)
    # bullish (default): Sección 3.3, evitar vender puts en medio de pánico sin agotamiento.
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
