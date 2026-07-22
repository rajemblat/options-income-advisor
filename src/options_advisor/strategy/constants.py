"""Estrategias de Ingreso a Largo Plazo cubiertas en Fase 1 (Sección 3.3 de la hoja de ruta)."""

CASH_SECURED_PUT = "cash_secured_put"
BULL_PUT_SPREAD = "bull_put_spread"
IRON_CONDOR = "iron_condor"
CALENDAR_PUT_SPREAD = "calendar_put_spread"
DIAGONAL_PUT_SPREAD = "diagonal_put_spread"
COVERED_CALL = "covered_call"
SHORT_PUT_NAKED = "short_put_naked"

ALL_INCOME_STRATEGIES = {
    CASH_SECURED_PUT,
    BULL_PUT_SPREAD,
    IRON_CONDOR,
    CALENDAR_PUT_SPREAD,
    DIAGONAL_PUT_SPREAD,
    COVERED_CALL,
    SHORT_PUT_NAKED,
}

IV_RANK_HIGH_THRESHOLD = 50  # Sección 4.1: IV Rank > 50 ~ prima cara
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
