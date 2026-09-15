"""Las propuestas de roll en la base (usuario 2026-09-09 / 2026-09-14).

Las reglas del usuario, y dónde se defienden acá:
  · "débito nunca" → una propuesta con crédito ≤ 0 ni siquiera se puede guardar;
  · "el robot propone y yo apruebo" → aprobar SOLO cambia el estado, no manda nada;
  · una propuesta de ayer no se ejecuta hoy: los precios que la justificaban ya no existen;
  · una misma posición no puede juntar veinte propuestas iguales esperando aprobación.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from options_advisor.storage import db
from options_advisor.storage import repository as repo


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _proponer(conn, *, open_order_id=1, credito=0.35, ahora=None, **kw):
    datos = dict(
        open_order_id=open_order_id, symbol="AAL", strike=13.0, contracts=2,
        expiration_vieja=date(2026, 9, 18), expiration_nueva=date(2026, 10, 16),
        dte_viejo=9, dte_nuevo=37, dias_agregados=28,
        occ_viejo="AAL   260918P00013000", occ_nuevo="AAL   261016P00013000",
        costo_recompra=0.65, prima_nueva=1.00, credito_neto=credito,
        credito_por_dia=round(credito / 28, 4), spot=12.92,
        motivo="ITM a 9 días del vencimiento",
    )
    datos.update(kw)
    return repo.insert_roll_proposal(conn, now=ahora, **datos)


def test_se_guarda_y_se_lee_pendiente(conn):
    pid = _proponer(conn)
    fila = repo.get_roll_proposal(conn, pid)
    assert fila["status"] == "pendiente"
    assert fila["symbol"] == "AAL"
    assert fila["strike"] == 13.0
    assert fila["occ_nuevo"] == "AAL   261016P00013000"
    assert [f["id"] for f in repo.get_roll_proposals(conn)] == [pid]


def test_el_strike_no_cambia_entre_las_dos_patas(conn):
    """Regla 1 del usuario: "el strike siempre se mantiene". La tabla guarda UN strike, no dos —
    así el dato no puede contradecirse a sí mismo."""
    fila = repo.get_roll_proposal(conn, _proponer(conn))
    assert "13000" in fila["occ_viejo"] and "13000" in fila["occ_nuevo"]


def test_un_roll_a_debito_no_se_puede_ni_guardar(conn):
    """Regla 3: "débito nunca". Si una propuesta a débito llegara a la base, un clic distraído la
    podría mandar. Por eso se corta en el insert y no solo en la pantalla."""
    with pytest.raises(ValueError, match="débito"):
        _proponer(conn, credito=-0.10)
    with pytest.raises(ValueError, match="débito"):
        _proponer(conn, credito=0.0)
    assert repo.get_roll_proposals(conn) == []


def test_aprobar_no_manda_nada_solo_cambia_el_estado(conn):
    pid = _proponer(conn)
    repo.aprobar_roll(conn, pid, now=datetime(2026, 9, 15, 10, 30))
    fila = repo.get_roll_proposal(conn, pid)
    assert fila["status"] == "aprobada"
    assert fila["approved_at"] == "2026-09-15T10:30:00"
    assert fila["schwab_order_id"] is None      # el broker todavía no vio nada
    assert fila["resolved_at"] is None


def test_aprobar_dos_veces_no_duplica_nada(conn):
    """Streamlit re-ejecuta la página entera con cada clic. Aprobar tiene que ser idempotente."""
    pid = _proponer(conn)
    repo.aprobar_roll(conn, pid, now=datetime(2026, 9, 15, 10, 30))
    repo.aprobar_roll(conn, pid, now=datetime(2026, 9, 15, 10, 31))
    assert repo.get_roll_proposal(conn, pid)["approved_at"] == "2026-09-15T10:30:00"


def test_una_rechazada_no_se_puede_aprobar_despues(conn):
    pid = _proponer(conn)
    repo.rechazar_roll(conn, pid)
    repo.aprobar_roll(conn, pid)
    assert repo.get_roll_proposal(conn, pid)["status"] == "rechazada"


def test_no_se_amontonan_propuestas_de_la_misma_posicion(conn):
    assert repo.hay_roll_pendiente_para(conn, 1) is False
    pid = _proponer(conn, open_order_id=1)
    assert repo.hay_roll_pendiente_para(conn, 1) is True
    assert repo.hay_roll_pendiente_para(conn, 2) is False
    repo.aprobar_roll(conn, pid)
    assert repo.hay_roll_pendiente_para(conn, 1) is True, "aprobada todavía ocupa el lugar"
    repo.rechazar_roll(conn, pid)
    assert repo.hay_roll_pendiente_para(conn, 1) is False, "rechazada libera el lugar"


def test_la_propuesta_de_ayer_vence(conn):
    """Los precios que la justificaban ya no valen. Lo mismo que aprendimos con el condor que llenó
    38 minutos tarde, solo que acá la ventana es de días."""
    vieja = _proponer(conn, open_order_id=1, ahora=datetime(2026, 9, 14, 11, 0))
    hoy = _proponer(conn, open_order_id=2, ahora=datetime(2026, 9, 15, 11, 0))
    assert repo.caducar_rolls_viejos(conn, date(2026, 9, 15)) == 1
    assert repo.get_roll_proposal(conn, vieja)["status"] == "vencida"
    assert repo.get_roll_proposal(conn, hoy)["status"] == "pendiente"
    assert repo.get_roll_proposal(conn, vieja)["result_note"]


def test_caducar_tambien_alcanza_a_una_aprobada_que_nunca_salio(conn):
    """Aprobada ayer y no ejecutada: mañana ya no vale. Si no venciera, el scheduler la mandaría al
    otro día con precios de otro mundo."""
    pid = _proponer(conn, ahora=datetime(2026, 9, 14, 11, 0))
    repo.aprobar_roll(conn, pid, now=datetime(2026, 9, 14, 11, 5))
    assert repo.caducar_rolls_viejos(conn, date(2026, 9, 15)) == 1
    assert repo.get_roll_proposal(conn, pid)["status"] == "vencida"


def test_cerrar_guarda_el_resultado_real(conn):
    pid = _proponer(conn)
    repo.aprobar_roll(conn, pid)
    repo.cerrar_roll(conn, pid, status="enviada", nota="llenó completo",
                     schwab_order_id="123456789", credito_real=0.38)
    fila = repo.get_roll_proposal(conn, pid)
    assert fila["status"] == "enviada"
    assert fila["schwab_order_id"] == "123456789"
    assert fila["credito_real"] == 0.38
    assert fila["result_note"] == "llenó completo"
    assert fila["resolved_at"] is not None


def test_solo_cuentan_los_rolls_que_de_verdad_salieron(conn):
    """El tope `max_rolls` no se puede gastar con intentos: cuenta solo 'enviada'."""
    p1 = _proponer(conn, open_order_id=7)
    repo.rechazar_roll(conn, p1)
    assert repo.contar_rolls_de(conn, 7) == 0
    p2 = _proponer(conn, open_order_id=7)
    repo.cerrar_roll(conn, p2, status="error", nota="Schwab rechazó la orden")
    assert repo.contar_rolls_de(conn, 7) == 0
    p3 = _proponer(conn, open_order_id=7)
    repo.cerrar_roll(conn, p3, status="enviada", nota="ok", schwab_order_id="9")
    assert repo.contar_rolls_de(conn, 7) == 1
    assert repo.contar_rolls_de(conn, 8) == 0
