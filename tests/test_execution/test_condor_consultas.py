"""Las consultas de ganancia del condor (usuario 2026-09-15).

"Que me consulte al 30% 35% y 40% si quiero cerrar o dejar abierto."

Al 40% cierra solo; en los escalones de abajo pregunta. Lo que se cuida acá:
  · se pregunta UNA vez por escalón, no una por minuto;
  · se mide con la ganancia al precio EJECUTABLE, la misma con la que se decide cerrar;
  · decir "dejala correr" vale para siempre en ese escalón;
  · y si la posición se cierra por otro camino, las preguntas sin contestar dejan de estorbar.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from options_advisor.config import load_settings
from options_advisor.execution import live_condor_engine as lce
from options_advisor.storage import db
from options_advisor.storage import repository as repo

HOY = date(2026, 9, 15)


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture()
def cfg():
    c = load_settings().intraday_condor
    c.consult_profit_pcts = [0.30, 0.35]
    c.profit_target_pct = 0.40
    c.early_window_minutes = 30.0
    return c


def _posicion(conn, *, credito=155.0) -> int:
    cur = conn.execute(
        "INSERT INTO real_condor_positions (underlying, entry_date, entry_ts, expiration_date, "
        "short_put_strike, short_call_strike, long_put_strike, long_call_strike, "
        "short_put_symbol, long_put_symbol, short_call_symbol, long_call_symbol, quantity, "
        "entry_net_credit, max_loss, max_profit, status) "
        "VALUES ('$SPX', '2026-09-15', '2026-09-15T10:52:00', '2026-09-15', 7540, 7610, 7530, "
        "7620, 'SP', 'LP', 'SC', 'LC', 1, ?, 845.0, ?, 'open')",
        (credito, credito),
    )
    conn.commit()
    return cur.lastrowid


def _fila(conn, pid):
    return conn.execute("SELECT * FROM real_condor_positions WHERE id = ?", (pid,)).fetchone()


# ───────────────────────── cuándo pregunta ─────────────────────────


def test_pregunta_al_cruzar_el_primer_escalon(conn, cfg):
    pid = _posicion(conn)
    lce._consultar_escalones(conn, _fila(conn, pid), cfg, 50.0, 60.0)   # 32% de 155
    pendientes = repo.consultas_condor_pendientes(conn)
    assert len(pendientes) == 1
    assert pendientes[0]["escalon"] == pytest.approx(0.30)
    assert pendientes[0]["pnl"] == pytest.approx(50.0)


def test_no_pregunta_antes_de_llegar(conn, cfg):
    pid = _posicion(conn)
    lce._consultar_escalones(conn, _fila(conn, pid), cfg, 40.0, 60.0)   # 26%
    assert repo.consultas_condor_pendientes(conn) == []


def test_pregunta_los_dos_escalones_si_pasa_los_dos_de_una(conn, cfg):
    """Un salto grande entre dos ticks no puede saltearse una pregunta."""
    pid = _posicion(conn)
    lce._consultar_escalones(conn, _fila(conn, pid), cfg, 58.0, 60.0)   # 37%
    assert {c["escalon"] for c in repo.consultas_condor_pendientes(conn)} == {0.30, 0.35}


def test_no_repregunta_cada_minuto(conn, cfg):
    """El tick corre cada minuto. Sin esto serían cuarenta carteles iguales — el mismo problema
    de volumen que los mil mails del 11/09."""
    pid = _posicion(conn)
    for _ in range(40):
        lce._consultar_escalones(conn, _fila(conn, pid), cfg, 50.0, 60.0)
    assert len(repo.consultas_condor_pendientes(conn)) == 1


def test_contestada_no_vuelve_a_preguntar(conn, cfg):
    pid = _posicion(conn)
    lce._consultar_escalones(conn, _fila(conn, pid), cfg, 50.0, 60.0)
    repo.resolver_consulta_condor(conn, repo.consultas_condor_pendientes(conn)[0]["id"], "dejar")
    lce._consultar_escalones(conn, _fila(conn, pid), cfg, 52.0, 61.0)
    assert repo.consultas_condor_pendientes(conn) == []


def test_en_la_ventana_temprana_no_consulta(conn, cfg):
    """Ahí manda `profit_target_early_pct`, que cierra solo. Preguntar por algo que se va a cerrar
    igual no le sirve a nadie."""
    pid = _posicion(conn)
    lce._consultar_escalones(conn, _fila(conn, pid), cfg, 50.0, 10.0)
    assert repo.consultas_condor_pendientes(conn) == []


def test_sin_escalones_configurados_no_pasa_nada(conn, cfg):
    cfg.consult_profit_pcts = []
    pid = _posicion(conn)
    lce._consultar_escalones(conn, _fila(conn, pid), cfg, 100.0, 60.0)
    assert repo.consultas_condor_pendientes(conn) == []


def test_una_posicion_en_perdida_no_dispara_consultas(conn, cfg):
    pid = _posicion(conn)
    lce._consultar_escalones(conn, _fila(conn, pid), cfg, -80.0, 60.0)
    assert repo.consultas_condor_pendientes(conn) == []


# ───────────────────────── qué pasa con la respuesta ─────────────────────────


def test_dejar_correr_solo_anota_no_cierra(conn, cfg):
    pid = _posicion(conn)
    lce._consultar_escalones(conn, _fila(conn, pid), cfg, 50.0, 60.0)
    cid = repo.consultas_condor_pendientes(conn)[0]["id"]
    repo.resolver_consulta_condor(conn, cid, "dejar", now=datetime(2026, 9, 15, 12, 0))
    assert _fila(conn, pid)["status"] == "open"
    assert repo.is_real_condor_manual_close_requested(conn, pid) is False


def test_una_decision_invalida_se_rechaza(conn, cfg):
    pid = _posicion(conn)
    lce._consultar_escalones(conn, _fila(conn, pid), cfg, 50.0, 60.0)
    cid = repo.consultas_condor_pendientes(conn)[0]["id"]
    with pytest.raises(ValueError):
        repo.resolver_consulta_condor(conn, cid, "quizas")


def test_al_cerrarse_la_posicion_las_preguntas_dejan_de_estorbar(conn, cfg):
    """La posición se cerró por el 40%, por el stop o por vencimiento: la pregunta ya no tiene
    sentido. Tiene estado propio para no quedar 'pendiente' para siempre en la pantalla."""
    pid = _posicion(conn)
    lce._consultar_escalones(conn, _fila(conn, pid), cfg, 58.0, 60.0)
    assert len(repo.consultas_condor_pendientes(conn)) == 2
    repo.close_real_condor_position(conn, pid, HOY, 95.0, "profit_target", 60.0,
                                    close_ts=datetime(2026, 9, 15, 12, 30))
    assert repo.consultas_condor_pendientes(conn) == []
    assert {c["status"] for c in repo.consultas_condor_de(conn, pid)} == {"superada"}


def test_el_motor_consulta_con_el_precio_ejecutable_no_con_el_mid():
    """El 15/09 el mid marcaba $82.50 y salir de verdad dejaba $60. Preguntar con el número lindo
    sería peor que no preguntar: el usuario decidiría sobre plata que no va a cobrar.

    Inspección de fuente, porque lo que hay que fijar es CON QUÉ número llama el motor."""
    from pathlib import Path
    fuente = (Path(lce.__file__)).read_text(encoding="utf-8")
    assert "_consultar_escalones(conn, row, cfg," in fuente
    assert "unrealized if unrealized_estricto is None else unrealized_estricto" in fuente


def test_se_consulta_solo_cuando_NO_corresponde_cerrar():
    """Si ya corresponde cerrar, preguntar sería absurdo: la salida automática manda. Por eso la
    llamada vive dentro del `if not do_close`."""
    from pathlib import Path
    fuente = (Path(lce.__file__)).read_text(encoding="utf-8")
    cuerpo = fuente.split("if not do_close:")[-1]
    assert cuerpo.index("_consultar_escalones") < cuerpo.index("mark_real_condor_position")
