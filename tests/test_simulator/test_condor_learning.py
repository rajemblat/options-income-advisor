"""Aprendizaje del Iron Condor (usuario 2026-08-14: "que el robot aprenda de las operaciones y se
haga experto, sepa qué hacer y qué no volver a hacer").

Lo que se protege acá:
  · no toca NADA hasta las 20 operaciones cerradas con señal (decisión del usuario);
  · aprende de papel Y real juntos, sin confundir un id de papel con uno real;
  · mueve cada perilla hacia donde estaban las BUENAS, con paso acotado;
  · modo mixto: cambio chico se aplica solo, grande queda como propuesta;
  · SALVAGUARDA: el stop-loss solo se auto-aplica cuando se hace más ESTRICTO — aflojarlo siempre
    pasa por aprobación del usuario, por chico que sea el paso;
  · lo aprendido lo aplican los DOS motores (papel y real) vía effective_condor.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from options_advisor.config import load_settings
from options_advisor.simulator import learning
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 8, 14)


def _cfg():
    return load_settings().intraday_condor


def _open_and_close(conn, *, book="paper", delta=0.15, day_range=0.003, credit=200.0,
                    vix_change=0.0, pnl=100.0, reason="profit_target", feedback=None):
    """Registra una apertura con sus features y la posición ya cerrada que le corresponde."""
    if book == "paper":
        pid = repo.insert_condor_position(
            conn, underlying="SPX", entry_date=AS_OF, expiration_date=AS_OF,
            short_put_strike=7500.0, short_call_strike=7700.0,
            long_put_strike=7490.0, long_call_strike=7710.0,
            entry_net_credit=credit, max_loss=800.0, max_profit=credit,
            lower_breakeven=7498.0, upper_breakeven=7702.0, entry_spot=7600.0,
            entry_ts=datetime.now())
        repo.close_condor_position(conn, pid, AS_OF, 50.0, reason, pnl, close_ts=datetime.now())
    else:
        pid = repo.insert_real_condor_position(
            conn, underlying="SPX", entry_date=AS_OF, expiration_date=AS_OF,
            short_put_strike=7500.0, short_call_strike=7700.0,
            long_put_strike=7490.0, long_call_strike=7710.0,
            short_put_symbol="SP", long_put_symbol="LP", short_call_symbol="SC", long_call_symbol="LC",
            quantity=1, entry_net_credit=credit, max_loss=800.0, max_profit=credit,
            lower_breakeven=7498.0, upper_breakeven=7702.0, entry_spot=7600.0,
            open_schwab_order_id="OID", status="working", entry_ts=datetime.now())
        repo.close_real_condor_position(conn, pid, AS_OF, 50.0, reason, pnl, close_ts=datetime.now())
    ctx = {"strategy": "iron_condor", "book": book, "position_id": pid, "spot": 7600.0,
           "day_range_pct": day_range, "net_credit": credit, "short_delta_avg": delta,
           "vix_change_pct": vix_change}
    did = repo.insert_robot_decision(conn, AS_OF, "SPX", "open", "Entrada condor abierta",
                                     json.dumps(ctx), datetime.now())
    if feedback:
        conn.execute("UPDATE robot_decisions SET user_feedback = ? WHERE id = ?", (feedback, did))
        conn.commit()
    return pid


# ------------------------------- muestra mínima -------------------------------

def test_does_not_touch_anything_below_the_sample_size():
    conn = db.connect(":memory:")
    for _ in range(19):
        _open_and_close(conn, pnl=100.0)
    out = learning.review_condor(conn, _cfg())
    assert out["enough"] is False
    assert out["examples"] == 19
    assert out["applied"] == [] and out["proposed"] == []
    assert repo.get_learning_state(conn) == {}
    assert "19 de las 20" in out["summary"]


# ------------------------------- papel + real juntos -------------------------------

def test_learns_from_paper_and_real_together_without_mixing_ids():
    """Papel y real tienen ids independientes: un id 1 de papel y un id 1 real son posiciones
    distintas. El `book` del contexto es lo que evita cruzarlas."""
    conn = db.connect(":memory:")
    _open_and_close(conn, book="paper", pnl=120.0, reason="profit_target")
    _open_and_close(conn, book="real", pnl=-100.0, reason="stop_loss")
    rows = repo.get_condor_learning_examples(conn)
    assert len(rows) == 2
    by_pnl = sorted(r["realized_pnl"] for r in rows)
    assert by_pnl == [-100.0, 120.0], "cada apertura tiene que traer el resultado de SU libro"
    assert {r["close_reason"] for r in rows} == {"profit_target", "stop_loss"}


# ------------------------------- perillas de entrada -------------------------------

def test_moves_delta_toward_what_the_winners_used():
    """Las ganadoras vendían más lejos del dinero (delta 0.10) y las perdedoras más cerca (0.25):
    el robot tiene que exigir vender MÁS lejos."""
    conn = db.connect(":memory:")
    for _ in range(14):
        _open_and_close(conn, delta=0.10, pnl=100.0, reason="profit_target")
    for _ in range(6):
        _open_and_close(conn, delta=0.25, pnl=-100.0, reason="stop_loss")
    out = learning.review_condor(conn, _cfg())
    assert out["enough"] is True
    tocados = [c["param"] for c in out["applied"] + out["proposed"]]
    assert "short_delta_max" in tocados
    cambio = [c for c in out["applied"] + out["proposed"] if c["param"] == "short_delta_max"][0]
    assert cambio["to"] < cambio["from"], "vender más lejos = delta más chico"


def test_raises_the_minimum_credit_when_the_thin_premiums_are_the_losers():
    """"Primas altas" (usuario 2026-08-14): si las malas cobraban poco, sube el crédito mínimo."""
    conn = db.connect(":memory:")
    for _ in range(14):
        _open_and_close(conn, credit=250.0, pnl=120.0, reason="profit_target")
    for _ in range(6):
        _open_and_close(conn, credit=60.0, pnl=-100.0, reason="stop_loss")
    out = learning.review_condor(conn, _cfg())
    cambios = {c["param"]: c for c in out["applied"] + out["proposed"]}
    assert "min_credit" in cambios
    assert cambios["min_credit"]["to"] > cambios["min_credit"]["from"] == 0.0


def test_learns_the_vix_ceiling_from_the_worst_winner_not_the_average():
    """El tope se pone en el PEOR movimiento de VIX con el que una operación igual salió bien,
    para no dejar afuera un escenario que históricamente funcionó."""
    conn = db.connect(":memory:")
    for i in range(14):
        _open_and_close(conn, vix_change=(1.0 if i < 13 else 3.0), pnl=100.0, reason="profit_target")
    for _ in range(6):
        _open_and_close(conn, vix_change=9.0, pnl=-100.0, reason="stop_loss")
    out = learning.review_condor(conn, _cfg())
    cambios = {c["param"]: c for c in out["applied"] + out["proposed"]}
    assert "max_vix_change_pct" in cambios
    # Arranca sin filtro (None → tope máximo 15) y baja hacia 3.0, con paso acotado.
    assert cambios["max_vix_change_pct"]["to"] < 15.0


def test_a_vix_that_collapses_teaches_the_same_as_one_that_spikes():
    """Usuario 2026-08-14: "el vix no tiene que estar bajando, ni subiendo … sino que día lateral
    estable". Un VIX que se DERRUMBA 9% tampoco es un día tranquilo (suele ser un rally fuerte, y al
    condor lo mata que el SPX se mueva, para donde sea). El aprendizaje mira el valor ABSOLUTO: con
    las perdedoras entrando a −9% tiene que bajar el tope igual que si hubieran entrado a +9%."""
    conn = db.connect(":memory:")
    for i in range(14):
        _open_and_close(conn, vix_change=(-1.0 if i % 2 else 1.0), pnl=100.0, reason="profit_target")
    for _ in range(6):
        _open_and_close(conn, vix_change=-9.0, pnl=-100.0, reason="stop_loss")
    out = learning.review_condor(conn, _cfg())
    cambios = {c["param"]: c for c in out["applied"] + out["proposed"]}
    assert "max_vix_change_pct" in cambios, "un VIX desplomándose tiene que enseñar igual que uno subiendo"
    c = cambios["max_vix_change_pct"]
    assert c["to"] < 15.0
    assert c["to"] > 0, "el tope es un movimiento máximo (±), nunca un número negativo"


# ------------------------------- salvaguarda del stop -------------------------------

def test_loosening_the_stop_is_never_automatic():
    """El stop es el límite de riesgo, no un parámetro más: aflojarlo SIEMPRE es propuesta."""
    conn = db.connect(":memory:")
    cfg = _cfg()
    aplicados, propuestos = learning._cd_move(
        conn, learning._CD_STOP_KEY, 100.0, 105.0,       # aflojar apenas $5, muy por debajo del auto_cap
        "prueba (${current:,.0f} → ${proposed:,.0f})", auto_only_if_tighter=True)
    assert aplicados == [], "aflojar el stop no puede aplicarse solo"
    assert propuestos and propuestos[0]["to"] > propuestos[0]["from"]
    assert repo.get_learning_state(conn).get(learning._CD_STOP_KEY) is None
    del cfg


def test_tightening_the_stop_can_be_automatic():
    conn = db.connect(":memory:")
    aplicados, propuestos = learning._cd_move(
        conn, learning._CD_STOP_KEY, 100.0, 95.0,
        "prueba (${current:,.0f} → ${proposed:,.0f})", auto_only_if_tighter=True)
    assert aplicados and aplicados[0]["to"] < aplicados[0]["from"]
    assert propuestos == []
    assert repo.get_learning_state(conn)[learning._CD_STOP_KEY] == 95.0


# ------------------------------- modo mixto -------------------------------

def test_big_change_becomes_a_proposal_and_does_not_apply_itself():
    conn = db.connect(":memory:")
    aplicados, propuestos = learning._cd_move(
        conn, learning._CD_DELTA_KEY, 0.15, 0.05, "prueba ({current:.3f} → {proposed:.3f})")
    assert aplicados == []
    assert propuestos and propuestos[0]["param"] == "short_delta_max"
    assert repo.get_learning_state(conn).get(learning._CD_DELTA_KEY) is None
    pendientes = repo.get_pending_learning_proposals(conn)
    assert len(pendientes) == 1


def test_approving_a_condor_proposal_writes_the_right_key():
    """Una propuesta aprobada tiene que escribir en 'condor.*', no en 'weight.*'."""
    conn = db.connect(":memory:")
    learning._cd_move(conn, learning._CD_DELTA_KEY, 0.15, 0.05, "prueba ({current:.3f} → {proposed:.3f})")
    pid = repo.get_pending_learning_proposals(conn)[0]["id"]
    assert learning.apply_approved_proposal(conn, pid) is True
    assert learning._CD_DELTA_KEY in repo.get_learning_state(conn)


# ------------------------------- aplicación en los dos motores -------------------------------

def test_effective_condor_applies_what_it_learned():
    conn = db.connect(":memory:")
    cfg = _cfg()
    repo.set_learning_value(conn, learning._CD_DELTA_KEY, 0.11)
    repo.set_learning_value(conn, learning._CD_CALM_KEY, 0.003)
    repo.set_learning_value(conn, learning._CD_CREDIT_KEY, 150.0)
    repo.set_learning_value(conn, learning._CD_VIX_KEY, 4.0)
    eff = learning.effective_condor(conn, cfg)
    assert eff.short_delta_max == 0.11
    assert eff.calm_range_pct == 0.003
    assert eff.min_credit == 150.0
    assert eff.max_vix_change_pct == 4.0
    # Lo que NO aprendió queda igual que en tu settings.
    assert eff.wing_width == cfg.wing_width
    assert eff.stop_loss_dollars == cfg.stop_loss_dollars


def test_effective_condor_is_a_noop_without_anything_learned():
    conn = db.connect(":memory:")
    cfg = _cfg()
    assert learning.effective_condor(conn, cfg) == cfg
