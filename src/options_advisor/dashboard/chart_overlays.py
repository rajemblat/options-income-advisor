from __future__ import annotations

import json
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
