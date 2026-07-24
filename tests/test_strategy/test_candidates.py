from __future__ import annotations

from datetime import date, timedelta

import pytest
from py_vollib.black_scholes_merton import black_scholes_merton

from options_advisor.broker.models import OptionChain, OptionContract
from options_advisor.indicators.greeks import calculate_greeks
from options_advisor.strategy import constants as c
from options_advisor.strategy.candidates import _coverage_pct, _has_good_support, build_candidate

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


def test_lower_target_short_delta_picks_more_otm_put_strike(chain):
    """Perfil conservador (delta más bajo) debería elegir un strike más lejos del precio
    actual que perfil agresivo (delta más alto) — más colchón, menos prima."""
    conservative = build_candidate(c.CASH_SECURED_PUT, chain, target_short_delta=0.15)
    aggressive = build_candidate(c.CASH_SECURED_PUT, chain, target_short_delta=0.35)
    assert conservative is not None and aggressive is not None
    conservative_strike = conservative.strikes["short_strike"]
    aggressive_strike = aggressive.strikes["short_strike"]
    # Put: strike más OTM = más bajo (más lejos de UNDERLYING_PRICE hacia abajo).
    assert conservative_strike < aggressive_strike
    assert (UNDERLYING_PRICE - conservative_strike) > (UNDERLYING_PRICE - aggressive_strike)


def test_lower_target_short_delta_picks_more_otm_collar_strikes(chain):
    conservative = build_candidate(c.COLLAR, chain, target_short_delta=0.15)
    aggressive = build_candidate(c.COLLAR, chain, target_short_delta=0.35)
    assert conservative is not None and aggressive is not None
    # Call vendida: más OTM = strike más alto para conservador que para agresivo.
    assert conservative.strikes["call_strike"] > aggressive.strikes["call_strike"]
    # Put comprada: más OTM = strike más bajo para conservador que para agresivo.
    assert conservative.strikes["put_strike"] < aggressive.strikes["put_strike"]


def test_lower_target_short_delta_widens_iron_condor(chain):
    conservative = build_candidate(c.IRON_CONDOR, chain, target_short_delta=0.15)
    aggressive = build_candidate(c.IRON_CONDOR, chain, target_short_delta=0.35)
    assert conservative is not None and aggressive is not None
    assert conservative.strikes["call_short_strike"] > aggressive.strikes["call_short_strike"]
    assert conservative.strikes["put_short_strike"] < aggressive.strikes["put_short_strike"]


def test_build_candidate_default_target_short_delta_matches_module_constant(chain):
    """Sin pasar target_short_delta explícito, se comporta igual que antes de este parámetro."""
    from options_advisor.strategy.candidates import TARGET_SHORT_DELTA

    default_call = build_candidate(c.CASH_SECURED_PUT, chain)
    explicit_call = build_candidate(c.CASH_SECURED_PUT, chain, target_short_delta=TARGET_SHORT_DELTA)
    assert default_call.strikes == explicit_call.strikes


# --- Refinamiento de selección por perfil de riesgo (cobertura mínima + soporte técnico,
# 2026-07-24) --- UNDERLYING_PRICE=100.0, strikes disponibles en `chain`: 80/90/95/97.5/100/
# 102.5/105/110/120 para cada tipo de opción y vencimiento.


def test_coverage_pct_put_and_call():
    assert _coverage_pct("put", strike=90.0, underlying_price=100.0) == pytest.approx(0.10)
    assert _coverage_pct("call", strike=110.0, underlying_price=100.0) == pytest.approx(0.10)


def test_has_good_support_put_strike_below_sma_and_price_above():
    assert _has_good_support("put", strike=90.0, underlying_price=100.0, support_sma_values=[95.0]) is True


def test_has_good_support_put_strike_above_sma_fails():
    assert _has_good_support("put", strike=97.5, underlying_price=100.0, support_sma_values=[95.0]) is False


def test_has_good_support_call_is_symmetric_with_resistance():
    assert _has_good_support("call", strike=110.0, underlying_price=100.0, support_sma_values=[105.0]) is True
    assert _has_good_support("call", strike=102.5, underlying_price=100.0, support_sma_values=[105.0]) is False


def test_has_good_support_any_sma_in_list_is_enough():
    # SMA8=97.5 no sirve de piso (muy cerca), pero SMA20=95 sí — alcanza con que UNA confirme.
    assert _has_good_support("put", strike=90.0, underlying_price=100.0, support_sma_values=[97.5, 95.0]) is True


def test_has_good_support_empty_or_none_values_means_not_applicable():
    assert _has_good_support("put", strike=97.5, underlying_price=100.0, support_sma_values=[]) is True
    assert _has_good_support("put", strike=97.5, underlying_price=100.0, support_sma_values=[None, None]) is True


def test_build_candidate_min_coverage_pushes_strike_further_otm(chain):
    """Sin restricción, un delta objetivo cercano al dinero (0.40) elegiría un strike cerca de
    97.5. Con cobertura mínima de 12%, debe saltar a un strike que si cumpla (80, el único a
    ≥12% de 100) en vez de descartar el candidato — pedido explícito del usuario: buscar el
    siguiente strike, no descartar."""
    unconstrained = build_candidate(c.CASH_SECURED_PUT, chain, target_short_delta=0.40)
    constrained = build_candidate(c.CASH_SECURED_PUT, chain, target_short_delta=0.40, min_coverage_pct=0.12)
    assert unconstrained is not None and constrained is not None
    assert unconstrained.strikes["short_strike"] > constrained.strikes["short_strike"]
    assert _coverage_pct("put", constrained.strikes["short_strike"], UNDERLYING_PRICE) >= 0.12


def test_build_candidate_support_requirement_filters_out_strike_above_sma(chain):
    """SMA de referencia en 95: un delta objetivo que caería en 97.5 debe saltar a 90 (o más
    OTM) para respetar que el strike quede por debajo de la SMA."""
    build = build_candidate(c.CASH_SECURED_PUT, chain, target_short_delta=0.40, support_sma_values=[95.0])
    assert build is not None
    assert build.strikes["short_strike"] <= 95.0


def test_build_candidate_covered_call_support_uses_resistance(chain):
    build = build_candidate(c.COVERED_CALL, chain, target_short_delta=0.40, support_sma_values=[105.0])
    assert build is not None
    assert build.strikes["short_strike"] >= 105.0


def test_build_candidate_iron_condor_applies_support_to_both_sold_legs(chain):
    build = build_candidate(c.IRON_CONDOR, chain, target_short_delta=0.40, support_sma_values=[95.0, 105.0])
    assert build is not None
    assert build.strikes["put_short_strike"] <= 95.0
    assert build.strikes["call_short_strike"] >= 105.0


def test_build_candidate_collar_support_applies_only_to_sold_call(chain):
    """El put comprado (protección) no tiene restricción de soporte — solo la call vendida."""
    build = build_candidate(c.COLLAR, chain, target_short_delta=0.40, support_sma_values=[105.0])
    assert build is not None
    assert build.strikes["call_strike"] >= 105.0


def test_build_candidate_returns_none_when_no_strike_satisfies_min_coverage(chain):
    """Ningún strike de la cadena de prueba llega a 50% de cobertura (el más lejano es 20%)."""
    assert build_candidate(c.CASH_SECURED_PUT, chain, target_short_delta=0.40, min_coverage_pct=0.50) is None


def test_build_candidate_zero_constraints_matches_unconstrained_behavior(chain):
    """min_coverage_pct=0.0 y support_sma_values=None (los defaults) deben dar exactamente el
    mismo resultado que no pasar estos parámetros — sin cambio de comportamiento para las
    estrategias que no threadean el refinamiento (Bull Put Spread, etc., y como red de
    seguridad para las que sí)."""
    plain = build_candidate(c.CASH_SECURED_PUT, chain, target_short_delta=0.25)
    explicit_defaults = build_candidate(c.CASH_SECURED_PUT, chain, target_short_delta=0.25, min_coverage_pct=0.0, support_sma_values=None)
    assert plain.strikes == explicit_defaults.strikes


def test_build_candidate_non_mvp_strategy_ignores_new_params(chain):
    """Bull Put Spread no threadea min_coverage_pct/support_sma_values — build_candidate no le
    pasa estos parámetros aunque se le den a la función top-level (quedan sin usar para esa
    estrategia), mismo comportamiento que antes del refinamiento."""
    without = build_candidate(c.BULL_PUT_SPREAD, chain, target_short_delta=0.25)
    with_constraints = build_candidate(c.BULL_PUT_SPREAD, chain, target_short_delta=0.25, min_coverage_pct=0.30, support_sma_values=[999.0])
    assert without.strikes == with_constraints.strikes
