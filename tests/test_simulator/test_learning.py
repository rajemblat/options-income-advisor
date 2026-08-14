from __future__ import annotations

import json
from datetime import date, datetime

from options_advisor.config import load_settings
from options_advisor.simulator import learning
from options_advisor.storage import db
from options_advisor.storage import repository as repo


def _settings():
    return load_settings().simulator


def _add_example(conn, symbol, ctx, *, feedback=None, realized_pnl=None):
    pid = repo.insert_simulated_position(
        conn, symbol=symbol, strategy_type="cash_secured_put", strike=100.0,
        expiration_date=date(2026, 5, 1), quantity=1, entry_date=date(2026, 3, 1),
        entry_premium=2.0, collateral=1000.0,
    )
    did = repo.insert_robot_decision(
        conn, date(2026, 3, 1), symbol, "open", "x", json.dumps(ctx), datetime(2026, 3, 1, 10, 0), position_id=pid
    )
    if feedback:
        repo.set_decision_feedback(conn, did, feedback)
    if realized_pnl is not None:
        repo.close_simulated_position(conn, pid, date(2026, 3, 5), 1.0, "profit_target", realized_pnl)
    return did


def test_review_not_enough_data():
    conn = db.connect(":memory:")
    _add_example(conn, "AAA", {"chosen_coverage_pct": 0.1}, feedback="good")
    res = learning.review(conn, _settings(), min_examples=8)
    assert res["enough"] is False
    assert "suficientes datos" in res["summary"]
    # deja igual los pesos
    assert repo.get_learning_state(conn) == {}


def test_review_proposes_big_change_for_strong_signal():
    conn = db.connect(":memory:")
    # cobertura alta -> gana/👍 ; cobertura baja -> pierde/👎 (señal fuerte en 'coverage')
    for i in range(10):
        good = i % 2 == 0
        ctx = {"volatile": False, "chosen_delta": -0.2, "chosen_coverage_pct": 0.15 if good else 0.02,
               "chosen_annualized_return": 0.8, "day_change_pct": 0.0}
        _add_example(conn, f"S{i}", ctx, feedback="good" if good else "bad",
                     realized_pnl=150.0 if good else -200.0)
    res = learning.review(conn, _settings())
    assert res["enough"] is True
    params_proposed = {p["param"] for p in res["proposed"]}
    assert "score_weight_coverage" in params_proposed
    # como es cambio grande, NO se aplicó solo todavía
    assert "weight.score_weight_coverage" not in repo.get_learning_state(conn)
    assert len(repo.get_pending_learning_proposals(conn)) >= 1


def test_approving_proposal_applies_weight():
    conn = db.connect(":memory:")
    pid = repo.insert_learning_proposal(conn, "score_weight_coverage", 0.25, 0.34, "porque sí")
    assert learning.apply_approved_proposal(conn, pid) is True
    assert repo.get_learning_state(conn)["weight.score_weight_coverage"] == 0.34
    assert repo.get_pending_learning_proposals(conn) == []
    # aplicar de nuevo no hace nada (ya no está pending)
    assert learning.apply_approved_proposal(conn, pid) is False


def test_effective_simulator_uses_learned_weights():
    conn = db.connect(":memory:")
    s = _settings()
    repo.set_learning_value(conn, "weight.score_weight_coverage", 0.40)
    eff = learning.load_effective_simulator(conn, s)
    assert eff.score_weight_coverage == 0.40
    # las demás quedan en su base
    assert eff.score_weight_delta == s.score_weight_delta


def test_quality_label_combines_feedback_and_outcome():
    conn = db.connect(":memory:")
    # 👍 pero perdió: feedback pesa más (0.7*1 + 0.3*-1 = 0.4 > 0)
    _add_example(conn, "X", {"chosen_coverage_pct": 0.1}, feedback="good", realized_pnl=-100.0)
    ex = repo.get_learning_examples(conn)[0]
    q = learning._quality_label(ex)
    assert q is not None and q > 0


def _add_bf_example(conn, dist, *, feedback=None, realized_pnl=None):
    bid = repo.insert_butterfly_position(
        conn, underlying="$SPX", direction="revert_down", entry_date=date(2026, 3, 1),
        expiration_date=date(2026, 3, 1), body_strike=7600, long_put_strike=7595, long_call_strike=7605,
        entry_net_credit=300, max_loss=200, max_profit=300, lower_breakeven=7597, upper_breakeven=7603, entry_spot=7600,
    )
    ctx = {"strategy": "iron_butterfly", "position_id": bid, "distance_pct": dist}
    repo.insert_robot_decision(conn, date(2026, 3, 1), "SPX", "open", "x", json.dumps(ctx), datetime(2026, 3, 1, 10, 0))
    if feedback:
        # el feedback del iron se marca en la decisión; para el test lo seteamos por id
        did = conn.execute("SELECT id FROM robot_decisions ORDER BY id DESC LIMIT 1").fetchone()["id"]
        repo.set_decision_feedback(conn, did, feedback)
    if realized_pnl is not None:
        repo.close_butterfly_position(conn, bid, date(2026, 3, 1), 100.0, "profit_target", realized_pnl)
    return bid


def test_butterfly_learning_proposes_threshold_change():
    from options_advisor.config import load_settings as _ls
    conn = db.connect(":memory:")
    cfg = _ls().intraday_butterfly
    for i in range(10):
        good = i % 2 == 0
        _add_bf_example(conn, 0.0030 if good else 0.0016, realized_pnl=50.0 if good else -70.0)
    res = learning.review_butterfly(conn, cfg)
    assert res["enough"] is True
    assert res["proposed"] and res["proposed"][0]["param"] == "distance_threshold_pct"
    assert res["proposed"][0]["to"] > res["proposed"][0]["from"]  # exigir más distancia


def test_butterfly_effective_uses_learned_threshold():
    from options_advisor.config import load_settings as _ls
    conn = db.connect(":memory:")
    cfg = _ls().intraday_butterfly
    repo.set_learning_value(conn, "butterfly.distance_threshold_pct", 0.0025)
    eff = learning.effective_butterfly(conn, cfg)
    assert eff.distance_threshold_pct == 0.0025


def test_approving_butterfly_proposal_routes_to_butterfly_key():
    conn = db.connect(":memory:")
    pid = repo.insert_learning_proposal(conn, "distance_threshold_pct", 0.0015, 0.0020, "x")
    assert learning.apply_approved_proposal(conn, pid) is True
    assert repo.get_learning_state(conn)["butterfly.distance_threshold_pct"] == 0.0020


def test_cumulative_drift_becomes_proposal_not_auto():
    """Salvaguarda: si el peso ya se alejó mucho de la base, aunque el paso sea chico, pasa a
    propuesta (no se aplica solo)."""
    from options_advisor.config import load_settings as _ls
    conn = db.connect(":memory:")
    s = _ls().simulator
    # forzar que 'coverage' ya esté lejos de su base
    base = s.score_weight_coverage
    repo.set_learning_value(conn, "weight.score_weight_coverage", base + 0.16)
    # señal chica hacia coverage: cambio pequeño, pero ya derivó > 0.15
    for i in range(10):
        good = i % 2 == 0
        ctx = {"volatile": False, "chosen_delta": -0.2, "chosen_coverage_pct": 0.11 if good else 0.10,
               "chosen_annualized_return": 0.8, "day_change_pct": 0.0}
        _add_example(conn, f"S{i}", ctx, realized_pnl=50.0 if good else -50.0)
    res = learning.review(conn, s, max_step=0.02, auto_cap=0.05)  # paso chico → normalmente auto
    cov_proposals = [p for p in res["proposed"] if p["param"] == "score_weight_coverage"]
    cov_applied = [a for a in res["applied"] if a["param"] == "score_weight_coverage"]
    # como ya derivó > 0.15, cualquier cambio de coverage va como propuesta, no auto
    if cov_proposals or cov_applied:
        assert cov_proposals and not cov_applied


def test_review_produces_plain_language_insights():
    from options_advisor.config import load_settings as _ls
    conn = db.connect(":memory:")
    s = _ls().simulator
    for i in range(10):
        good = i % 2 == 0
        ctx = {"volatile": False, "chosen_delta": -0.18 if good else -0.30, "iv_rank": 80 if good else 55,
               "chosen_coverage_pct": 0.12 if good else 0.04, "chosen_annualized_return": 0.9,
               "day_change_pct": -2.5 if good else 3.0}
        _add_example(conn, f"S{i}", ctx, realized_pnl=100.0 if good else -100.0)
    res = learning.review(conn, s)
    assert res["insights"]
    joined = " ".join(res["insights"])
    assert "IV Rank" in joined
    assert "variación del día" in joined  # valida la regla de "las que caen"


def test_analyze_real_trade_profile_learns_style():
    """Estudia las operaciones reales para aprender el estilo del usuario: tickers favoritos,
    cobertura, delta implícita (1-POP), DTE (usuario 2026-08-05). Excluye rolls."""
    from datetime import date as _date, datetime as _dt
    from options_advisor.storage import db as _db
    from options_advisor.storage import repository as _repo
    from options_advisor.storage.models import RealTradeAlert
    from options_advisor.simulator import learning as _learning
    conn = _db.connect(":memory:")

    def mk(i, sym, strike, under, dte, pop, ann, strat="cash_secured_put"):
        return RealTradeAlert(
            account_number="x", occ_symbol=f"{sym}{strike}", symbol=sym, trade_date=_date(2026, 8, 5),
            trade_ts=_dt(2026, 8, 5, 10, 0), strategy_type=strat, option_type="put", strike=strike,
            expiration_date=_date(2026, 9, 18), quantity=1, entry_price=3.0, order_id=i,
            legs=[{"side": "sell", "option_type": "put", "strike": strike, "expiration": "2026-09-18", "premium": 3.0}],
            net_premium=300.0, max_profit=300.0, max_loss=1000.0, breakevens=[strike - 3],
            probability_of_profit=pop, dte=dte, underlying_price=under, payoff_is_estimate=False,
            annualized_return_pct=ann, narrative_text="x", narrative_source="fallback_template",
        )
    for t in [mk(1, "GOOGL", 320, 363, 44, 0.86, 8.4), mk(2, "GOOGL", 300, 360, 40, 0.80, 9.0),
              mk(3, "AAPL", 270, 300, 45, 0.82, 10.0), mk(4, "MSFT", 450, 500, 44, 0.83, 9.5),
              mk(5, "GOOGL", 310, 362, 44, 0.85, 8.0), mk(6, "AA", 60, 60, 30, 0.5, 1.0, strat="roll_closed_leg")]:
        _repo.insert_real_trade_alert(conn, t)

    p = _learning.analyze_real_trade_profile(conn)
    assert p["n"] == 5 and p["enough"] is True          # el roll NO cuenta
    assert p["top_symbols"][0] == ("GOOGL", 3)          # ticker favorito
    assert p["put_pct"] == 100.0
    assert p["implied_delta"] is not None and 0.1 < p["implied_delta"] < 0.25
    assert "GOOGL" in _learning.real_trade_profile_summary(p)
