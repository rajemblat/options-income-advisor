"""Tests del guardián de órdenes reales. La propiedad más importante: NUNCA se aprueba más de lo
pedido y todos los topes duros se respetan (usuario 2026-08-07, trading real con poco capital)."""

from __future__ import annotations

import pytest

from options_advisor.config import LiveTradingSettings
from options_advisor.execution import live_guard as lg
from options_advisor.execution.live_guard import (
    AccountSnapshot,
    DayState,
    IntendedOrder,
    LiveLimits,
    evaluate,
)


def _order(**over):
    base = dict(symbol="AAPL", action=lg.ACTION_OPEN, option_type="PUT", strike=100.0,
               expiration="2026-09-18", requested_contracts=3, limit_price=1.50, underlying_price=180.0)
    base.update(over)
    return IntendedOrder(**base)


def _limits(**over):
    # Base "habilitada y armada" para poder testear las capas de abajo; los tests de gate la apagan.
    base = dict(enabled=True, dry_run=True, kill_switch=False, require_manual_arm=True,
                max_contracts_per_order=4, max_notional_per_order=1_000_000.0, max_orders_per_day=5,
                max_total_deployed=1_000_000.0, max_underlying_price=700.0, min_account_cash_buffer=0.0,
                allowed_symbols=())
    base.update(over)
    return LiveLimits(**base)


_ACCT = AccountSnapshot(cash=100_000.0, equity=100_000.0)
_DAY = DayState(orders_today=0, deployed_today=0.0)


# --------------------------- compuertas duras ---------------------------

def test_fully_off_rejects():
    # Apagado del todo (enabled=false Y dry_run=false) → rechaza.
    d = evaluate(_order(), _ACCT, _limits(enabled=False, dry_run=False), _DAY, armed=True)
    assert d.rejected and d.final_contracts == 0
    assert any("apagado" in r for r in d.reasons)


def test_dry_run_approves_without_enabled():
    # En dry-run (enabled=false, dry_run=true) el guardián APRUEBA para mostrar el plan, marcándolo
    # como simulacro (no se envía).
    d = evaluate(_order(requested_contracts=1), _ACCT, _limits(enabled=False, dry_run=True), _DAY, armed=True)
    assert d.approved and d.is_dry_run is True


def test_kill_switch_rejects():
    d = evaluate(_order(), _ACCT, _limits(kill_switch=True), _DAY, armed=True)
    assert d.rejected
    assert any("KILL SWITCH" in r for r in d.reasons)


def test_not_armed_rejects():
    d = evaluate(_order(), _ACCT, _limits(require_manual_arm=True), _DAY, armed=False)
    assert d.rejected
    assert any("ARMADO" in r for r in d.reasons)


def test_armed_not_required_passes_gate():
    d = evaluate(_order(requested_contracts=1), _ACCT, _limits(require_manual_arm=False), _DAY, armed=False)
    assert d.approved


# --------------------------- filtros de apertura ---------------------------

def test_whitelist_blocks_other_symbols():
    d = evaluate(_order(symbol="TSLA"), _ACCT, _limits(allowed_symbols=("AAPL", "MSFT")), _DAY, armed=True)
    assert d.rejected
    assert any("whitelist" in r for r in d.reasons)


def test_whitelist_allows_listed_symbol():
    d = evaluate(_order(symbol="AAPL", requested_contracts=1), _ACCT, _limits(allowed_symbols=("AAPL",)), _DAY, armed=True)
    assert d.approved


def test_underlying_price_cap_rejects():
    d = evaluate(_order(underlying_price=720.0), _ACCT, _limits(max_underlying_price=700.0), _DAY, armed=True)
    assert d.rejected
    assert any("subyacente" in r for r in d.reasons)


def test_price_cap_exemption_allows_spy_over_cap():
    # SPY a $720 supera el tope de $700 pero está exento → se opera igual (usuario 2026-08-09).
    d = evaluate(_order(symbol="SPY", underlying_price=720.0, requested_contracts=1),
                 _ACCT, _limits(max_underlying_price=700.0, price_cap_exempt_symbols=("SPY",)), _DAY, armed=True)
    assert d.approved


def test_deployment_uses_margin_not_notional():
    # strike 70 → notional $7.000/contrato, pero el MARGEN real es $1.200. Con presupuesto $10.000 y
    # 3 pedidos, deberían entrar los 3 (3×1.200=3.600 ≤ 10.000), no recortar por el notional de $7.000.
    o = _order(strike=70.0, requested_contracts=3, collateral_per_contract=1200.0, underlying_price=70.0)
    d = evaluate(o, _ACCT, _limits(max_contracts_per_order=5, max_total_deployed=10_000.0), _DAY, armed=True)
    assert d.approved and d.final_contracts == 3


def test_daily_order_cap_rejects():
    d = evaluate(_order(), _ACCT, _limits(max_orders_per_day=5), DayState(orders_today=5), armed=True)
    assert d.rejected
    assert any("órdenes reales por día" in r for r in d.reasons)


def test_weekly_order_cap_rejects():
    d = evaluate(_order(), _ACCT, _limits(max_orders_per_week=5),
                 DayState(orders_today=0, orders_this_week=5), armed=True)
    assert d.rejected
    assert any("por semana" in r for r in d.reasons)


def test_weekly_cap_allows_under_limit():
    d = evaluate(_order(requested_contracts=1), _ACCT, _limits(max_orders_per_week=5),
                 DayState(orders_today=0, orders_this_week=4), armed=True)
    assert d.approved


# --------------------------- recortes (nunca hacia arriba) ---------------------------

def test_contracts_cap_clamps_down():
    d = evaluate(_order(requested_contracts=10), _ACCT, _limits(max_contracts_per_order=4), _DAY, armed=True)
    assert d.approved and d.final_contracts == 4
    assert any("tope de contratos por orden" in r for r in d.reasons)


def test_notional_cap_clamps_down():
    # strike 100 → $10,000/contrato. Tope $25,000 → máximo 2 contratos.
    d = evaluate(_order(strike=100.0, requested_contracts=4), _ACCT,
                 _limits(max_notional_per_order=25_000.0), _DAY, armed=True)
    assert d.approved and d.final_contracts == 2
    assert any("notional" in r for r in d.reasons)


def test_notional_cap_rejects_when_not_even_one_fits():
    # strike 500 → $50,000/contrato, tope $40,000 → ni 1 entra.
    d = evaluate(_order(strike=500.0, requested_contracts=1, underlying_price=520.0), _ACCT,
                 _limits(max_notional_per_order=40_000.0), _DAY, armed=True)
    assert d.rejected and d.final_contracts == 0


def test_total_deployed_cap_clamps():
    # $10,000/contrato, tope total $50,000, ya desplegado $35,000 → entran 1 (15k libres).
    d = evaluate(_order(strike=100.0, requested_contracts=4), _ACCT,
                 _limits(max_total_deployed=50_000.0), DayState(deployed_today=35_000.0), armed=True)
    assert d.approved and d.final_contracts == 1


def test_cash_buffer_clamps():
    # cash 100k, colchón 95k → 5k disponibles → 0 contratos de $10k → rechaza.
    d = evaluate(_order(strike=100.0, requested_contracts=3), AccountSnapshot(cash=100_000.0, equity=100_000.0),
                 _limits(min_account_cash_buffer=95_000.0), _DAY, armed=True)
    assert d.rejected


# --------------------------- happy path + invariante ---------------------------

def test_happy_path_approves_exactly_requested():
    d = evaluate(_order(strike=100.0, requested_contracts=3), _ACCT, _limits(), _DAY, armed=True)
    assert d.approved and d.final_contracts == 3
    assert d.notional == 100.0 * 100 * 3
    assert d.is_dry_run is True


@pytest.mark.parametrize("req", [1, 2, 3, 4, 7, 20, 100])
def test_invariant_never_more_than_requested(req):
    d = evaluate(_order(requested_contracts=req), _ACCT, _limits(max_contracts_per_order=4), _DAY, armed=True)
    assert d.final_contracts <= req  # invariante central de seguridad


# --------------------------- cierres ---------------------------

def test_close_bypasses_deployment_caps():
    # Un cierre reduce riesgo: pasa aunque el capital desplegado ya esté al tope.
    d = evaluate(_order(action=lg.ACTION_CLOSE, requested_contracts=3), _ACCT,
                 _limits(max_total_deployed=1.0), DayState(deployed_today=999_999.0), armed=True)
    assert d.approved and d.final_contracts == 3


def test_close_still_blocked_by_kill_switch():
    d = evaluate(_order(action=lg.ACTION_CLOSE, requested_contracts=3), _ACCT,
                 _limits(kill_switch=True), _DAY, armed=True)
    assert d.rejected


def test_limits_from_settings_maps_fields():
    live = LiveTradingSettings(enabled=True, max_contracts_per_order=2, allowed_symbols=["AAPL", "MSFT"])
    lim = lg.limits_from_settings(live)
    assert lim.enabled is True
    assert lim.max_contracts_per_order == 2
    assert lim.allowed_symbols == ("AAPL", "MSFT")
