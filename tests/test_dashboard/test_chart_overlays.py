from __future__ import annotations

import json
from datetime import date

from options_advisor.dashboard.chart_overlays import StrikeLevel, build_alert_strike_levels


def _candidate_row(strategy_type: str, legs: list[dict]) -> dict:
    return {"symbol": "AAPL", "strategy_type": strategy_type, "expiration_date": "2026-08-21", "legs_json": json.dumps(legs)}


def _real_trade_row(strategy_type: str, legs: list[dict], expiration_date: str, leg_role: str | None = None) -> dict:
    return {
        "symbol": "AAPL",
        "strategy_type": strategy_type,
        "expiration_date": expiration_date,
        "legs_json": json.dumps(legs),
        "leg_role": leg_role,
    }


_PUT_SHORT = {"strike": 300.0, "option_type": "put", "side": "sell"}
_CALL_LONG = {"strike": 320.0, "option_type": "call", "side": "buy"}


def test_candidate_leg_becomes_a_level():
    levels = build_alert_strike_levels([_candidate_row("cash_secured_put", [_PUT_SHORT])], [], as_of=date(2026, 7, 31))
    assert levels == [StrikeLevel(strike=300.0, option_type="put", side="sell", strategy_type="cash_secured_put", source="candidato")]


def test_real_trade_leg_becomes_a_level():
    levels = build_alert_strike_levels(
        [], [_real_trade_row("cash_secured_put", [_PUT_SHORT], "2026-08-21")], as_of=date(2026, 7, 31)
    )
    assert levels == [
        StrikeLevel(strike=300.0, option_type="put", side="sell", strategy_type="cash_secured_put", source="operación real")
    ]


def test_multi_leg_row_produces_one_level_per_leg():
    levels = build_alert_strike_levels([_candidate_row("iron_condor", [_PUT_SHORT, _CALL_LONG])], [], as_of=date(2026, 7, 31))
    assert len(levels) == 2
    assert {lvl.strike for lvl in levels} == {300.0, 320.0}


def test_real_trade_roll_closed_leg_is_excluded():
    """La pata `roll_closed` de un roll ya no es una posición activa (mismo criterio que la
    Pestaña Operaciones para "operación abierta") — no debe dibujarse en el gráfico."""
    levels = build_alert_strike_levels(
        [], [_real_trade_row("cash_secured_put", [_PUT_SHORT], "2026-08-21", leg_role="roll_closed")], as_of=date(2026, 7, 31)
    )
    assert levels == []


def test_real_trade_roll_opened_leg_is_included():
    levels = build_alert_strike_levels(
        [], [_real_trade_row("cash_secured_put", [_PUT_SHORT], "2026-08-21", leg_role="roll_opened")], as_of=date(2026, 7, 31)
    )
    assert len(levels) == 1


def test_expired_real_trade_is_excluded():
    levels = build_alert_strike_levels(
        [], [_real_trade_row("cash_secured_put", [_PUT_SHORT], "2026-07-01")], as_of=date(2026, 7, 31)
    )
    assert levels == []


def test_duplicate_levels_are_deduplicated():
    row = _candidate_row("cash_secured_put", [_PUT_SHORT])
    levels = build_alert_strike_levels([row, row], [], as_of=date(2026, 7, 31))
    assert len(levels) == 1


def test_no_rows_returns_empty_list():
    assert build_alert_strike_levels([], [], as_of=date(2026, 7, 31)) == []
