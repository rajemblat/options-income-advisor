from __future__ import annotations

from datetime import date, timedelta

import pytest
from py_vollib.black_scholes_merton import black_scholes_merton

from options_advisor.broker.models import OptionChain, OptionContract
from options_advisor.indicators.greeks import calculate_greeks
from options_advisor.strategy import constants as c
from options_advisor.strategy.candidates import build_candidate

AS_OF = date(2026, 1, 1)
UNDERLYING_PRICE = 100.0
RISK_FREE_RATE = 0.045
DTE_BUCKETS = (7, 14, 21, 30, 45, 60)
STRIKE_OFFSETS = (-0.20, -0.10, -0.05, -0.025, 0.0, 0.025, 0.05, 0.10, 0.20)


def _build_chain() -> OptionChain:
    """Réplica simplificada de lo que genera scripts/seed_fixtures.py + mock_client.py: una
    cadena con varios vencimientos y strikes por vencimiento, para cada tipo de opción, con
    precios y griegos reales vía Black-Scholes — para ejercitar la selección por delta de
    candidates.py con datos realistas en vez de contratos armados a mano."""
    contracts: list[OptionContract] = []
    for dte in DTE_BUCKETS:
        expiration = AS_OF + timedelta(days=dte)
        t = dte / 365
        for offset in STRIKE_OFFSETS:
            strike = round(UNDERLYING_PRICE * (1 + offset), 1)
            iv = 0.28
            for option_type in ("call", "put"):
                flag = "c" if option_type == "call" else "p"
                theo = black_scholes_merton(flag, UNDERLYING_PRICE, strike, t, RISK_FREE_RATE, iv, 0.0)
                half_spread = max(0.01, round(theo * 0.02 / 2, 2))
                greeks = calculate_greeks(option_type, UNDERLYING_PRICE, strike, expiration, AS_OF, iv, RISK_FREE_RATE)
                contracts.append(
                    OptionContract(
                        symbol="TST",
                        option_type=option_type,
                        strike=strike,
                        expiration=expiration,
                        bid=round(max(0.01, theo - half_spread), 2),
                        ask=round(theo + half_spread, 2),
                        last_price=round(theo, 2),
                        implied_volatility=iv,
                        open_interest=500,
                        volume=50,
                        greeks=greeks,
                    )
                )
    return OptionChain(symbol="TST", as_of=AS_OF, underlying_price=UNDERLYING_PRICE, contracts=contracts)


ALL_19_STRATEGIES = sorted(c.ALL_INCOME_STRATEGIES)


@pytest.fixture(scope="module")
def chain() -> OptionChain:
    return _build_chain()


@pytest.mark.parametrize("strategy_type", ALL_19_STRATEGIES)
def test_build_candidate_for_every_strategy(chain, strategy_type):
    """Confirma que las 19 estrategias se pueden construir contra una cadena realista, sin
    excepciones, y que cada candidato trae al menos una pata."""
    build = build_candidate(strategy_type, chain)
    assert build is not None, f"{strategy_type} no pudo construirse contra la cadena de prueba"
    assert build.strategy_type == strategy_type
    assert len(build.legs) >= 1


def test_ratio_backspread_buys_two_and_sells_one(chain):
    build = build_candidate(c.CALL_RATIO_BACKSPREAD, chain)
    assert build is not None
    sell_legs = [leg for leg in build.legs if leg.side == "sell"]
    buy_legs = [leg for leg in build.legs if leg.side == "buy"]
    assert sum(leg.quantity for leg in sell_legs) == 1
    assert sum(leg.quantity for leg in buy_legs) == 2


def test_ratio_front_spread_buys_one_and_sells_two(chain):
    build = build_candidate(c.PUT_RATIO_SPREAD, chain)
    assert build is not None
    sell_legs = [leg for leg in build.legs if leg.side == "sell"]
    buy_legs = [leg for leg in build.legs if leg.side == "buy"]
    assert sum(leg.quantity for leg in buy_legs) == 1
    assert sum(leg.quantity for leg in sell_legs) == 2


@pytest.mark.parametrize("strategy_type", [c.SHORT_CALL_CONDOR, c.SHORT_PUT_CONDOR])
def test_short_condor_has_four_distinct_strikes_sell_buy_buy_sell(chain, strategy_type):
    build = build_candidate(strategy_type, chain)
    assert build is not None
    assert len(build.legs) == 4
    strikes = [leg.contract.strike for leg in build.legs]
    assert strikes == sorted(strikes)
    assert len(set(strikes)) == 4
    assert [leg.side for leg in build.legs] == ["sell", "buy", "buy", "sell"]


def test_collar_sells_call_and_buys_put(chain):
    build = build_candidate(c.COLLAR, chain)
    assert build is not None
    sides_by_type = {leg.contract.option_type: leg.side for leg in build.legs}
    assert sides_by_type == {"call": "sell", "put": "buy"}


def test_bear_call_spread_sells_lower_strike_buys_higher(chain):
    build = build_candidate(c.BEAR_CALL_SPREAD, chain)
    assert build is not None
    short = next(leg for leg in build.legs if leg.side == "sell")
    long = next(leg for leg in build.legs if leg.side == "buy")
    assert short.contract.strike < long.contract.strike


def test_bull_call_spread_is_a_debit(chain):
    build = build_candidate(c.BULL_CALL_SPREAD, chain)
    assert build is not None
    long = next(leg for leg in build.legs if leg.side == "buy")
    short = next(leg for leg in build.legs if leg.side == "sell")
    assert long.contract.strike < short.contract.strike
    assert long.contract.mid_price > short.contract.mid_price  # neto débito


def test_calendar_call_spread_has_two_expirations(chain):
    build = build_candidate(c.CALENDAR_CALL_SPREAD, chain)
    assert build is not None
    expirations = {leg.contract.expiration for leg in build.legs}
    assert len(expirations) == 2
    assert all(leg.contract.option_type == "call" for leg in build.legs)


def test_unknown_strategy_raises(chain):
    with pytest.raises(ValueError):
        build_candidate("not_a_real_strategy", chain)
