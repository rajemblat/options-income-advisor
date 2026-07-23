from __future__ import annotations

from datetime import date
from typing import NamedTuple

from options_advisor.broker.models import OptionChain, OptionContract, OptionType
from options_advisor.strategy import constants as c

TARGET_SHORT_DELTA = 0.25  # convención estándar de venta de prima: ~25 delta (~75-80% prob. OTM)
CALENDAR_SHORT_DELTA = 0.35  # pata corta de calendar/diagonal, más cercana al dinero
NEAR_LEG_DTE_RANGE = (20, 30)
FAR_LEG_DTE_RANGE = (45, 60)
SINGLE_LEG_DTE_RANGE = (25, 50)


class Leg(NamedTuple):
    side: str  # "sell" | "buy"
    contract: OptionContract


class CandidateBuild(NamedTuple):
    strategy_type: str
    expiration_date: date
    strikes: dict
    net_greeks: dict
    greeks_source: str
    legs: list[Leg]


def _contracts_for(chain: OptionChain, option_type: OptionType, expiration: date) -> list[OptionContract]:
    return sorted(
        (ct for ct in chain.contracts if ct.option_type == option_type and ct.expiration == expiration),
        key=lambda ct: ct.strike,
    )


def _pick_by_target_delta(contracts: list[OptionContract], target_delta: float) -> OptionContract | None:
    if not contracts:
        return None
    return min(contracts, key=lambda ct: abs(abs(ct.greeks.delta) - target_delta))


def _pick_further_otm(contracts: list[OptionContract], short: OptionContract, option_type: OptionType) -> OptionContract | None:
    idx = contracts.index(short)
    if option_type == "put":
        lower = contracts[:idx]
        return lower[-1] if lower else None
    higher = contracts[idx + 1 :]
    return higher[0] if higher else None


def _position_greeks(contract: OptionContract, is_short: bool) -> dict:
    sign = -1 if is_short else 1
    g = contract.greeks
    return {
        "delta": sign * g.delta,
        "gamma": sign * g.gamma,
        "theta": sign * g.theta,
        "vega": sign * g.vega,
        "rho": sign * g.rho,
    }


def _sum_greeks(*legs: dict) -> dict:
    keys = ("delta", "gamma", "theta", "vega", "rho")
    return {k: round(sum(leg[k] for leg in legs), 4) for k in keys}


def _greeks_source(*contracts: OptionContract) -> str:
    return "broker" if all(ct.greeks.source == "broker" for ct in contracts) else "calculated"


def _nearest_expiration_or_none(chain: OptionChain, dte_range: tuple[int, int]) -> date | None:
    try:
        return chain.nearest_expiration(*dte_range)
    except ValueError:
        return None


def _build_single_short_leg(strategy_type: str, chain: OptionChain, option_type: OptionType) -> CandidateBuild | None:
    expiration = _nearest_expiration_or_none(chain, SINGLE_LEG_DTE_RANGE)
    if expiration is None:
        return None
    contracts = _contracts_for(chain, option_type, expiration)
    short = _pick_by_target_delta(contracts, TARGET_SHORT_DELTA)
    if short is None:
        return None
    return CandidateBuild(
        strategy_type=strategy_type,
        expiration_date=expiration,
        strikes={"short_strike": short.strike},
        net_greeks=_position_greeks(short, is_short=True),
        greeks_source=_greeks_source(short),
        legs=[Leg("sell", short)],
    )


def _build_vertical_spread(strategy_type: str, chain: OptionChain, option_type: OptionType) -> CandidateBuild | None:
    expiration = _nearest_expiration_or_none(chain, SINGLE_LEG_DTE_RANGE)
    if expiration is None:
        return None
    contracts = _contracts_for(chain, option_type, expiration)
    short = _pick_by_target_delta(contracts, TARGET_SHORT_DELTA)
    if short is None:
        return None
    long = _pick_further_otm(contracts, short, option_type)
    if long is None:
        return None
    return CandidateBuild(
        strategy_type=strategy_type,
        expiration_date=expiration,
        strikes={"short_strike": short.strike, "long_strike": long.strike},
        net_greeks=_sum_greeks(_position_greeks(short, True), _position_greeks(long, False)),
        greeks_source=_greeks_source(short, long),
        legs=[Leg("sell", short), Leg("buy", long)],
    )


def _build_iron_condor(chain: OptionChain) -> CandidateBuild | None:
    put_spread = _build_vertical_spread(c.IRON_CONDOR, chain, "put")
    expiration = _nearest_expiration_or_none(chain, SINGLE_LEG_DTE_RANGE)
    if put_spread is None or expiration is None:
        return None
    call_contracts = _contracts_for(chain, "call", expiration)
    short_call = _pick_by_target_delta(call_contracts, TARGET_SHORT_DELTA)
    if short_call is None:
        return None
    long_call = _pick_further_otm(call_contracts, short_call, "call")
    if long_call is None:
        return None

    put_short_strike = put_spread.strikes["short_strike"]
    put_long_strike = put_spread.strikes["long_strike"]
    return CandidateBuild(
        strategy_type=c.IRON_CONDOR,
        expiration_date=expiration,
        strikes={
            "put_short_strike": put_short_strike,
            "put_long_strike": put_long_strike,
            "call_short_strike": short_call.strike,
            "call_long_strike": long_call.strike,
        },
        net_greeks=_sum_greeks(
            put_spread.net_greeks,
            _position_greeks(short_call, True),
            _position_greeks(long_call, False),
        ),
        greeks_source=put_spread.greeks_source if put_spread.greeks_source == "calculated" else _greeks_source(short_call, long_call),
        legs=[*put_spread.legs, Leg("sell", short_call), Leg("buy", long_call)],
    )


def _build_calendar_or_diagonal(strategy_type: str, chain: OptionChain, same_strike: bool) -> CandidateBuild | None:
    near_expiration = _nearest_expiration_or_none(chain, NEAR_LEG_DTE_RANGE)
    far_expiration = _nearest_expiration_or_none(chain, FAR_LEG_DTE_RANGE)
    if near_expiration is None or far_expiration is None or near_expiration == far_expiration:
        return None

    near_contracts = _contracts_for(chain, "put", near_expiration)
    short_near = _pick_by_target_delta(near_contracts, CALENDAR_SHORT_DELTA)
    if short_near is None:
        return None

    far_contracts = _contracts_for(chain, "put", far_expiration)
    if same_strike:
        long_far = min(far_contracts, key=lambda ct: abs(ct.strike - short_near.strike), default=None)
    else:
        long_far = _pick_by_target_delta(far_contracts, TARGET_SHORT_DELTA)
    if long_far is None:
        return None

    return CandidateBuild(
        strategy_type=strategy_type,
        expiration_date=far_expiration,
        strikes={
            "near_expiration": near_expiration.isoformat(),
            "near_strike": short_near.strike,
            "far_expiration": far_expiration.isoformat(),
            "far_strike": long_far.strike,
        },
        net_greeks=_sum_greeks(_position_greeks(short_near, True), _position_greeks(long_far, False)),
        greeks_source=_greeks_source(short_near, long_far),
        legs=[Leg("sell", short_near), Leg("buy", long_far)],
    )


def build_candidate(strategy_type: str, chain: OptionChain) -> CandidateBuild | None:
    """Construye los strikes/vencimiento concretos para una estrategia dada, a partir de la
    cadena de opciones. Devuelve None si no hay contratos suficientes en la cadena para armarla."""
    if strategy_type in (c.CASH_SECURED_PUT, c.SHORT_PUT_NAKED):
        return _build_single_short_leg(strategy_type, chain, "put")
    if strategy_type == c.COVERED_CALL:
        return _build_single_short_leg(strategy_type, chain, "call")
    if strategy_type == c.BULL_PUT_SPREAD:
        return _build_vertical_spread(strategy_type, chain, "put")
    if strategy_type == c.IRON_CONDOR:
        return _build_iron_condor(chain)
    if strategy_type == c.CALENDAR_PUT_SPREAD:
        return _build_calendar_or_diagonal(strategy_type, chain, same_strike=True)
    if strategy_type == c.DIAGONAL_PUT_SPREAD:
        return _build_calendar_or_diagonal(strategy_type, chain, same_strike=False)
    raise ValueError(f"Estrategia desconocida: {strategy_type}")
