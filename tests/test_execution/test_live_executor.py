"""Tests del cerebro de ejecución: junta guardián + walker + armador en un plan (dry-run)."""

from __future__ import annotations

from datetime import date

from options_advisor.execution import live_guard as guard
from options_advisor.execution.live_executor import Opportunity, WalkConfig, plan_live_order
from options_advisor.execution.live_guard import AccountSnapshot, DayState, LiveLimits


def _opp(**over):
    base = dict(symbol="AAL", action=guard.ACTION_OPEN, expiration=date(2026, 9, 18), strike=11.0,
                requested_contracts=1, bid=1.30, ask=1.70, underlying_price=12.0,
                collateral_per_contract=300.0)
    base.update(over)
    return Opportunity(**base)


def _limits(**over):
    base = dict(enabled=True, dry_run=True, require_manual_arm=True, max_contracts_per_order=1,
                max_notional_per_order=0.0, max_orders_per_day=1, max_orders_per_week=5,
                max_total_deployed=10_000.0, max_underlying_price=700.0,
                allowed_symbols=("AAL", "NU", "SPY"), price_cap_exempt_symbols=("SPY",))
    base.update(over)
    return LiveLimits(**base)


_ACCT = AccountSnapshot(cash=50_000.0, equity=50_000.0)
_DAY = DayState()


def test_plan_approved_builds_payload_and_ladder():
    plan = plan_live_order(_opp(), _ACCT, _limits(), _DAY, armed=True)
    assert plan.approved
    assert plan.final_contracts == 1
    assert plan.is_dry_run is True
    # Vendiendo con paso FIJO de 2 centavos: arranca 2¢ bajo el ask (1.68) y baja hasta el mid (1.50).
    assert plan.start_limit_price == 1.68
    assert plan.price_ladder[0] == 1.68
    assert plan.price_ladder[-1] == 1.50
    # Baja de 2 en 2: 1.68, 1.66, ... 1.50
    assert plan.price_ladder[1] == 1.66
    assert plan.order_payload["orderType"] == "LIMIT"
    assert plan.order_payload["orderLegCollection"][0]["instrument"]["symbol"] == "AAL   260918P00011000"
    assert "DRY-RUN" in plan.description


def test_plan_rejected_when_not_armed():
    plan = plan_live_order(_opp(), _ACCT, _limits(), _DAY, armed=False)
    assert not plan.approved
    assert any("ARMADO" in r for r in plan.reasons)


def test_plan_rejected_symbol_not_whitelisted():
    plan = plan_live_order(_opp(symbol="TSLA"), _ACCT, _limits(), _DAY, armed=True)
    assert not plan.approved


def test_plan_spy_exempt_from_price_cap():
    # SPY a $720 supera $700 pero está exento; margen alto pero presupuesto alcanza en este test.
    plan = plan_live_order(
        _opp(symbol="SPY", strike=600.0, underlying_price=720.0, bid=2.0, ask=2.4,
             collateral_per_contract=8000.0),
        _ACCT, _limits(max_total_deployed=10_000.0), _DAY, armed=True)
    assert plan.approved
    assert plan.order_payload["orderLegCollection"][0]["instrument"]["symbol"].startswith("SPY")


def test_plan_never_more_than_requested():
    plan = plan_live_order(_opp(requested_contracts=10), _ACCT, _limits(max_contracts_per_order=1),
                           _DAY, armed=True)
    assert plan.final_contracts <= 10 and plan.final_contracts == 1


def test_plan_wide_spread_uses_5_dollar_step():
    # Strike 132 (spread 1.00, grande) → arranca $5/contrato bajo el ask (12.45) y baja de 5 en 5.
    plan = plan_live_order(
        _opp(symbol="AAL", strike=132.0, underlying_price=140.0, bid=11.50, ask=12.50,
             collateral_per_contract=2000.0),
        _ACCT, _limits(max_underlying_price=0.0, max_total_deployed=100_000.0), _DAY, armed=True)
    assert plan.approved
    assert plan.start_limit_price == 12.45
    assert plan.price_ladder[1] == 12.40
    assert plan.price_ladder[-1] == 12.00


def test_plan_close_action_builds_buy_to_close():
    plan = plan_live_order(_opp(action=guard.ACTION_CLOSE, bid=0.66, ask=0.74), _ACCT, _limits(),
                           _DAY, armed=True)
    assert plan.approved
    leg = plan.order_payload["orderLegCollection"][0]
    assert leg["instruction"] == "BUY_TO_CLOSE"
    # Recomprando: arranca cerca del bid, sube hacia el mid (0.70), sin pasarlo.
    assert plan.price_ladder[-1] == 0.70
    assert all(p <= 0.70 for p in plan.price_ladder)
