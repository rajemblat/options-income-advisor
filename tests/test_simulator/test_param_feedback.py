from __future__ import annotations

import json
from datetime import date, datetime

from options_advisor.config import load_settings
from options_advisor.simulator import learning
from options_advisor.storage import db
from options_advisor.storage import repository as repo

TODAY = date(2026, 8, 6)
NOW = datetime(2026, 8, 6, 12, 0, 0)


def _decision(conn, symbol="AAL", ctx=None):
    return repo.insert_robot_decision(
        conn, TODAY, symbol, "open", "Entrada", json.dumps(ctx or {}), NOW, position_id=None
    )


def test_param_feedback_roundtrip_and_cleaning():
    conn = db.connect(":memory:")
    did = _decision(conn)
    # 'foo' no es un voto válido → se descarta; el resto se guarda.
    repo.set_decision_param_feedback(conn, did, {"delta": "good", "iv_rank": "bad", "theta": "normal", "x": "foo"})
    got = repo.get_decision_param_feedback(conn, did)
    assert got == {"delta": "good", "iv_rank": "bad", "theta": "normal"}


def test_param_feedback_empty_clears():
    conn = db.connect(":memory:")
    did = _decision(conn)
    repo.set_decision_param_feedback(conn, did, {"delta": "good"})
    repo.set_decision_param_feedback(conn, did, {})   # sin votos → limpia
    assert repo.get_decision_param_feedback(conn, did) == {}


def test_migration_adds_column_on_existing_db(tmp_path):
    # Base creada desde cero debe tener la columna (schema.sql) y la migración no debe fallar.
    p = tmp_path / "app.db"
    conn = db.connect(p)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(robot_decisions)")}
    assert "param_feedback_json" in cols


def test_review_param_feedback_proposes_up_on_thumbs_up():
    conn = db.connect(":memory:")
    settings = load_settings().simulator
    # 3 decisiones con 👍 en delta → propone SUBIR el peso de delta.
    for _ in range(3):
        did = _decision(conn)
        repo.set_decision_param_feedback(conn, did, {"delta": "good"})
    out = learning.review_param_feedback(conn, settings)
    props = {p["param"]: p for p in out["proposed"]}
    assert "score_weight_delta" in props
    assert props["score_weight_delta"]["to"] > props["score_weight_delta"]["from"]
    # Y quedó como propuesta pendiente para aprobar.
    assert repo.has_pending_proposal_for(conn, "score_weight_delta")


def test_review_param_feedback_proposes_down_on_thumbs_down():
    conn = db.connect(":memory:")
    settings = load_settings().simulator
    for _ in range(3):
        did = _decision(conn)
        repo.set_decision_param_feedback(conn, did, {"cobertura": "bad"})
    out = learning.review_param_feedback(conn, settings)
    props = {p["param"]: p for p in out["proposed"]}
    assert "score_weight_coverage" in props
    assert props["score_weight_coverage"]["to"] < props["score_weight_coverage"]["from"]


def test_review_param_feedback_needs_minimum_votes():
    conn = db.connect(":memory:")
    settings = load_settings().simulator
    # Solo 2 votos (< mínimo de 3) → no propone nada.
    for _ in range(2):
        did = _decision(conn)
        repo.set_decision_param_feedback(conn, did, {"delta": "good"})
    out = learning.review_param_feedback(conn, settings)
    assert out["proposed"] == []


def test_review_param_feedback_ignores_unmapped_params():
    conn = db.connect(":memory:")
    settings = load_settings().simulator
    # 'strike'/'dte'/'soporte' no mapean a ninguna dimensión → no generan propuestas.
    for _ in range(5):
        did = _decision(conn)
        repo.set_decision_param_feedback(conn, did, {"strike": "bad", "dte": "good", "soporte": "bad"})
    out = learning.review_param_feedback(conn, settings)
    assert out["proposed"] == []


def test_normal_votes_are_neutral():
    conn = db.connect(":memory:")
    settings = load_settings().simulator
    for _ in range(5):
        did = _decision(conn)
        repo.set_decision_param_feedback(conn, did, {"delta": "normal"})
    out = learning.review_param_feedback(conn, settings)
    assert out["proposed"] == []
