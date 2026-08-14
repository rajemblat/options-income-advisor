from __future__ import annotations

from datetime import date

from options_advisor.broker.models import AccountPosition, Quote
from options_advisor.config import load_settings
from options_advisor.simulator import learning, rules
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 8, 6)


def _quote(sym, px):
    return Quote(symbol=sym, as_of=AS_OF, last_price=px, bid=px - 0.05, ask=px + 0.05)


class _FakeBroker:
    def __init__(self, positions, quotes):
        self._positions = positions
        self._quotes = quotes

    def get_all_positions(self):
        return self._positions

    def get_quote(self, symbol):
        return self._quotes[symbol]


def _short_put(sym, qty, strike, prem, maint):
    return AccountPosition(
        account_number="X", symbol=f"{sym}  260918P00015000", asset_type="OPTION",
        quantity=qty, average_price=prem, market_value=-prem * 100 * abs(qty), unrealized_pnl=0.0,
        underlying_symbol=sym, option_type="put", strike=strike, expiration=date(2026, 9, 18),
        maintenance_requirement=maint,
    )


def test_per_contract_cost_applies_broker_factor():
    settings = load_settings().simulator.model_copy(update={"margin_mode": "naked", "broker_margin_factor": 0.5})
    regt = rules.naked_put_margin(16.07, 15.0, 0.57)
    got = rules.per_contract_cost(16.07, 15.0, 0.57, settings)
    assert got == round(regt * 0.5, 2)


def test_per_contract_cost_factor_one_is_pure_regt():
    settings = load_settings().simulator.model_copy(update={"margin_mode": "naked", "broker_margin_factor": 1.0})
    assert rules.per_contract_cost(16.07, 15.0, 0.57, settings) == rules.naked_put_margin(16.07, 15.0, 0.57)


def test_cash_secured_ignores_factor():
    settings = load_settings().simulator.model_copy(update={"margin_mode": "cash_secured", "broker_margin_factor": 0.5})
    assert rules.per_contract_cost(16.07, 15.0, 0.57, settings) == 15.0 * 100.0


def test_calibrate_from_real_positions():
    conn = db.connect(":memory:")
    settings = load_settings().simulator
    positions = [
        _short_put("AAL", -4, 15.0, 0.57, 527.39),
        _short_put("F", -2, 11.0, 0.40, 180.0),
    ]
    quotes = {"AAL": _quote("AAL", 16.07), "F": _quote("F", 12.0)}
    broker = _FakeBroker(positions, quotes)
    out = learning.calibrate_broker_margin_factor(conn, broker, settings)
    assert out["calibrated"] is True
    assert out["n"] == 2
    # El factor guardado se usa como el vigente.
    assert learning.effective_broker_margin_factor(conn, settings) == out["factor"]
    # Es < 1 (portfolio margin < Reg-T) y dentro de los límites.
    assert 0.05 <= out["factor"] < 1.0


def test_calibrate_needs_minimum_positions():
    conn = db.connect(":memory:")
    settings = load_settings().simulator
    broker = _FakeBroker([_short_put("AAL", -4, 15.0, 0.57, 527.39)], {"AAL": _quote("AAL", 16.07)})
    out = learning.calibrate_broker_margin_factor(conn, broker, settings)
    assert out["calibrated"] is False
    # Sin calibrar → cae al factor base del settings.
    assert learning.effective_broker_margin_factor(conn, settings) == settings.broker_margin_factor


def test_calibrate_ignores_long_and_non_option_positions():
    conn = db.connect(":memory:")
    settings = load_settings().simulator
    long_put = _short_put("AAL", 4, 15.0, 0.57, 527.39)   # cantidad positiva = largo
    equity = AccountPosition(account_number="X", symbol="AAL", asset_type="EQUITY", quantity=-100,
                             average_price=16.0, market_value=-1600.0, unrealized_pnl=0.0,
                             maintenance_requirement=500.0)
    broker = _FakeBroker([long_put, equity], {"AAL": _quote("AAL", 16.07)})
    out = learning.calibrate_broker_margin_factor(conn, broker, settings)
    assert out["calibrated"] is False
    assert out["n"] == 0
