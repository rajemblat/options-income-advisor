from __future__ import annotations

from datetime import date

from options_advisor.broker.models import Greeks, OptionChain, OptionContract
from options_advisor.indicators.gex import compute_net_gex


def _contract(option_type: str, gamma: float, open_interest: int) -> OptionContract:
    return OptionContract(
        symbol="TST",
        option_type=option_type,
        strike=100.0,
        expiration=date(2026, 8, 1),
        bid=1.0,
        ask=1.1,
        last_price=1.05,
        implied_volatility=0.3,
        open_interest=open_interest,
        volume=10,
        greeks=Greeks(delta=0.3, gamma=gamma, theta=-0.01, vega=0.1, rho=0.01, source="broker"),
    )


def test_calls_contribute_positive_and_puts_negative():
    chain = OptionChain(
        symbol="TST",
        as_of=date(2026, 1, 1),
        underlying_price=100.0,
        contracts=[_contract("call", gamma=0.05, open_interest=1000)],
    )
    call_only = compute_net_gex(chain)
    assert call_only > 0

    chain_put = OptionChain(
        symbol="TST",
        as_of=date(2026, 1, 1),
        underlying_price=100.0,
        contracts=[_contract("put", gamma=0.05, open_interest=1000)],
    )
    put_only = compute_net_gex(chain_put)
    assert put_only < 0
    assert abs(put_only) == abs(call_only)  # misma magnitud, signo opuesto para gamma/OI idénticos


def test_matches_hand_computed_formula():
    chain = OptionChain(
        symbol="TST",
        as_of=date(2026, 1, 1),
        underlying_price=100.0,
        contracts=[_contract("call", gamma=0.05, open_interest=1000)],
    )
    expected = 0.05 * 1000 * 100 * (100.0**2) * 0.01
    assert compute_net_gex(chain) == round(expected, 2)


def test_empty_chain_returns_zero():
    chain = OptionChain(symbol="TST", as_of=date(2026, 1, 1), underlying_price=100.0, contracts=[])
    assert compute_net_gex(chain) == 0.0
