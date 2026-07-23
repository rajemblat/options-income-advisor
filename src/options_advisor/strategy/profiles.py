from __future__ import annotations

from options_advisor.strategy import constants as c

# Tamaño de posición sugerido como % del capital disponible (Sección 2.2 de la hoja de ruta).
POSITION_SIZE_PCT = {
    "conservador": 0.015,
    "moderado": 0.04,
    "agresivo": 0.07,
}

# Riesgo definido en ambos lados — habilitadas para los tres perfiles.
_BASE_INCOME_STRATEGIES = {
    c.CASH_SECURED_PUT,
    c.BULL_PUT_SPREAD,
    c.BEAR_CALL_SPREAD,
    c.BULL_CALL_SPREAD,
    c.BEAR_PUT_SPREAD,
    c.IRON_CONDOR,
    c.CALENDAR_PUT_SPREAD,
    c.CALENDAR_CALL_SPREAD,
    c.DIAGONAL_PUT_SPREAD,
    c.DIAGONAL_CALL_SPREAD,
    c.COVERED_CALL,
    c.COLLAR,
}

# Riesgo grande pero acotado (naked de un solo tipo, condors, backspread) o estructuras más
# complejas de gestionar — moderado y agresivo.
_MODERATE_RISK_ADDITIONS = {
    c.SHORT_PUT_NAKED,
    c.SHORT_CALL_NAKED,
    c.SHORT_CALL_CONDOR,
    c.SHORT_PUT_CONDOR,
    c.CALL_RATIO_BACKSPREAD,
}

# Riesgo no acotado (pata neta corta sin cobertura completa) — solo agresivo.
_AGGRESSIVE_ONLY_ADDITIONS = {
    c.CALL_RATIO_SPREAD,
    c.PUT_RATIO_SPREAD,
}

# La Sección 2.2 (tabla general) restringe Short Put desnudo a "Agresivo", pero la Sección 3.3
# (específica del escenario Ingreso, que es el alcance de Fase 1) lo habilita para "moderado/agresivo".
# Seguimos la 3.3 por ser la fuente específica del escenario que estamos construyendo — mismo
# criterio aplicado ahora al resto de riesgo grande/no acotado (ver constants.UNDEFINED_RISK_STRATEGIES).
ALLOWED_STRATEGIES_BY_RISK_LEVEL = {
    "conservador": frozenset(_BASE_INCOME_STRATEGIES),
    "moderado": frozenset(_BASE_INCOME_STRATEGIES | _MODERATE_RISK_ADDITIONS),
    "agresivo": frozenset(_BASE_INCOME_STRATEGIES | _MODERATE_RISK_ADDITIONS | _AGGRESSIVE_ONLY_ADDITIONS),
}


def allowed_strategies(risk_level: str) -> frozenset[str]:
    return ALLOWED_STRATEGIES_BY_RISK_LEVEL[risk_level]


def position_size_pct(risk_level: str) -> float:
    return POSITION_SIZE_PCT[risk_level]
