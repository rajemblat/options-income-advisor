from __future__ import annotations

from datetime import date, timedelta

from options_advisor.broker.models import Greeks, OptionChain, OptionContract
from options_advisor.dashboard.simulator_table import (
    build_closed_position_rows,
    build_equity_curve_rows,
    build_open_position_rows,
)
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 3, 2)


def _conn():
    return db.connect(":memory:")


def _put(strike: float, expiration: date, mid: float) -> OptionContract:
    half_spread = 0.05
    return OptionContract(
        symbol="TST", option_type="put", strike=strike, expiration=expiration,
        bid=round(mid - half_spread, 2), ask=round(mid + half_spread, 2), last_price=mid,
        implied_volatility=0.30, open_interest=500, volume=50,
        greeks=Greeks(delta=-0.15, gamma=0.01, theta=-0.02, vega=0.05, rho=0.01, source="calculated"),
    )


def test_build_open_position_rows_uses_live_price_when_available():
    conn = _conn()
    expiration = AS_OF + timedelta(days=35)
    repo.insert_simulated_position(
        conn, symbol="TST", strategy_type="cash_secured_put", strike=80.0, expiration_date=expiration,
        quantity=2, entry_date=AS_OF, entry_premium=2.00, collateral=16_000.0,
    )
    rows = repo.get_open_simulated_positions(conn, "TST")

    chain = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=90.0, contracts=[_put(80.0, expiration, 1.20)])
    live_data = {"TST": (90.0, chain)}

    table = build_open_position_rows(rows, live_data)
    assert len(table) == 1
    assert table[0]["Valor actual"] == 1.20
    assert table[0]["P&L no realizado"] == (2.00 - 1.20) * 100 * 2
    assert table[0]["% s/prima"] == 40.0


def test_build_open_position_rows_falls_back_to_last_marked_pnl_without_live_price():
    conn = _conn()
    expiration = AS_OF + timedelta(days=35)
    position_id = repo.insert_simulated_position(
        conn, symbol="TST", strategy_type="cash_secured_put", strike=80.0, expiration_date=expiration,
        quantity=1, entry_date=AS_OF, entry_premium=1.50, collateral=8_000.0,
    )
    repo.mark_simulated_position(conn, position_id, AS_OF + timedelta(days=1), 42.0)
    rows = repo.get_open_simulated_positions(conn, "TST")

    table = build_open_position_rows(rows, live_data={})
    assert table[0]["Valor actual"] is None
    assert table[0]["P&L no realizado"] == 42.0


def test_build_closed_position_rows_labels_close_reason():
    conn = _conn()
    expiration = AS_OF + timedelta(days=35)
    position_id = repo.insert_simulated_position(
        conn, symbol="TST", strategy_type="cash_secured_put", strike=80.0, expiration_date=expiration,
        quantity=1, entry_date=AS_OF, entry_premium=2.00, collateral=8_000.0,
    )
    repo.close_simulated_position(conn, position_id, AS_OF + timedelta(days=10), 1.40, "profit_target", 60.0)

    table = build_closed_position_rows(repo.get_closed_simulated_positions(conn))
    assert table[0]["Motivo"] == "Objetivo 30%"
    assert table[0]["P&L realizado"] == 60.0


def test_build_equity_curve_rows_maps_fields():
    conn = _conn()
    repo.upsert_simulated_equity_snapshot(conn, AS_OF, cash=90_000.0, collateral_committed=8_000.0, unrealized_pnl=150.0, equity=98_150.0)

    rows = build_equity_curve_rows(repo.get_simulated_equity_history(conn))
    assert rows == [{"Fecha": AS_OF.isoformat(), "Equity": 98_150.0, "Cash": 90_000.0, "P&L no realizado": 150.0}]
