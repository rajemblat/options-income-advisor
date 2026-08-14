from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class StrikeLevel:
    strike: float
    option_type: str  # "put" | "call"
    side: str  # "sell" | "buy" (ver strategy/candidates.py::Leg)
    strategy_type: str
    source: str  # "candidato" | "operación real"


def _parse_legs(legs_json: str | None) -> list[dict]:
    return json.loads(legs_json) if legs_json else []


def build_alert_strike_levels(
    candidate_rows: list[sqlite3.Row], real_trade_rows: list[sqlite3.Row], as_of: date
) -> list[StrikeLevel]:
    """Niveles de strike a dibujar sobre el gráfico de velas (pedido 2026-07-31, "conectar el
    gráfico con alertas") — una `StrikeLevel` por pata de cada alerta ACTIVA del símbolo, para
    ver el precio en contexto de la posición/candidato.

    `candidate_rows` ya viene filtrado a no vencidas (`repo.get_active_candidate_alerts_with_legs`,
    filtro de expiración en SQL). `real_trade_rows` es el resultado crudo de
    `repo.get_real_trade_alerts` (sin ese filtro) — acá se excluyen tanto las vencidas
    (`expiration_date < as_of`) como la pata `roll_closed` de un roll (ya no es una posición
    activa, mismo criterio que usa la Pestaña Operaciones para "operación abierta"). Niveles
    idénticos (mismo strike/tipo/lado/estrategia/origen) se deduplican — pueden repetirse entre
    corridas del scheduler o entre un candidato y la operación real que se ejecutó a partir de él."""
    levels: list[StrikeLevel] = []
    for row in candidate_rows:
        for leg in _parse_legs(row["legs_json"]):
            levels.append(
                StrikeLevel(
                    strike=leg["strike"],
                    option_type=leg["option_type"],
                    side=leg["side"],
                    strategy_type=row["strategy_type"],
                    source="candidato",
                )
            )
    for row in real_trade_rows:
        if row["leg_role"] == "roll_closed":
            continue
        if date.fromisoformat(row["expiration_date"]) < as_of:
            continue
        for leg in _parse_legs(row["legs_json"]):
            levels.append(
                StrikeLevel(
                    strike=leg["strike"],
                    option_type=leg["option_type"],
                    side=leg["side"],
                    strategy_type=row["strategy_type"],
                    source="operación real",
                )
            )
    # dict.fromkeys preserva el orden de primera aparición (a diferencia de un set) — no
    # importa acá, pero es gratis y evita sorpresas si en algún momento el orden sí importa.
    return list(dict.fromkeys(levels))


@dataclass(frozen=True)
class SimulatorOverlay:
    """Lo que el SIMULADOR usó para decidir una apertura de put, para dibujarlo sobre el gráfico y
    que Gráfico y Simulador compartan datos (usuario 2026-08-06): strike elegido, precio del
    subyacente al abrir, cobertura (colchón), movimiento de 1σ y TODOS los soportes usados."""

    strike: float | None
    underlying: float | None
    coverage_pct: float | None       # fracción (0.089 = 8.9%)
    sigma_move: float | None         # $ del movimiento esperado de 1σ hasta el vencimiento
    supports: tuple[float, ...]      # todos los soportes (fuerte + diarios), ordenados desc, sin repetir
    strong_support: float | None     # el soporte "fuerte" que el robot usó
    dte: int | None


def build_simulator_overlay(position_row, ctx: dict | None) -> SimulatorOverlay | None:
    """Arma el overlay del simulador desde una posición abierta y el contexto de su decisión.
    Devuelve None si no hay datos útiles. Función pura (recibe fila + ctx ya parseado) para poder
    testearla sin base ni red."""
    ctx = ctx or {}

    def _num(v):
        return v if isinstance(v, (int, float)) else None

    strike = _num(ctx.get("chosen_strike"))
    if strike is None and position_row is not None:
        try:
            strike = _num(position_row["strike"])
        except (KeyError, IndexError, TypeError):
            strike = None

    underlying = _num(ctx.get("underlying_price"))
    coverage = _num(ctx.get("chosen_coverage_pct"))
    if underlying is None and strike is not None and coverage is not None and coverage < 1:
        underlying = round(strike / (1 - coverage), 2)   # cobertura = (precio-strike)/precio

    iv = _num(ctx.get("chosen_iv"))
    dte = _num(ctx.get("chosen_dte"))
    sigma_move = None
    if underlying and underlying > 0 and iv and isinstance(dte, (int, float)) and dte >= 0:
        sigma_move = round(underlying * iv * math.sqrt(max(dte, 0.0) / 365.0), 2)

    strong = _num(ctx.get("support_used"))
    supports_raw = ctx.get("supports_daily")
    all_supports = []
    if strong is not None:
        all_supports.append(round(strong, 2))
    if isinstance(supports_raw, list):
        for s in supports_raw:
            if isinstance(s, (int, float)):
                all_supports.append(round(s, 2))
    supports = tuple(dict.fromkeys(sorted(all_supports, reverse=True)))   # sin repetir, desc

    if strike is None and not supports and sigma_move is None:
        return None
    return SimulatorOverlay(
        strike=strike, underlying=underlying, coverage_pct=coverage, sigma_move=sigma_move,
        supports=supports, strong_support=(round(strong, 2) if strong is not None else None),
        dte=int(dte) if isinstance(dte, (int, float)) else None,
    )
