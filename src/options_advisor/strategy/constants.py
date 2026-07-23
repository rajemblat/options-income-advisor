"""Estrategias de venta de prima (Income) cubiertas por el motor. Agrupadas por categoría
según la hoja de ruta funcional del producto."""

# Income / venta de prima
CASH_SECURED_PUT = "cash_secured_put"
COVERED_CALL = "covered_call"
SHORT_PUT_NAKED = "short_put_naked"
SHORT_CALL_NAKED = "short_call_naked"

# Credit spreads
BULL_PUT_SPREAD = "bull_put_spread"
BEAR_CALL_SPREAD = "bear_call_spread"

# Debit spreads
BULL_CALL_SPREAD = "bull_call_spread"
BEAR_PUT_SPREAD = "bear_put_spread"
COLLAR = "collar"

# Neutral
IRON_CONDOR = "iron_condor"

# Calendar / diagonal (vencimientos combinados)
CALENDAR_PUT_SPREAD = "calendar_put_spread"
CALENDAR_CALL_SPREAD = "calendar_call_spread"
DIAGONAL_PUT_SPREAD = "diagonal_put_spread"
DIAGONAL_CALL_SPREAD = "diagonal_call_spread"

# Avanzadas (riesgo grande o no acotado en alguna pata — ver strategy/profiles.py)
CALL_RATIO_BACKSPREAD = "call_ratio_backspread"
CALL_RATIO_SPREAD = "call_ratio_spread"
PUT_RATIO_SPREAD = "put_ratio_spread"
SHORT_CALL_CONDOR = "short_call_condor"
SHORT_PUT_CONDOR = "short_put_condor"

ALL_INCOME_STRATEGIES = {
    CASH_SECURED_PUT,
    COVERED_CALL,
    SHORT_PUT_NAKED,
    SHORT_CALL_NAKED,
    BULL_PUT_SPREAD,
    BEAR_CALL_SPREAD,
    BULL_CALL_SPREAD,
    BEAR_PUT_SPREAD,
    COLLAR,
    IRON_CONDOR,
    CALENDAR_PUT_SPREAD,
    CALENDAR_CALL_SPREAD,
    DIAGONAL_PUT_SPREAD,
    DIAGONAL_CALL_SPREAD,
    CALL_RATIO_BACKSPREAD,
    CALL_RATIO_SPREAD,
    PUT_RATIO_SPREAD,
    SHORT_CALL_CONDOR,
    SHORT_PUT_CONDOR,
}

# Estrategias donde una pata queda neta corta sin cobertura completa (riesgo grande o no
# acotado): restringidas a perfil agresivo en strategy/profiles.py.
UNDEFINED_RISK_STRATEGIES = {
    SHORT_PUT_NAKED,
    SHORT_CALL_NAKED,
    CALL_RATIO_SPREAD,
    PUT_RATIO_SPREAD,
}

IV_RANK_HIGH_THRESHOLD = 50  # Sección 4.1: IV Rank > 50 ~ prima cara
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
