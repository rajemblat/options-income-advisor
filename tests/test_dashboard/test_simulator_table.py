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
    assert table[0]["Motivo"] == "Objetivo de ganancia"
    assert table[0]["P&L realizado"] == 60.0


def test_build_equity_curve_rows_maps_fields():
    conn = _conn()
    repo.upsert_simulated_equity_snapshot(conn, AS_OF, cash=90_000.0, collateral_committed=8_000.0, unrealized_pnl=150.0, equity=98_150.0)

    rows = build_equity_curve_rows(repo.get_simulated_equity_history(conn))
    assert rows == [{"Fecha": AS_OF.isoformat(), "Equity": 98_150.0, "Cash": 90_000.0, "P&L no realizado": 150.0}]


def test_build_broker_order_rows_open_and_close_legs():
    from datetime import datetime

    from options_advisor.dashboard.simulator_table import build_broker_order_rows

    conn = _conn()
    expiration = AS_OF + timedelta(days=35)
    # Una posición abierta (solo orden de apertura)
    repo.insert_simulated_position(
        conn, symbol="AAPL", strategy_type="cash_secured_put", strike=180.0, expiration_date=expiration,
        quantity=2, entry_date=AS_OF, entry_premium=3.10, collateral=36_000.0,
        entry_ts=datetime(2026, 3, 2, 9, 40, 30),
    )
    # Una posición cerrada (apertura + cierre)
    pid = repo.insert_simulated_position(
        conn, symbol="WMT", strategy_type="cash_secured_put", strike=105.0, expiration_date=expiration,
        quantity=3, entry_date=AS_OF, entry_premium=2.16, collateral=31_500.0,
        entry_ts=datetime(2026, 3, 2, 12, 40, 31),
    )
    repo.close_simulated_position(conn, pid, AS_OF + timedelta(days=5), 1.00, "profit_target", 348.0,
                                  close_ts=datetime(2026, 3, 7, 15, 41, 55))

    orders = build_broker_order_rows(repo.get_open_simulated_positions(conn), repo.get_closed_simulated_positions(conn))
    assert len(orders) == 3  # 1 apertura abierta + (1 apertura + 1 cierre) cerrada

    opens = [o for o in orders if o["Side"] == "SELL"]
    closes = [o for o in orders if o["Side"] == "BUY"]
    assert len(opens) == 2 and len(closes) == 1
    # La apertura: SELL / TO OPEN / cantidad negativa / CREDITO / PUT / LMT
    aapl_open = next(o for o in orders if o["Symbol"] == "AAPL")
    assert aapl_open["Side"] == "SELL" and aapl_open["Pos Effect"] == "TO OPEN"
    assert aapl_open["Qty"] == -2 and aapl_open["C/D"] == "CREDITO" and aapl_open["Order Type"] == "LMT"
    assert aapl_open["Exec Time"] == "3/2/26 09:40:30"
    # El cierre: BUY / TO CLOSE / cantidad positiva / DEBITO
    wmt_close = closes[0]
    assert wmt_close["Pos Effect"] == "TO CLOSE" and wmt_close["Qty"] == 3 and wmt_close["C/D"] == "DEBITO"


def test_build_broker_order_rows_includes_live_price():
    from datetime import datetime

    from options_advisor.dashboard.simulator_table import build_broker_order_rows

    conn = _conn()
    expiration = AS_OF + timedelta(days=35)
    repo.insert_simulated_position(
        conn, symbol="AAPL", strategy_type="cash_secured_put", strike=180.0, expiration_date=expiration,
        quantity=2, entry_date=AS_OF, entry_premium=3.10, collateral=36_000.0,
        entry_ts=datetime(2026, 3, 2, 9, 40, 30),
    )
    live = {"AAPL": (191.25, 1.34)}
    orders = build_broker_order_rows(repo.get_open_simulated_positions(conn), [], live_quotes=live)
    assert orders[0]["Precio ahora"] == 191.25
    assert orders[0]["% día"] == 1.34


def test_build_broker_open_position_rows_shows_underlying_price_and_change():
    from options_advisor.dashboard.simulator_table import build_broker_open_position_rows

    conn = _conn()
    expiration = AS_OF + timedelta(days=40)
    repo.insert_simulated_position(
        conn, symbol="TST", strategy_type="cash_secured_put", strike=75.0, expiration_date=expiration,
        quantity=1, entry_date=AS_OF, entry_premium=1.80, collateral=430.0,
    )
    chain = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=91.0, contracts=[_put(75.0, expiration, 1.60)])
    live_data = {"TST": (91.0, -0.82, chain)}
    rows = build_broker_open_position_rows(repo.get_open_simulated_positions(conn), live_data, AS_OF)
    assert rows[0]["Precio ahora"] == 91.0
    assert rows[0]["% día"] == -0.82
    assert rows[0]["Mark"] == 1.60  # sigue marcando con la cadena


def test_build_decision_report_rows_explains_each_open_put():
    import json
    from datetime import datetime

    from options_advisor.dashboard.simulator_table import build_decision_report_rows

    conn = _conn()
    expiration = AS_OF + timedelta(days=45)
    repo.insert_simulated_position(
        conn, symbol="JNJ", strategy_type="cash_secured_put", strike=240.0, expiration_date=expiration,
        quantity=2, entry_date=AS_OF, entry_premium=3.60, collateral=8067.2,
    )
    ctx = {
        "volatile": False, "iv_rank": 74.5, "chosen_delta": -0.258, "delta_target": 0.2,
        "chosen_coverage_pct": 0.0554, "chosen_annualized_return": 0.7239, "chosen_dte": 45,
        "chosen_margin": 4033.6, "has_event": True,
    }
    repo.insert_robot_decision(conn, AS_OF, "JNJ", "open", "Entrada abierta", json.dumps(ctx), datetime(2026, 3, 2, 15, 0, 0))
    # ruido: una decisión de butterfly no debe aparecer en el reporte de puts
    repo.insert_robot_decision(conn, AS_OF, "SPX", "open", "iron", json.dumps({"strategy": "iron_butterfly"}), datetime(2026, 3, 2, 15, 1, 0))

    rows = build_decision_report_rows(repo.get_open_simulated_positions(conn), repo.get_robot_decisions(conn, limit=100))
    assert len(rows) == 1
    r = rows[0]
    assert r["Symbol"] == "JNJ"
    assert r["Delta"] == -0.258
    assert r["IV Rank"] == 74.5
    assert r["Cobertura"] == 5.54          # ya en %
    assert r["Anualizado"] == 72.4         # ya en %
    assert "IV Rank 74 (alta)" in r["Por qué la abrió"]
    assert "evento" in r["Por qué la abrió"].lower()
