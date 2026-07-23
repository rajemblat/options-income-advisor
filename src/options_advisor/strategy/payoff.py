from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import log, sqrt
from statistics import NormalDist

from py_vollib.black_scholes_merton import black_scholes_merton

from options_advisor.strategy import constants as c
from options_advisor.strategy.candidates import CandidateBuild, Leg

_NORMAL = NormalDist()
_CONTRACT_MULTIPLIER = 100

# Estrategia con vencimientos combinados (calendar/diagonal): no hay fórmula cerrada para
# beneficio máximo/breakeven, se estima proyectando la pata lejana con Black-Scholes sobre
# una grilla de precios al vencimiento de la pata cercana (Sección 4 del plan, mismo patrón
# de "aproximación documentada" que ya usa el bootstrap de IV Rank).
_GRID_POINTS = 400
_GRID_RANGE = (0.4, 1.8)  # múltiplos del precio actual del subyacente


@dataclass
class PayoffResult:
    legs: list[dict]
    net_premium: float  # positivo = crédito recibido, negativo = débito pagado
    max_profit: float
    max_loss: float  # siempre positivo: dólares en riesgo
    breakevens: list[float] = field(default_factory=list)
    probability_of_profit: float | None = None
    dte: int = 0
    underlying_price: float = 0.0
    is_estimate: bool = False


def _leg_dict(leg: Leg) -> dict:
    ct = leg.contract
    return {
        "side": leg.side,
        "option_type": ct.option_type,
        "strike": ct.strike,
        "expiration": ct.expiration.isoformat(),
        "premium": ct.mid_price,
        "implied_volatility": ct.implied_volatility,
    }


def _prob_above(underlying_price: float, target: float, years: float, risk_free_rate: float, sigma: float) -> float:
    """Probabilidad risk-neutral (Black-Scholes) de que el subyacente termine por encima de
    `target` al cabo de `years`. P(S_T > K) = N(d2) — vale para calls y puts por igual, es una
    propiedad de la distribución lognormal del precio, no del tipo de contrato."""
    if years <= 0 or sigma <= 0:
        return 1.0 if underlying_price > target else 0.0
    d1 = (log(underlying_price / target) + (risk_free_rate + 0.5 * sigma**2) * years) / (sigma * sqrt(years))
    d2 = d1 - sigma * sqrt(years)
    return _NORMAL.cdf(d2)


def _prob_below(underlying_price: float, target: float, years: float, risk_free_rate: float, sigma: float) -> float:
    return 1 - _prob_above(underlying_price, target, years, risk_free_rate, sigma)


def _leg(legs: list[Leg], side: str, option_type: str | None = None) -> Leg:
    for leg in legs:
        if leg.side == side and (option_type is None or leg.contract.option_type == option_type):
            return leg
    raise ValueError(f"No se encontró pata side={side} option_type={option_type}")


def _payoff_single_short_put(legs: list[Leg], underlying_price: float, dte: int, risk_free_rate: float) -> PayoffResult:
    short = legs[0].contract
    premium = short.mid_price
    net_premium = premium * _CONTRACT_MULTIPLIER
    breakeven = short.strike - premium
    years = dte / 365
    pop = _prob_above(underlying_price, breakeven, years, risk_free_rate, short.implied_volatility)
    return PayoffResult(
        legs=[_leg_dict(legs[0])],
        net_premium=round(net_premium, 2),
        max_profit=round(net_premium, 2),
        max_loss=round((short.strike - premium) * _CONTRACT_MULTIPLIER, 2),
        breakevens=[round(breakeven, 2)],
        probability_of_profit=round(pop, 4),
        dte=dte,
        underlying_price=underlying_price,
    )


def _payoff_covered_call(legs: list[Leg], underlying_price: float, dte: int, risk_free_rate: float) -> PayoffResult:
    short = legs[0].contract
    premium = short.mid_price
    net_premium = premium * _CONTRACT_MULTIPLIER
    # Asume que las 100 acciones se adquieren/valúan al precio actual (Fase 1 no rastrea
    # cost basis real hasta que hay una asignación registrada en `assigned_positions`).
    breakeven = underlying_price - premium
    max_profit = (premium + max(0.0, short.strike - underlying_price)) * _CONTRACT_MULTIPLIER
    max_loss = max(0.0, underlying_price - premium) * _CONTRACT_MULTIPLIER
    years = dte / 365
    pop = _prob_above(underlying_price, breakeven, years, risk_free_rate, short.implied_volatility)
    return PayoffResult(
        legs=[_leg_dict(legs[0])],
        net_premium=round(net_premium, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakevens=[round(breakeven, 2)],
        probability_of_profit=round(pop, 4),
        dte=dte,
        underlying_price=underlying_price,
    )


def _payoff_vertical_credit_spread(legs: list[Leg], underlying_price: float, dte: int, risk_free_rate: float) -> PayoffResult:
    short_leg, long_leg = _leg(legs, "sell"), _leg(legs, "buy")
    short_ct, long_ct = short_leg.contract, long_leg.contract
    net_premium = (short_ct.mid_price - long_ct.mid_price) * _CONTRACT_MULTIPLIER
    width = abs(short_ct.strike - long_ct.strike)
    breakeven = short_ct.strike - (net_premium / _CONTRACT_MULTIPLIER)
    years = dte / 365
    pop = _prob_above(underlying_price, breakeven, years, risk_free_rate, short_ct.implied_volatility)
    return PayoffResult(
        legs=[_leg_dict(short_leg), _leg_dict(long_leg)],
        net_premium=round(net_premium, 2),
        max_profit=round(net_premium, 2),
        max_loss=round(width * _CONTRACT_MULTIPLIER - net_premium, 2),
        breakevens=[round(breakeven, 2)],
        probability_of_profit=round(pop, 4),
        dte=dte,
        underlying_price=underlying_price,
    )


def _payoff_iron_condor(legs: list[Leg], underlying_price: float, dte: int, risk_free_rate: float) -> PayoffResult:
    put_short, put_long = _leg(legs, "sell", "put"), _leg(legs, "buy", "put")
    call_short, call_long = _leg(legs, "sell", "call"), _leg(legs, "buy", "call")

    net_premium = (
        put_short.contract.mid_price
        - put_long.contract.mid_price
        + call_short.contract.mid_price
        - call_long.contract.mid_price
    ) * _CONTRACT_MULTIPLIER
    put_wing = abs(put_short.contract.strike - put_long.contract.strike)
    call_wing = abs(call_long.contract.strike - call_short.contract.strike)
    credit_per_share = net_premium / _CONTRACT_MULTIPLIER
    lower_breakeven = put_short.contract.strike - credit_per_share
    upper_breakeven = call_short.contract.strike + credit_per_share

    years = dte / 365
    p_below_lower = _prob_below(underlying_price, lower_breakeven, years, risk_free_rate, put_short.contract.implied_volatility)
    p_above_upper = _prob_above(underlying_price, upper_breakeven, years, risk_free_rate, call_short.contract.implied_volatility)
    pop = max(0.0, 1 - p_below_lower - p_above_upper)

    return PayoffResult(
        legs=[_leg_dict(put_short), _leg_dict(put_long), _leg_dict(call_short), _leg_dict(call_long)],
        net_premium=round(net_premium, 2),
        max_profit=round(net_premium, 2),
        max_loss=round(max(put_wing, call_wing) * _CONTRACT_MULTIPLIER - net_premium, 2),
        breakevens=[round(lower_breakeven, 2), round(upper_breakeven, 2)],
        probability_of_profit=round(pop, 4),
        dte=dte,
        underlying_price=underlying_price,
    )


def _payoff_calendar(legs: list[Leg], underlying_price: float, as_of: date, risk_free_rate: float) -> PayoffResult:
    short_leg, long_leg = _leg(legs, "sell"), _leg(legs, "buy")
    near, far = short_leg.contract, long_leg.contract
    # DTE relevante para esta estrategia es el de la pata corta (near): es el primer
    # punto de decisión/gestión de la posición, no el vencimiento de la pata larga.
    dte = (near.expiration - as_of).days

    near_credit = near.mid_price
    far_debit = far.mid_price
    net_premium = (near_credit - far_debit) * _CONTRACT_MULTIPLIER

    t_gap = max((far.expiration - near.expiration).days, 1) / 365
    far_sigma = far.implied_volatility

    def pnl_at(price: float) -> float:
        near_payoff = near_credit - max(0.0, near.strike - price)
        far_value = black_scholes_merton("p", max(price, 0.01), far.strike, t_gap, risk_free_rate, far_sigma, 0.0)
        far_payoff = far_value - far_debit
        return (near_payoff + far_payoff) * _CONTRACT_MULTIPLIER

    lo, hi = underlying_price * _GRID_RANGE[0], underlying_price * _GRID_RANGE[1]
    step = (hi - lo) / _GRID_POINTS
    prices = [lo + i * step for i in range(_GRID_POINTS + 1)]
    pnls = [pnl_at(p) for p in prices]

    max_profit = max(max(pnls), 0.0)
    max_loss = abs(min(min(pnls), 0.0))

    breakevens: list[float] = []
    for i in range(len(pnls) - 1):
        if pnls[i] == 0:
            breakevens.append(round(prices[i], 2))
        elif (pnls[i] < 0) != (pnls[i + 1] < 0):
            frac = -pnls[i] / (pnls[i + 1] - pnls[i])
            breakevens.append(round(prices[i] + frac * step, 2))

    years_near = dte / 365
    pop: float | None = None
    if len(breakevens) >= 2:
        p_below = _prob_below(underlying_price, breakevens[0], years_near, risk_free_rate, near.implied_volatility)
        p_above = _prob_above(underlying_price, breakevens[-1], years_near, risk_free_rate, near.implied_volatility)
        pop = max(0.0, 1 - p_below - p_above)
    elif len(breakevens) == 1:
        profitable_at_center = pnl_at(near.strike) >= 0
        pop = (
            _prob_above(underlying_price, breakevens[0], years_near, risk_free_rate, near.implied_volatility)
            if profitable_at_center
            else _prob_below(underlying_price, breakevens[0], years_near, risk_free_rate, near.implied_volatility)
        )

    return PayoffResult(
        legs=[_leg_dict(short_leg), _leg_dict(long_leg)],
        net_premium=round(net_premium, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakevens=breakevens,
        probability_of_profit=round(pop, 4) if pop is not None else None,
        dte=dte,
        underlying_price=underlying_price,
        is_estimate=True,
    )


def compute_payoff(build: CandidateBuild, underlying_price: float, as_of: date, risk_free_rate: float) -> PayoffResult:
    """Calcula precio de cada pata, crédito/débito neto, beneficio máximo, pérdida máxima,
    breakeven(s) y probabilidad de beneficio para un candidato ya armado por `candidates.py`.
    Determinístico: nunca lo decide el LLM (Sección 6.1/6.2 del plan de Fase 1)."""
    dte = (build.expiration_date - as_of).days
    strategy = build.strategy_type

    if strategy in (c.CASH_SECURED_PUT, c.SHORT_PUT_NAKED):
        return _payoff_single_short_put(build.legs, underlying_price, dte, risk_free_rate)
    if strategy == c.COVERED_CALL:
        return _payoff_covered_call(build.legs, underlying_price, dte, risk_free_rate)
    if strategy == c.BULL_PUT_SPREAD:
        return _payoff_vertical_credit_spread(build.legs, underlying_price, dte, risk_free_rate)
    if strategy == c.IRON_CONDOR:
        return _payoff_iron_condor(build.legs, underlying_price, dte, risk_free_rate)
    if strategy in (c.CALENDAR_PUT_SPREAD, c.DIAGONAL_PUT_SPREAD):
        return _payoff_calendar(build.legs, underlying_price, as_of, risk_free_rate)
    raise ValueError(f"Estrategia desconocida para el cálculo de payoff: {strategy}")
