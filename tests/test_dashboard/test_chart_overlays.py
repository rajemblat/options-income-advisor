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


# ---------- Overlay del simulador (usuario 2026-08-06) ----------
from options_advisor.dashboard.chart_overlays import SimulatorOverlay, build_simulator_overlay


class _Row(dict):
    """Fila tipo sqlite3.Row para test (permite row['strike'])."""


def test_simulator_overlay_full_context():
    ctx = {
        "chosen_strike": 15.0, "underlying_price": 16.47, "chosen_coverage_pct": 0.089,
        "chosen_iv": 0.512, "chosen_dte": 43, "support_used": 16.43,
        "supports_daily": [16.0, 16.0, 15.0, 14.0, 16.43],
    }
    ov = build_simulator_overlay(_Row(strike=15.0), ctx)
    assert ov is not None
    assert ov.strike == 15.0
    assert ov.underlying == 16.47
    assert ov.coverage_pct == 0.089
    assert ov.strong_support == 16.43
    # Soportes sin repetir, ordenados desc, incluye el fuerte + los diarios.
    assert ov.supports == (16.43, 16.0, 15.0, 14.0)
    # 1σ = precio * IV * sqrt(dte/365) ≈ 16.47*0.512*sqrt(43/365)
    assert ov.sigma_move is not None and 2.7 < ov.sigma_move < 3.0


def test_simulator_overlay_derives_underlying_from_coverage():
    # Sin underlying_price: se deriva de strike/(1-cobertura).
    ov = build_simulator_overlay(_Row(strike=100.0), {"chosen_coverage_pct": 0.10})
    assert ov is not None
    assert ov.underlying == round(100.0 / 0.9, 2)


def test_simulator_overlay_none_when_empty():
    assert build_simulator_overlay(_Row(strike=None), {}) is None


def test_simulator_overlay_falls_back_to_position_strike():
    ov = build_simulator_overlay(_Row(strike=42.0), {"support_used": 40.0})
    assert ov.strike == 42.0
    assert ov.supports == (40.0,)
