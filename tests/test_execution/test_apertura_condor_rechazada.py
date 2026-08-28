"""Una apertura de condor real NUNCA se descarta con una sola lectura de estado.

Historia real (2026-08-28, dinero de verdad). El robot dejó la orden de apertura puesta a las
09:38:58. A las 09:39:32 leyó "REJECTED" y tiró la fila como `apertura_no_llenó`. Pero la orden
llenó igual: 09:41:06, crédito $1.95. La posición quedó VIVA dos horas —sin stop de $100, sin
objetivo de ganancia, sin nadie mirándola— hasta que el usuario la vio en su broker y la cerró a
mano. $807.50 de riesgo maximo que el robot creia que no existia.

Contexto del dia: 2845 errores de conexion entre las 09 y las 11, y el detector de operaciones
reales caido hasta las 11:03. La lectura de estado bien pudo ser basura.

La regla: antes de dar por muerta una apertura hay que preguntarle a Schwab si hay una orden
LLENADA con esas 4 patas exactas. Si aparece, se ADOPTA.
"""

from __future__ import annotations

from datetime import date

import pytest

from options_advisor.execution import live_condor_engine as lce
from options_advisor.storage import repository as repo


@pytest.fixture
def conn():
    from options_advisor.storage import db
    return db.connect(":memory:")


SP, LP = "SPXW  260828P07685000", "SPXW  260828P07675000"
SC, LC = "SPXW  260828C07785000", "SPXW  260828C07795000"


def _fila(conn, status="working"):
    """Deja en la base una apertura puesta, como la del 28/08. Usa la MISMA funcion que el motor
    para insertarla, asi el test no se rompe si cambia el esquema."""
    pid = repo.insert_real_condor_position(
        conn, underlying="$SPX", entry_date=date(2026, 8, 28), expiration_date=date(2026, 8, 28),
        short_put_strike=7685, short_call_strike=7785, long_put_strike=7675, long_call_strike=7795,
        short_put_symbol=SP, long_put_symbol=LP, short_call_symbol=SC, long_call_symbol=LC,
        quantity=1, entry_net_credit=192.5, max_loss=807.5, max_profit=192.5,
        lower_breakeven=7683.07, upper_breakeven=7786.93, entry_spot=7740.59,
        open_schwab_order_id="1007746861187", status=status,
    )
    return conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (pid,)).fetchone()


class _Pata:
    def __init__(self, occ, instruction, price):
        self.occ_symbol, self.instruction, self.price = occ, instruction, price


class _Orden:
    order_id = "1007746861999"
    legs = [_Pata(SP, "SELL_TO_OPEN", 3.11), _Pata(LP, "BUY_TO_OPEN", 2.34),
            _Pata(SC, "SELL_TO_OPEN", 2.86), _Pata(LC, "BUY_TO_OPEN", 1.68)]


class Broker:
    """Dice REJECTED al preguntar por la orden, pero tiene la orden LLENADA en el historial."""

    def __init__(self, llenada=True, detalle=""):
        self._llenada = llenada
        self._detalle = detalle

    def get_order(self, account_hash, order_id):
        return {"status": "REJECTED", "statusDescription": self._detalle}

    def get_recent_filled_orders(self, desde):
        return [_Orden()] if self._llenada else []


@pytest.fixture
def mails(monkeypatch):
    enviados = []
    from options_advisor.alerts import notifier
    monkeypatch.setattr(notifier, "send_email_robot_real",
                        lambda a, c: enviados.append((a, c)) or True)
    return enviados


def test_si_schwab_dice_que_llenO_se_adopta_en_vez_de_descartar(conn, mails):
    """EL test del 28/08. La posicion existe: hay que gestionarla, no borrarla."""
    fila = _fila(conn)
    lce._reconcile_working_open(conn, Broker(llenada=True), "HASH", fila)

    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["status"] == "open", (
        "Se descarto una posicion que en el broker estaba ABIERTA: es el bug que dejo $807.50 "
        "en riesgo dos horas sin stop ni objetivo"
    )
    assert r["close_reason"] is None
    # credito = (3.11 + 2.86) - (2.34 + 1.68) = 1.95
    assert r["entry_credit_ps"] == 1.95
    assert r["entry_net_credit"] == 195.0
    assert mails and "recuper" in mails[0][0].lower()


def test_si_de_verdad_no_llenO_se_descarta_y_AVISA(conn, mails):
    """Cuando el rechazo es real la fila se descarta igual que antes — pero ahora avisa."""
    fila = _fila(conn)
    lce._reconcile_working_open(
        conn, Broker(llenada=False, detalle="REJECTED: buying power"), "HASH", fila)

    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["status"] == "closed"
    assert r["close_reason"] == "apertura_no_llenó"
    assert r["realized_pnl"] is None, "Una apertura que no entro no puede contar en el win rate"
    assert len(mails) == 1, "El 28/08 el usuario no se entero de nada"
    assert "NO pudo abrir" in mails[0][0]
    assert "buying power" in mails[0][1], "El mail tiene que decir POR QUE lo rechazo el broker"


def test_una_apertura_que_llenO_normal_sigue_funcionando(conn, mails):
    """No romper el camino feliz: si el broker dice FILLED, se marca abierta."""
    fila = _fila(conn)

    class BrokerOk(Broker):
        def get_order(self, account_hash, order_id):
            return {"status": "FILLED"}

    lce._reconcile_working_open(conn, BrokerOk(), "HASH", fila)
    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["status"] == "open"
