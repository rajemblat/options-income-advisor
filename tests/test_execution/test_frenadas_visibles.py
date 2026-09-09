"""Una entrada que califica y no llega a ser orden real tiene que DEJAR RASTRO.

Usuario 2026-09-09: "algo esta frenando los naked, es un dia para abrir y no abrio ninguno".

Ese dia el cerebro aprobo UNH dos veces —09:51 y 10:52— y las dos murieron dentro de
`maybe_log_live_order` sin registrar nada: UNH no estaba en la lista blanca del dinero real y el
motor salia con un `return` pelado. En el dashboard el dia se veia sin oportunidades, cuando en
realidad hubo dos.

De las cuatro puertas de ese bloque, TRES salian mudas. Y la asimetria era lo peor: cuando frena el
guardian queda una fila con su motivo y el usuario la lee; cuando frenaban estas, nada. El usuario
terminaba preguntando "por que no abrio?" sobre un sistema que sabia la respuesta y no la contaba.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from options_advisor.execution import live_engine
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 9, 9)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    repo.arm_live_today(c, AS_OF)
    return c


class _LT:
    def __init__(self, permitidos=("AAL",)):
        self.allowed_symbols = list(permitidos)
        self.enabled, self.dry_run, self.kill_switch = True, False, False
        self.max_open_real_per_symbol = 1


class _Contrato:
    strike = 300.0
    expiration = date(2026, 10, 16)
    bid, ask = 4.10, 4.30


def _frenadas(conn):
    return [f for f in repo.get_live_orders_today(conn, AS_OF) if not f["sent"]]


def test_un_simbolo_fuera_de_la_lista_blanca_queda_registrado(conn):
    """EL caso del 09/09. UNH califico y no se pudo operar; el usuario tiene que poder VERLO."""
    live_engine._registrar_frenada(
        conn, AS_OF, "UNH",
        "UNH no está en la lista blanca de dinero real (live_trading.allowed_symbols).",
        _LT(), _Contrato())
    filas = _frenadas(conn)
    assert len(filas) == 1
    assert filas[0]["symbol"] == "UNH"
    assert "lista blanca" in filas[0]["reasons"]
    assert filas[0]["sent"] == 0 and filas[0]["approved"] == 0
    assert filas[0]["final_contracts"] == 0, "no se pidio ni un contrato"


def test_guarda_el_strike_y_las_puntas_para_saber_que_se_perdio(conn):
    """No alcanza con "algo se freno": hay que poder ver QUE oportunidad se dejo pasar, para decidir
    si vale la pena cambiar la regla que la freno."""
    live_engine._registrar_frenada(conn, AS_OF, "UNH", "motivo", _LT(), _Contrato())
    f = _frenadas(conn)[0]
    assert f["strike"] == 300.0
    assert f["expiration"] == "2026-10-16"
    assert (f["bid"], f["ask"]) == (4.10, 4.30)


def test_sin_contrato_igual_registra(conn):
    """Aunque no haya contrato a mano, el motivo tiene que quedar. Media constancia es mejor que
    ninguna."""
    live_engine._registrar_frenada(conn, AS_OF, "UNH", "sin START del día", _LT(), None)
    f = _frenadas(conn)[0]
    assert f["symbol"] == "UNH" and f["strike"] is None


def test_no_repite_el_mismo_motivo_para_el_mismo_simbolo_el_mismo_dia(conn):
    """UNH califico dos veces ese dia. Una linea alcanza: repetirla no agrega informacion y ensucia
    la tabla que el usuario mira todos los dias."""
    for _ in range(5):
        live_engine._registrar_frenada(conn, AS_OF, "UNH", "fuera de la lista blanca", _LT(), _Contrato())
    assert len(_frenadas(conn)) == 1


def test_motivos_distintos_del_mismo_simbolo_se_registran_por_separado(conn):
    """Si a la manana la freno la lista blanca y a la tarde el tope por simbolo, son dos cosas
    distintas y las dos hay que poder verlas."""
    live_engine._registrar_frenada(conn, AS_OF, "AAL", "fuera de la lista blanca", _LT(), _Contrato())
    live_engine._registrar_frenada(conn, AS_OF, "AAL", "ya tenés 1 posición abierta", _LT(), _Contrato())
    assert len(_frenadas(conn)) == 2


def test_una_frenada_nunca_se_confunde_con_una_orden_enviada(conn):
    """Lo mas importante: esto deja constancia, NO manda nada. Una fila de frenada con sent=1 seria
    una orden real fantasma en el registro."""
    live_engine._registrar_frenada(conn, AS_OF, "UNH", "motivo", _LT(), _Contrato())
    f = _frenadas(conn)[0]
    assert f["sent"] == 0
    assert f["payload_json"] is None, "no hay payload: nunca se armo una orden"
    assert f["collateral"] == 0.0


def test_UNH_quedo_en_la_lista_blanca():
    """El usuario 2026-09-09: "UNH poner en la lista"."""
    from options_advisor.config import load_settings
    assert "UNH" in load_settings().live_trading.allowed_symbols
