"""Tests del cierre MANUAL de posiciones del simulador desde el chat de Moshe (usuario 2026-08-11)."""
from __future__ import annotations

from datetime import date, datetime

from options_advisor.broker.models import Greeks, OptionChain, OptionContract, Quote
from options_advisor.config import load_settings
from options_advisor.simulator import manual_close
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 8, 11)
EXP = date(2026, 9, 18)


def _put(strike, bid, ask):
    return OptionContract(
        symbol="AAL", option_type="put", strike=strike, expiration=EXP, bid=bid, ask=ask,
        last_price=(bid + ask) / 2, implied_volatility=0.4, open_interest=100, volume=50,
        greeks=Greeks(delta=-0.2, gamma=0.01, theta=-0.4, vega=0.1, rho=0.01, source="broker"),
    )


class _FakeBroker:
    def get_option_chain(self, symbol, expiration_range_days=(0, 90)):
        return OptionChain(symbol=symbol, as_of=AS_OF, underlying_price=12.0, contracts=[_put(11.0, 0.60, 0.70)])
    def get_quote(self, symbol):
        return Quote(symbol=symbol, as_of=AS_OF, last_price=12.0, bid=11.99, ask=12.01, net_change_pct=0.0)


def test_manual_close_put_locks_gain():
    """Cerrar un put del simulador que subió de valor a favor: vendido a 1.50, recompra ~0.65 → gana."""
    conn = db.connect(":memory:")
    repo.init_simulated_account(conn, 100_000.0, datetime(2026, 8, 1))
    pid = repo.insert_simulated_position(conn, "AAL", "cash_secured_put", 11.0, EXP, 1, AS_OF, 1.50, 1100.0)
    settings = load_settings()
    ok, pnl, note = manual_close.close_simulator_position(conn, _FakeBroker(), settings, AS_OF, "put", position_id=pid)
    assert ok is True
    assert pnl is not None and pnl > 0            # recompró más barato de lo que vendió
    assert repo.get_open_simulated_positions(conn) == []   # ya no está abierta


def test_manual_close_put_not_found():
    conn = db.connect(":memory:")
    repo.init_simulated_account(conn, 100_000.0, datetime(2026, 8, 1))
    ok, pnl, note = manual_close.close_simulator_position(conn, _FakeBroker(), load_settings(), AS_OF, "put",
                                                          symbol="ZZZZ", strike=1.0)
    assert ok is False and "no encontré" in note


def test_manual_close_all_empty():
    conn = db.connect(":memory:")
    repo.init_simulated_account(conn, 100_000.0, datetime(2026, 8, 1))
    assert manual_close.close_all_simulator(conn, _FakeBroker(), load_settings(), AS_OF) == []
