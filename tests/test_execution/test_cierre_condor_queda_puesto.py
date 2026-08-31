"""La recompra del condor real QUEDA PUESTA en el broker en vez de cancelarse cada minuto.

Usuario 2026-08-24, viendo su historial de ordenes: "envio el cierre en 1.25, despues lo sigue
enviando mas veces en 1.25 en vez de dejarlo working que lo tome cuando lo vaya a tomar. Si
entiendo que lo envie otra vez si modifica el precio de cierre, pero el mismo precio no es
necesario, lo deja en working".

Tenia razon por dos motivos. El obvio: mandar la misma orden al mismo precio una y otra vez es
ruido. El grave: ese ciclo cancelar/reponer abrio la carrera de ese mismo dia -- la orden llenO a
$1.25 justo mientras salia el cancel, el robot la dio por no llenada, y paso 40 minutos mandando
recompras que Schwab rechazaba sobre una posicion que ya no existia.

Ahora la orden vive entre ticks: se la sondea, y solo se la reemplaza si el precio objetivo se
movio de verdad.
"""

from __future__ import annotations

from datetime import date

import pytest

from options_advisor.execution import live_condor_engine as lce
from options_advisor.storage import repository as repo

SP, LP = "SPXW  260828P07685000", "SPXW  260828P07675000"
SC, LC = "SPXW  260828C07785000", "SPXW  260828C07795000"


@pytest.fixture
def conn():
    from options_advisor.storage import db
    return db.connect(":memory:")


@pytest.fixture
def fila(conn):
    pid = repo.insert_real_condor_position(
        conn, underlying="$SPX", entry_date=date(2026, 8, 28), expiration_date=date(2026, 8, 28),
        short_put_strike=7685, short_call_strike=7785, long_put_strike=7675, long_call_strike=7795,
        short_put_symbol=SP, long_put_symbol=LP, short_call_symbol=SC, long_call_symbol=LC,
        quantity=1, entry_net_credit=195.0, max_loss=805.0, max_profit=195.0,
        lower_breakeven=7683.0, upper_breakeven=7787.0, entry_spot=7740.0,
        open_schwab_order_id="OPEN-1", status="open")
    repo.set_real_condor_close_working(conn, pid, "CIERRE-1", 1.25)
    return conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (pid,)).fetchone()


class _Contrato:
    def __init__(self, otype, strike, bid, ask):
        self.option_type, self.strike, self.bid, self.ask = otype, strike, bid, ask


class _Cadena:
    """Cadena minima con las 4 patas. `desplazamiento` mueve los precios para simular el mercado."""

    def __init__(self, desplazamiento=0.0):
        d = desplazamiento
        self.contracts = [
            _Contrato("put", 7685, 0.60 + d, 0.70 + d), _Contrato("put", 7675, 0.20, 0.30),
            _Contrato("call", 7785, 0.60 + d, 0.70 + d), _Contrato("call", 7795, 0.20, 0.30),
        ]


class Broker:
    def __init__(self, estado="WORKING", detalle=""):
        self.estado, self.detalle = estado, detalle
        self.reemplazos = []
        self.cancelados = []

    def get_order(self, account_hash, order_id):
        return {"status": self.estado, "statusDescription": self.detalle}

    def replace_order(self, account_hash, order_id, payload):
        self.reemplazos.append((order_id, payload))
        return "CIERRE-2"

    def cancel_order(self, account_hash, order_id):
        self.cancelados.append(order_id)


@pytest.fixture
def mails(monkeypatch):
    enviados = []
    from options_advisor.alerts import notifier
    monkeypatch.setattr(notifier, "send_email_robot_real",
                        lambda a, c: enviados.append((a, c)) or True)
    return enviados


def _cfg():
    from options_advisor.config import IntradayCondorSettings
    return IntradayCondorSettings(stop_loss_dollars=100.0)


def test_si_la_orden_puesta_lleno_se_registra_el_cierre(conn, fila, mails):
    est = lce._gestionar_cierre_puesto(conn, Broker("FILLED"), "H", fila, "CIERRE-1", 1.25,
                                       195.0, 1, _Cadena(), _cfg())
    assert est == "llenó"
    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["status"] == "closed"
    assert r["close_value"] == 125.0
    assert r["realized_pnl"] == 70.0          # 195 cobrados - 125 de recompra
    assert r["close_working_order_id"] is None, "Quedo apuntando a una orden que ya no existe"
    assert mails and "CERRÓ" in mails[0][0]


def _precio_objetivo(cadena, fila):
    """El precio al que el motor querria tener la orden AHORA. Se calcula con las mismas funciones
    del motor en vez de escribirlo a mano: escribirlo a mano hacia que el test comprobara mi
    aritmetica en vez del comportamiento (error real al escribir estos tests)."""
    q = lce._combo_quotes(cadena, fila["short_put_strike"], fila["short_call_strike"],
                          fila["long_put_strike"], fila["long_call_strike"])
    return lce._close_debit_ladder(*q)[-1]


def test_si_sigue_viva_y_el_precio_no_se_movio_NO_la_toca(conn, fila):
    """EL pedido del usuario: no reenviar la misma orden al mismo precio."""
    cadena = _Cadena()
    puesto = _precio_objetivo(cadena, fila)      # la orden ya esta justo donde corresponde
    broker = Broker("WORKING")

    est = lce._gestionar_cierre_puesto(conn, broker, "H", fila, "CIERRE-1", puesto,
                                       195.0, 1, cadena, _cfg())
    assert est == "sigue_viva"
    assert broker.reemplazos == [], "Reemplazo la orden sin que el precio cambiara"
    assert broker.cancelados == [], "Cancelo una orden que estaba trabajando bien"
    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["close_working_order_id"] == "CIERRE-1"


def test_un_movimiento_chiquito_tampoco_la_toca(conn, fila):
    """Menos de 5 centavos es ruido del mid: reemplazar por eso solo agrega churn."""
    cadena = _Cadena()
    puesto = _precio_objetivo(cadena, fila) + 0.02
    broker = Broker("WORKING")

    lce._gestionar_cierre_puesto(conn, broker, "H", fila, "CIERRE-1", puesto,
                                 195.0, 1, cadena, _cfg())
    assert broker.reemplazos == []


def test_si_el_precio_se_movio_la_reemplaza(conn, fila):
    """"Si modifica el precio de cierre sí" -- ahi si vale reemplazar."""
    cadena = _Cadena()
    puesto = _precio_objetivo(cadena, fila)
    movida = _Cadena(desplazamiento=0.40)
    assert abs(_precio_objetivo(movida, fila) - puesto) > 0.05, "la cadena movida no mueve el precio"
    broker = Broker("WORKING")

    est = lce._gestionar_cierre_puesto(conn, broker, "H", fila, "CIERRE-1", puesto,
                                       195.0, 1, movida, _cfg())
    assert est == "sigue_viva"
    assert len(broker.reemplazos) == 1, "No reajusto el precio con el mercado movido"
    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["close_working_order_id"] == "CIERRE-2"


def test_si_la_orden_murio_se_limpia_y_se_reintenta(conn, fila):
    broker = Broker("REJECTED", detalle="REJECTED: buying power")
    est = lce._gestionar_cierre_puesto(conn, broker, "H", fila, "CIERRE-1", 1.25,
                                       195.0, 1, _Cadena(), _cfg())
    assert est == "murió"
    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["close_working_order_id"] is None
    assert r["status"] == "open", "Una recompra rechazada no cierra la posicion"


def test_si_el_broker_no_contesta_se_deja_la_orden_quieta(conn, fila):
    """Ante un error de red NO se cancela nada: una orden viva en el broker es mas segura que una
    cancelada a ciegas. Es la leccion del 24/08."""

    class Mudo(Broker):
        def get_order(self, account_hash, order_id):
            raise RuntimeError("sin red")

    broker = Mudo()
    est = lce._gestionar_cierre_puesto(conn, broker, "H", fila, "CIERRE-1", 1.25,
                                       195.0, 1, _Cadena(), _cfg())
    assert est == "sigue_viva"
    assert broker.cancelados == []
    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["close_working_order_id"] == "CIERRE-1"
