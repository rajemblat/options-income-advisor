from __future__ import annotations

from options_advisor.strategy import constants as c

# Tamaño de posición sugerido como % del capital disponible (Sección 2.2 de la hoja de ruta).
POSITION_SIZE_PCT = {
    "conservador": 0.015,
    "moderado": 0.04,
    "agresivo": 0.07,
}

_BASE_INCOME_STRATEGIES = {
    c.CASH_SECURED_PUT,
    c.BULL_PUT_SPREAD,
    c.IRON_CONDOR,
    c.CALENDAR_PUT_SPREAD,
    c.DIAGONAL_PUT_SPREAD,
    c.COVERED_CALL,
}

# La Sección 2.2 (tabla general) restringe Short Put desnudo a "Agresivo", pero la Sección 3.3
# (específica del escenario Ingreso, que es el alcance de Fase 1) lo habilita para "moderado/agresivo".
# Seguimos la 3.3 por ser la fuente específica del escenario que estamos construyendo.
ALLOWED_STRATEGIES_BY_RISK_LEVEL = {
    "conservador": frozenset(_BASE_INCOME_STRATEGIES),
    "moderado": frozenset(_BASE_INCOME_STRATEGIES | {c.SHORT_PUT_NAKED}),
    "agresivo": frozenset(_BASE_INCOME_STRATEGIES | {c.SHORT_PUT_NAKED}),
}


def allowed_strategies(risk_level: str) -> frozenset[str]:
    return ALLOWED_STRATEGIES_BY_RISK_LEVEL[risk_level]


def position_size_pct(risk_level: str) -> float:
    return POSITION_SIZE_PCT[risk_level]
