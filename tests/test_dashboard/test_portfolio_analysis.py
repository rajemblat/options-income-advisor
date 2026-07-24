from __future__ import annotations

from datetime import date

import pytest

from options_advisor.broker.models import AccountPosition, Greeks, OptionChain, OptionContract
from options_advisor.dashboard.portfolio_analysis import (
    effective_projected_pnl_at_date,
    effective_projected_pnl_at_own_expiration,
    find_matching_contract_iv,
    intrinsic_value,
    position_cost_basis,
    position_multiplier,
    position_pct_return,
    projected_pnl_at_date,
    projected_pnl_at_own_expiration,
    reprice_option_bsm,
)

RISK_FREE_RATE = 0.045


def _equity_position(**overrides) -> AccountPosition:
    base = dict(
        account_number="111", symbol="NVDA", asset_type="EQUITY",
        quantity=300, average_price=209.41, market_value=62370.0, unrealized_pnl=-453.49,
    )
    base.update(overrides)
    return AccountPosition(**base)


def _short_put_position(**overrides) -> AccountPosition:
    base = dict(
        account_number="111", symbol="SLV   260821P00060000", asset_type="OPTION",
        quantity=-2, average_price=6.71, market_value=-1670.0, unrealized_pnl=-300.82,
        underlying_symbol="SLV", option_type="put", strike=60.0, expiration=date(2026, 8, 21),
    )
    base.update(overrides)
    return AccountPosition(**base)


def _long_call_position(**overrides) -> AccountPosition:
    base = dict(
        account_number="111", symbol="AAPL  260117C00320000", asset_type="OPTION",
        quantity=1, average_price=12.0, market_value=1200.0, unrealized_pnl=0.0,
        underlying_symbol="AAPL", option_type="call", strike=320.0, expiration=date(2026, 1, 17),
    )
    base.update(overrides)
    return AccountPosition(**base)


def test_position_multiplier_is_100_for_options_and_1_for_equity():
    assert position_multiplier(_short_put_position()) == 100
    assert position_multiplier(_equity_position()) == 1


def test_position_cost_basis_uses_absolute_value():
    position = _short_put_position()
    assert position_cost_basis(position) == pytest.approx(2 * 6.71 * 100)


def test_position_pct_return_matches_unrealized_pnl_over_cost_basis():
    position = _short_put_position()
    pct = position_pct_return(position)
    assert pct == pytest.approx(-300.82 / 1342.0 * 100)


def test_position_pct_return_none_when_cost_basis_is_zero():
    position = _equity_position(average_price=0.0)
    assert position_pct_return(position) is None


def test_intrinsic_value_call_and_put():
    assert intrinsic_value("call", strike=100, underlying_price=110) == 10
    assert intrinsic_value("call", strike=100, underlying_price=90) == 0
    assert intrinsic_value("put", strike=100, underlying_price=90) == 10
    assert intrinsic_value("put", strike=100, underlying_price=110) == 0


def test_projected_pnl_at_own_expiration_short_put_max_profit_when_otm():
    """Put corta, precio termina arriba del strike -> vale $0, se queda con toda la prima."""
    position = _short_put_position()
    pnl = projected_pnl_at_own_expiration(position, underlying_price=65.0)
    assert pnl == pytest.approx(-2 * 100 * (0 - 6.71))
    assert pnl == pytest.approx(1342.0)


def test_projected_pnl_at_own_expiration_short_put_loses_when_deep_itm():
    position = _short_put_position()
    pnl = projected_pnl_at_own_expiration(position, underlying_price=50.0)  # 10 ITM
    assert pnl == pytest.approx(-2 * 100 * (10 - 6.71))
    assert pnl < 0


def test_projected_pnl_at_own_expiration_long_call_max_loss_when_otm():
    position = _long_call_position()
    pnl = projected_pnl_at_own_expiration(position, underlying_price=300.0)  # abajo del strike 320
    assert pnl == pytest.approx(1 * 100 * (0 - 12.0))
    assert pnl == pytest.approx(-1200.0)  # pierde toda la prima pagada, no más


def test_projected_pnl_at_own_expiration_none_for_equity():
    assert projected_pnl_at_own_expiration(_equity_position(), underlying_price=200.0) is None


def test_reprice_option_bsm_at_zero_years_returns_intrinsic():
    price = reprice_option_bsm("call", underlying_price=110, strike=100, years_to_expiration=0, iv=0.3, risk_free_rate=RISK_FREE_RATE)
    assert price == 10.0


def test_reprice_option_bsm_positive_time_exceeds_intrinsic_for_atm():
    """Una call ATM con tiempo restante vale más que su intrínseco (que es 0) — todo es valor
    extrínseco, prueba de sanidad de que el BSM está corriendo, no solo devolviendo 0."""
    price = reprice_option_bsm("call", underlying_price=100, strike=100, years_to_expiration=0.25, iv=0.3, risk_free_rate=RISK_FREE_RATE)
    assert price > 0


def test_projected_pnl_at_date_after_expiration_uses_intrinsic_only():
    position = _short_put_position()  # vence 2026-08-21
    pnl = projected_pnl_at_date(position, underlying_price=65.0, target_date=date(2026, 9, 1), iv=None, risk_free_rate=RISK_FREE_RATE)
    assert pnl == pytest.approx(1342.0)  # mismo resultado que "a vencimiento", no hizo falta iv


def test_projected_pnl_at_date_before_expiration_requires_iv():
    position = _short_put_position()
    assert projected_pnl_at_date(position, underlying_price=65.0, target_date=date(2026, 8, 1), iv=None, risk_free_rate=RISK_FREE_RATE) is None


def test_projected_pnl_at_date_before_expiration_with_iv_uses_bsm():
    position = _short_put_position()
    pnl = projected_pnl_at_date(position, underlying_price=65.0, target_date=date(2026, 8, 1), iv=0.35, risk_free_rate=RISK_FREE_RATE)
    assert pnl is not None
    # Todavía queda valor extrínseco antes del vencimiento -> la opción vale más que $0 ->
    # el P&L proyectado es MENOR que el máximo teórico a vencimiento (1342.0).
    assert pnl < 1342.0


def test_projected_pnl_at_date_none_for_equity():
    assert projected_pnl_at_date(_equity_position(), underlying_price=200.0, target_date=date(2026, 12, 1), iv=0.3, risk_free_rate=RISK_FREE_RATE) is None


def _contract(option_type: str, strike: float, expiration: date, iv: float) -> OptionContract:
    return OptionContract(
        symbol="SLV", option_type=option_type, strike=strike, expiration=expiration,
        bid=1.0, ask=1.1, last_price=1.05, implied_volatility=iv, open_interest=10, volume=1,
        greeks=Greeks(delta=0.3, gamma=0.01, theta=-0.02, vega=0.05, rho=0.01, source="broker"),
    )


def test_find_matching_contract_iv_finds_exact_match():
    position = _short_put_position()
    chain = OptionChain(
        symbol="SLV", as_of=date(2026, 7, 24), underlying_price=65.0,
        contracts=[
            _contract("put", 55.0, date(2026, 8, 21), iv=0.40),
            _contract("put", 60.0, date(2026, 8, 21), iv=0.38),  # el que matchea
            _contract("call", 60.0, date(2026, 8, 21), iv=0.36),
        ],
    )
    assert find_matching_contract_iv(chain, position) == 0.38


def test_find_matching_contract_iv_none_when_not_in_chain():
    position = _short_put_position()
    chain = OptionChain(symbol="SLV", as_of=date(2026, 7, 24), underlying_price=65.0, contracts=[])
    assert find_matching_contract_iv(chain, position) is None


def test_effective_projected_pnl_at_own_expiration_passes_through_equity_pnl():
    position = _equity_position(unrealized_pnl=-453.49)
    assert effective_projected_pnl_at_own_expiration(position, underlying_price=200.0) == -453.49
    assert effective_projected_pnl_at_own_expiration(position, underlying_price=None) == -453.49  # no necesita precio


def test_effective_projected_pnl_at_own_expiration_delegates_for_options():
    position = _short_put_position()
    assert effective_projected_pnl_at_own_expiration(position, underlying_price=65.0) == pytest.approx(1342.0)
    assert effective_projected_pnl_at_own_expiration(position, underlying_price=None) is None


def test_effective_projected_pnl_at_date_passes_through_equity_pnl():
    position = _equity_position(unrealized_pnl=-453.49)
    assert effective_projected_pnl_at_date(position, 200.0, date(2026, 12, 1), iv=None, risk_free_rate=RISK_FREE_RATE) == -453.49
