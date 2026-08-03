from __future__ import annotations

from datetime import date, timedelta

import pytest

from options_advisor.broker.models import Greeks, OptionChain, OptionContract
from options_advisor.config import SimulatorSettings
from options_advisor.simulator import positions
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 3, 2)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    repo.init_simulated_account(c, 100_000.0, __import__("datetime").datetime(2026, 3, 1))
    return c


def _settings(**overrides) -> SimulatorSettings:
    defaults = dict(
        enabled=True,
        initial_capital=100_000.0,
        max_position_pct=0.10,
        profit_target_pct=0.30,
        dte_range=(30, 45),
        max_delta=0.18,
        rsi_range=(30.0, 40.0),
        iv_rank_min=50.0,
        iv_percentile_min=50.0,
        support_max_distance_pct=0.05,
        weekly_support_max_distance_pct=0.09,
        sma_periods=[8, 20, 50],
        sma_min_distance_pct=0.03,
    )
    defaults.update(overrides)
    return SimulatorSettings(**defaults)


def _put(strike: float, expiration: date, mid: float) -> OptionContract:
    half_spread = 0.05
    return OptionContract(
        symbol="TST",
        option_type="put",
        strike=strike,
        expiration=expiration,
        bid=round(mid - half_spread, 2),
        ask=round(mid + half_spread, 2),
        last_price=mid,
        implied_volatility=0.30,
        open_interest=500,
        volume=50,
        greeks=Greeks(delta=-0.15, gamma=0.01, theta=-0.02, vega=0.05, rho=0.01, source="calculated"),
    )


def test_size_position_limits_to_max_pct_of_equity():
    result = positions.size_position(strike=100.0, cash_available=100_000.0, account_equity=100_000.0, settings=_settings(max_position_pct=0.10))
    assert result is not None
    assert result.quantity == 1  # 10% de 100k = 10,000 / (100*100 por contrato) = 1 contrato
    assert result.collateral == 10_000.0


def test_size_position_none_when_strike_too_expensive_for_any_contract():
    result = positions.size_position(strike=5000.0, cash_available=100_000.0, account_equity=100_000.0, settings=_settings(max_position_pct=0.01))
    assert result is None


def test_size_position_capped_by_available_cash_even_if_equity_allows_more():
    # 50% de 100k de equity permitiría 5 contratos (50,000 / 10,000 por contrato), pero solo
    # hay 25,000 de cash libre — se recorta a 2 contratos (25,000 // 10,000).
    result = positions.size_position(strike=100.0, cash_available=25_000.0, account_equity=100_000.0, settings=_settings(max_position_pct=0.50))
    assert result is not None
    assert result.quantity == 2
    assert result.collateral == 20_000.0


def test_open_position_reserves_collateral_and_credits_premium(conn):
    contract = _put(strike=80.0, expiration=AS_OF + timedelta(days=35), mid=1.50)
    positions.open_position(conn, "TST", contract, quantity=3, collateral=24_000.0, entry_date=AS_OF)

    account = repo.get_simulated_account(conn)
    # 100,000 - 24,000 (garantía) + 1.50*100*3 (prima) = 76,450
    assert account["cash"] == pytest.approx(76_450.0)

    open_positions = repo.get_open_simulated_positions(conn, "TST")
    assert len(open_positions) == 1
    assert open_positions[0]["entry_premium"] == 1.50
    assert open_positions[0]["status"] == "open"


def test_mark_position_closes_at_profit_target(conn):
    contract = _put(strike=80.0, expiration=AS_OF + timedelta(days=35), mid=2.00)
    position_id = positions.open_position(conn, "TST", contract, quantity=2, collateral=16_000.0, entry_date=AS_OF)
    row = repo.get_open_simulated_positions(conn, "TST")[0]
    assert row["id"] == position_id

    # El valor actual del put cayó a 1.30 (35% menos que la prima cobrada de 2.00) — supera el 30%.
    chain = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=90.0, contracts=[_put(80.0, contract.expiration, 1.30)])
    outcome = positions.mark_position(conn, row, chain, underlying_price=90.0, as_of=AS_OF + timedelta(days=5), settings=_settings())

    assert outcome["closed"] is True
    assert outcome["reason"] == "profit_target"
    closed = repo.get_closed_simulated_positions(conn)
    assert len(closed) == 1
    assert closed[0]["realized_pnl"] == pytest.approx((2.00 - 1.30) * 100 * 2)
    assert repo.get_open_simulated_positions(conn, "TST") == []


def test_mark_position_closes_at_expiration_using_intrinsic_value(conn):
    expiration = AS_OF + timedelta(days=30)
    contract = _put(strike=80.0, expiration=expiration, mid=1.00)
    positions.open_position(conn, "TST", contract, quantity=1, collateral=8_000.0, entry_date=AS_OF)
    row = repo.get_open_simulated_positions(conn, "TST")[0]

    # Vencimiento ya pasó, contrato ITM (precio 75 < strike 80) — se cierra al valor intrínseco.
    outcome = positions.mark_position(conn, row, None, underlying_price=75.0, as_of=expiration, settings=_settings())
    assert outcome["closed"] is True
    assert outcome["reason"] == "expired"
    assert outcome["current_value"] == 5.0  # max(80-75, 0)


def test_mark_position_stays_open_when_no_trigger(conn):
    expiration = AS_OF + timedelta(days=35)
    contract = _put(strike=80.0, expiration=expiration, mid=1.00)
    positions.open_position(conn, "TST", contract, quantity=1, collateral=8_000.0, entry_date=AS_OF)
    row = repo.get_open_simulated_positions(conn, "TST")[0]

    chain = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=95.0, contracts=[_put(80.0, expiration, 0.95)])
    outcome = positions.mark_position(conn, row, chain, underlying_price=95.0, as_of=AS_OF + timedelta(days=2), settings=_settings())

    assert outcome["closed"] is False
    assert repo.get_open_simulated_positions(conn, "TST") != []
    marked = repo.get_open_simulated_positions(conn, "TST")[0]
    assert marked["last_unrealized_pnl"] == pytest.approx((1.00 - 0.95) * 100)


def test_current_contract_value_falls_back_to_intrinsic_when_missing_from_chain():
    expiration = AS_OF + timedelta(days=30)
    empty_chain = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=70.0, contracts=[])
    value = positions.current_contract_value(empty_chain, strike=80.0, expiration=expiration, underlying_price=70.0)
    assert value == 10.0  # max(80-70, 0)
