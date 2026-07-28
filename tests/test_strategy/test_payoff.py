from __future__ import annotations

from datetime import date, timedelta

import pytest

from options_advisor.broker.models import Greeks, OptionContract
from options_advisor.strategy import constants as c
from options_advisor.strategy import payoff
from options_advisor.strategy.candidates import CandidateBuild, Leg

AS_OF = date(2026, 1, 1)
RISK_FREE_RATE = 0.045

_DUMMY_GREEKS = Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0, source="broker")


def _contract(option_type: str, strike: float, mid: float, dte: int, iv: float = 0.3) -> OptionContract:
    half_spread = 0.05
    return OptionContract(
        symbol="TST",
        option_type=option_type,
        strike=strike,
        expiration=AS_OF + timedelta(days=dte),
        bid=round(mid - half_spread, 2),
        ask=round(mid + half_spread, 2),
        last_price=mid,
        implied_volatility=iv,
        open_interest=100,
        volume=10,
        greeks=_DUMMY_GREEKS,
    )


def test_cash_secured_put():
    short = _contract("put", strike=100.0, mid=2.0, dte=30)
    build = CandidateBuild(
        strategy_type=c.CASH_SECURED_PUT,
        expiration_date=short.expiration,
        strikes={"short_strike": 100.0},
        net_greeks={},
        greeks_source="broker",
        legs=[Leg("sell", short)],
    )
    result = payoff.compute_payoff(build, underlying_price=105.0, as_of=AS_OF, risk_free_rate=RISK_FREE_RATE)

    assert result.net_premium == 200.0
    assert result.max_profit == 200.0
    assert result.max_loss == pytest.approx((100.0 - 2.0) * 100, abs=0.01)
    assert result.breakevens == [98.0]
    assert result.dte == 30
    assert 0.0 < result.probability_of_profit < 1.0
    assert result.is_estimate is False

    # Rendimiento anualizado: (200 / 9800) * (365/30) * 100 — pedido 2026-07-24.
    assert result.annualized_return_pct == pytest.approx((200.0 / 9800.0) * (365 / 30) * 100, abs=0.01)

    # Cierre anticipado (pedido 2026-07-24): con el precio bien por encima del strike (105 vs
    # 100), el 100% del beneficio máximo solo se alcanza exactamente al vencimiento (día 30) —
    # antes de eso siempre queda valor extrínseco en la pata vendida. 30%/50% se alcanzan antes.
    projection = {p["pct"]: p["days"] for p in result.early_close_projection}
    assert set(projection) == {30, 50, 100}
    assert projection[100] == 30
    assert projection[30] is not None and projection[50] is not None

    # Volume/open_interest copiados del contrato (pedido 2026-07-27, vista tabla en Escaneo) —
    # antes _leg_dict no los copiaba, se perdían al persistir en candidate_contracts.legs_json.
    assert result.legs[0]["open_interest"] == 100
    assert result.legs[0]["volume"] == 10


# --- probability_otm (Sección 'Pestaña Screener', pedido 2026-07-27) ---


def test_probability_otm_put_deep_otm_is_high():
    """Put con strike MUY por debajo del precio (deep OTM): muy probable que el precio termine
    por ENCIMA del strike (el put vence sin valor) — probability_otm alta."""
    prob = payoff.probability_otm("put", underlying_price=100.0, strike=50.0, dte=30, risk_free_rate=RISK_FREE_RATE, sigma=0.30)
    assert prob > 0.95


def test_probability_otm_put_deep_itm_is_low():
    """Put con strike MUY por encima del precio (deep ITM): muy improbable que el precio suba
    tanto como para terminar por encima del strike — probability_otm baja."""
    prob = payoff.probability_otm("put", underlying_price=100.0, strike=150.0, dte=30, risk_free_rate=RISK_FREE_RATE, sigma=0.30)
    assert prob < 0.05


def test_probability_otm_call_deep_otm_is_high():
    """Call con strike MUY por encima del precio (deep OTM): muy probable que el precio termine
    por DEBAJO del strike (la call vence sin valor) — probability_otm alta."""
    prob = payoff.probability_otm("call", underlying_price=100.0, strike=150.0, dte=30, risk_free_rate=RISK_FREE_RATE, sigma=0.30)
    assert prob > 0.95


def test_probability_otm_call_deep_itm_is_low():
    prob = payoff.probability_otm("call", underlying_price=100.0, strike=50.0, dte=30, risk_free_rate=RISK_FREE_RATE, sigma=0.30)
    assert prob < 0.05


def test_probability_otm_differs_from_probability_of_profit():
    """Para el mismo cash-secured put del test de arriba (strike 100, precio 105), POP mide
    contra el breakeven (98, más lejos) y probability_otm mide contra el strike (100, más
    cerca) — probability_otm SIEMPRE <= POP para un put vendido, nunca deberían coincidir por
    casualidad con estos números."""
    short = _contract("put", strike=100.0, mid=2.0, dte=30)
    build = CandidateBuild(
        strategy_type=c.CASH_SECURED_PUT,
        expiration_date=short.expiration,
        strikes={"short_strike": 100.0},
        net_greeks={},
        greeks_source="broker",
        legs=[Leg("sell", short)],
    )
    result = payoff.compute_payoff(build, underlying_price=105.0, as_of=AS_OF, risk_free_rate=RISK_FREE_RATE)
    otm_prob = payoff.probability_otm("put", underlying_price=105.0, strike=100.0, dte=30, risk_free_rate=RISK_FREE_RATE, sigma=0.3)
    assert otm_prob < result.probability_of_profit


def test_bull_put_spread():
    short = _contract("put", strike=100.0, mid=2.0, dte=30)
    long = _contract("put", strike=95.0, mid=1.0, dte=30)
    build = CandidateBuild(
        strategy_type=c.BULL_PUT_SPREAD,
        expiration_date=short.expiration,
        strikes={"short_strike": 100.0, "long_strike": 95.0},
        net_greeks={},
        greeks_source="broker",
        legs=[Leg("sell", short), Leg("buy", long)],
    )
    result = payoff.compute_payoff(build, underlying_price=105.0, as_of=AS_OF, risk_free_rate=RISK_FREE_RATE)

    assert result.net_premium == 100.0
    assert result.max_profit == 100.0
    assert result.max_loss == pytest.approx(5 * 100 - 100, abs=0.01)
    assert result.breakevens == [99.0]


def test_iron_condor():
    put_short = _contract("put", strike=100.0, mid=2.0, dte=30)
    put_long = _contract("put", strike=95.0, mid=1.0, dte=30)
    call_short = _contract("call", strike=110.0, mid=2.0, dte=30)
    call_long = _contract("call", strike=115.0, mid=1.0, dte=30)
    build = CandidateBuild(
        strategy_type=c.IRON_CONDOR,
        expiration_date=put_short.expiration,
        strikes={
            "put_short_strike": 100.0,
            "put_long_strike": 95.0,
            "call_short_strike": 110.0,
            "call_long_strike": 115.0,
        },
        net_greeks={},
        greeks_source="broker",
        legs=[Leg("sell", put_short), Leg("buy", put_long), Leg("sell", call_short), Leg("buy", call_long)],
    )
    result = payoff.compute_payoff(build, underlying_price=105.0, as_of=AS_OF, risk_free_rate=RISK_FREE_RATE)

    assert result.net_premium == 200.0
    assert result.max_profit == 200.0
    assert result.max_loss == pytest.approx(5 * 100 - 200, abs=0.01)
    assert result.breakevens == [98.0, 112.0]
    assert 0.0 < result.probability_of_profit < 1.0


def test_covered_call():
    short = _contract("call", strike=110.0, mid=2.0, dte=30)
    build = CandidateBuild(
        strategy_type=c.COVERED_CALL,
        expiration_date=short.expiration,
        strikes={"short_strike": 110.0},
        net_greeks={},
        greeks_source="broker",
        legs=[Leg("sell", short)],
    )
    result = payoff.compute_payoff(build, underlying_price=105.0, as_of=AS_OF, risk_free_rate=RISK_FREE_RATE)

    assert result.net_premium == 200.0
    assert result.max_profit == pytest.approx((2.0 + 5.0) * 100, abs=0.01)
    assert result.breakevens == [103.0]
    assert result.max_loss == pytest.approx((105.0 - 2.0) * 100, abs=0.01)


@pytest.mark.parametrize("strategy_type,same_strike", [(c.CALENDAR_PUT_SPREAD, True), (c.DIAGONAL_PUT_SPREAD, False)])
def test_calendar_and_diagonal_estimate(strategy_type, same_strike):
    near = _contract("put", strike=100.0, mid=1.5, dte=25, iv=0.30)
    far_strike = 100.0 if same_strike else 95.0
    far = _contract("put", strike=far_strike, mid=2.5, dte=50, iv=0.32)
    build = CandidateBuild(
        strategy_type=strategy_type,
        expiration_date=far.expiration,
        strikes={
            "near_expiration": near.expiration.isoformat(),
            "near_strike": near.strike,
            "far_expiration": far.expiration.isoformat(),
            "far_strike": far.strike,
        },
        net_greeks={},
        greeks_source="broker",
        legs=[Leg("sell", near), Leg("buy", far)],
    )
    result = payoff.compute_payoff(build, underlying_price=100.0, as_of=AS_OF, risk_free_rate=RISK_FREE_RATE)

    assert result.net_premium == pytest.approx((1.5 - 2.5) * 100, abs=0.01)  # débito neto
    assert result.is_estimate is True
    assert result.dte == 25  # DTE relevante es el de la pata corta (near)
    assert result.max_profit >= 0.0
    assert result.max_loss >= 0.0
    assert result.legs[0]["side"] == "sell"
    assert result.legs[1]["side"] == "buy"


def test_call_ratio_backspread_has_unbounded_profit():
    short = _contract("call", strike=100.0, mid=3.0, dte=30)
    long = _contract("call", strike=110.0, mid=1.0, dte=30)
    build = CandidateBuild(
        strategy_type=c.CALL_RATIO_BACKSPREAD,
        expiration_date=short.expiration,
        strikes={"short_strike": 100.0, "long_strike": 110.0},
        net_greeks={},
        greeks_source="broker",
        legs=[Leg("sell", short, 1), Leg("buy", long, 2)],
    )
    result = payoff.compute_payoff(build, underlying_price=100.0, as_of=AS_OF, risk_free_rate=RISK_FREE_RATE)

    assert result.net_premium == 100.0
    assert result.max_profit == float("inf")
    assert result.max_loss == pytest.approx(900.0, abs=0.01)
    assert result.breakevens == [101.0, 119.0]


def test_call_ratio_spread_has_unbounded_loss():
    long = _contract("call", strike=100.0, mid=3.0, dte=30)
    short = _contract("call", strike=110.0, mid=1.0, dte=30)
    build = CandidateBuild(
        strategy_type=c.CALL_RATIO_SPREAD,
        expiration_date=long.expiration,
        strikes={"long_strike": 100.0, "short_strike": 110.0},
        net_greeks={},
        greeks_source="broker",
        legs=[Leg("buy", long, 1), Leg("sell", short, 2)],
    )
    result = payoff.compute_payoff(build, underlying_price=100.0, as_of=AS_OF, risk_free_rate=RISK_FREE_RATE)

    assert result.net_premium == -100.0  # débito neto
    assert result.max_profit == pytest.approx(900.0, abs=0.01)
    assert result.max_loss == float("inf")
    assert result.breakevens == [101.0, 119.0]


def test_collar_caps_both_sides():
    short_call = _contract("call", strike=105.0, mid=2.0, dte=30)
    long_put = _contract("put", strike=95.0, mid=1.5, dte=30)
    build = CandidateBuild(
        strategy_type=c.COLLAR,
        expiration_date=short_call.expiration,
        strikes={"call_strike": 105.0, "put_strike": 95.0},
        net_greeks={},
        greeks_source="broker",
        legs=[Leg("sell", short_call), Leg("buy", long_put)],
    )
    result = payoff.compute_payoff(build, underlying_price=100.0, as_of=AS_OF, risk_free_rate=RISK_FREE_RATE)

    assert result.net_premium == 50.0
    assert result.max_profit == pytest.approx(550.0, abs=0.01)
    assert result.max_loss == pytest.approx(450.0, abs=0.01)
    assert result.breakevens == [99.5]


def test_short_call_condor_profits_in_the_outer_tails():
    k1 = _contract("call", strike=90.0, mid=12.0, dte=30)
    k2 = _contract("call", strike=97.0, mid=6.0, dte=30)
    k3 = _contract("call", strike=103.0, mid=3.0, dte=30)
    k4 = _contract("call", strike=110.0, mid=1.0, dte=30)
    build = CandidateBuild(
        strategy_type=c.SHORT_CALL_CONDOR,
        expiration_date=k1.expiration,
        strikes={"strike_1": 90.0, "strike_2": 97.0, "strike_3": 103.0, "strike_4": 110.0},
        net_greeks={},
        greeks_source="broker",
        legs=[Leg("sell", k1), Leg("buy", k2), Leg("buy", k3), Leg("sell", k4)],
    )
    result = payoff.compute_payoff(build, underlying_price=100.0, as_of=AS_OF, risk_free_rate=RISK_FREE_RATE)

    # net_premium = (12 - 6 - 3 + 1) * 100 = 400 (crédito). A diferencia del Iron Condor, el
    # Short Call Condor (vende afuera, compra adentro) gana con un movimiento grande en
    # cualquier dirección y pierde si el precio queda en el rango central [K2, K3].
    assert result.net_premium == 400.0
    assert result.max_profit == 400.0  # se realiza en las colas (S bajo o S alto)
    assert result.max_loss is not None and result.max_loss != float("inf")  # riesgo acotado en ambos lados
    assert len(result.breakevens) == 2
    assert 0.0 < result.probability_of_profit < 1.0
    mid_price = (90.0 + 110.0) / 2
    assert payoff._pnl_at(build.legs, result.net_premium, mid_price, False, 100.0) < 0  # pierde en el centro


def test_annualized_return_pct_basic():
    assert payoff.annualized_return_pct(max_profit=200.0, max_loss=9800.0, dte=30) == pytest.approx((200 / 9800) * (365 / 30) * 100, abs=0.01)


def test_annualized_return_pct_none_when_loss_unbounded():
    assert payoff.annualized_return_pct(max_profit=200.0, max_loss=float("inf"), dte=30) is None


def test_annualized_return_pct_none_when_loss_zero():
    assert payoff.annualized_return_pct(max_profit=200.0, max_loss=0.0, dte=30) is None


def test_annualized_return_pct_none_when_dte_zero():
    assert payoff.annualized_return_pct(max_profit=200.0, max_loss=9800.0, dte=0) is None


def test_annualized_return_pct_none_when_missing_data():
    assert payoff.annualized_return_pct(max_profit=None, max_loss=9800.0, dte=30) is None
    assert payoff.annualized_return_pct(max_profit=200.0, max_loss=None, dte=30) is None


def test_early_close_projection_empty_without_bounded_max_profit():
    short = _contract("put", strike=100.0, mid=2.0, dte=30)
    assert payoff.early_close_projection([Leg("sell", short)], net_premium=200.0, underlying_price=105.0, dte=30, risk_free_rate=RISK_FREE_RATE, max_profit=None) == []
    assert payoff.early_close_projection([Leg("sell", short)], net_premium=200.0, underlying_price=105.0, dte=0, risk_free_rate=RISK_FREE_RATE, max_profit=200.0) == []


def test_unknown_strategy_raises():
    short = _contract("put", strike=100.0, mid=2.0, dte=30)
    build = CandidateBuild(
        strategy_type="not_a_real_strategy",
        expiration_date=short.expiration,
        strikes={},
        net_greeks={},
        greeks_source="broker",
        legs=[Leg("sell", short)],
    )
    with pytest.raises(ValueError):
        payoff.compute_payoff(build, underlying_price=100.0, as_of=AS_OF, risk_free_rate=RISK_FREE_RATE)
