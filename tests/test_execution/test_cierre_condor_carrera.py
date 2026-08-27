"""La orden de cierre que LLENA justo mientras sale el cancel.

Historia real (2026-08-24, dinero de verdad). El motor quiso cerrar un condor real en ganancia. La
orden no llenó dentro de la ventana del walk, asi que el sender la CANCELO... pero en Schwab ya
habia llenado, a $1.25. El walk devolvio CANCELED, el motor dejo la posicion como abierta, y durante
40 MINUTOS mando recompras que Schwab rechazo una tras otra:

    "REJECTED: This order may result in an oversold/overbought position in your account."

Estaba pidiendo recomprar algo que ya no tenia. El usuario lo vio en su broker antes que el robot, y
no recibio ningun aviso porque el email solo salia cuando el cierre entraba.

Estos tests fijan las tres reglas que salieron de ahi:
  1. Despues de cancelar hay que volver a PREGUNTAR: si llenó, gana el fill.
  2. El motivo del rechazo se guarda (antes se tiraba y el log solo decia "REJECTED").
  3. Si la recompra falla varias veces seguidas, se avisa por mail.
"""

from __future__ import annotations

from options_advisor.execution import real_condor_sender as rcs

SP = "SPXW  260824P07620000"
LP = "SPXW  260824P07610000"
SC = "SPXW  260824C07680000"
LC = "SPXW  260824C07690000"
LEGS = rcs.CondorLegs(short_put_symbol=SP, long_put_symbol=LP, short_call_symbol=SC, long_call_symbol=LC)


class Broker:
    """Broker falso que va devolviendo estados de una lista, y opcionalmente un motivo."""

    def __init__(self, estados, detalle=None):
        self._estados = list(estados)
        self._detalle = detalle
        self.cancelados = []
        self.sondeos = 0

    def place_order(self, account_hash, payload):
        return "ORD-1"

    def replace_order(self, account_hash, order_id, payload):
        return "ORD-2"

    def cancel_order(self, account_hash, order_id):
        self.cancelados.append(order_id)

    def get_order(self, account_hash, order_id):
        self.sondeos += 1
        estado = self._estados.pop(0) if self._estados else "WORKING"
        info = {"status": estado, "filledQuantity": 1}
        if self._detalle and estado in ("REJECTED", "CANCELED"):
            info["statusDescription"] = self._detalle
        return info


def _cerrar(broker, ladder=(1.20,)):
    return rcs.execute_condor_walk(
        broker, "HASH", rcs.SIDE_CLOSE, LEGS, 1, list(ladder),
        interval_seconds=10, poll_seconds=5, sleep=lambda s: None,
        clock=iter(range(0, 100000, 5)).__next__, leave_resting_at_mid=False,
    )


def test_si_lleno_mientras_salia_el_cancel_gana_el_FILL():
    """EL test del 24/08. Los tres primeros sondeos dicen WORKING, se cancela, y el sondeo de
    despues revela que en realidad habia llenado. El resultado tiene que ser un fill."""
    broker = Broker(["WORKING", "WORKING", "WORKING", "FILLED"])
    res = _cerrar(broker)

    assert res.filled is True, (
        "Devolvio 'no llenó' con la orden llenada: es el bug que dejo al robot 40 minutos "
        "mandando recompras rechazadas sobre una posicion que ya no existia"
    )
    assert res.status == "FILLED"
    assert res.fill_price == 1.20
    assert broker.cancelados == ["ORD-1"], "Tiene que haber intentado cancelar igual"


def test_si_de_verdad_no_lleno_queda_cancelado():
    """El caso normal no cambia: si sigue vivo despues del cancel, es un cancel."""
    broker = Broker(["WORKING", "WORKING", "WORKING", "CANCELED"])
    res = _cerrar(broker)
    assert res.filled is False
    assert res.status == "CANCELED"


def test_se_guarda_el_motivo_del_rechazo():
    """Antes el log solo decia 'REJECTED' y el texto de Schwab se perdia."""
    motivo = ("REJECTED: This order may result in an oversold/overbought position in your account. "
              "Please check your position quantity and/or open orders.")
    broker = Broker(["WORKING", "REJECTED"], detalle=motivo)
    res = _cerrar(broker)

    assert res.filled is False
    assert "oversold/overbought" in res.status_detalle, "Se perdio el motivo que dio el broker"


def test_el_sondeo_de_despues_del_cancel_no_rompe_si_el_broker_falla():
    """Si el broker no contesta ese ultimo sondeo, no puede reventar: seguimos con el cancel."""

    class BrokerQueFalla(Broker):
        def get_order(self, account_hash, order_id):
            self.sondeos += 1
            if self.sondeos >= 4:
                raise RuntimeError("sin red")
            return {"status": "WORKING", "filledQuantity": 0}

    res = _cerrar(BrokerQueFalla([]))
    assert res.filled is False
    assert res.status == "CANCELED"


# ─────────────────────── el aviso por mail ───────────────────────

import pytest  # noqa: E402

from options_advisor.execution import live_condor_engine as lce  # noqa: E402


@pytest.fixture
def conn():
    from options_advisor.storage import db
    return db.connect(":memory:")


class _Res:
    status = "REJECTED"
    status_detalle = "REJECTED: This order may result in an oversold/overbought position"


FILA = {"id": 7, "underlying": "$SPX", "short_put_strike": 7620.0, "short_call_strike": 7680.0}


@pytest.fixture
def mails(monkeypatch):
    enviados = []
    from options_advisor.alerts import notifier
    monkeypatch.setattr(notifier, "send_email_robot_real",
                        lambda asunto, cuerpo: enviados.append((asunto, cuerpo)) or True)
    return enviados


def test_avisa_por_mail_recien_al_tercer_rechazo(conn, mails):
    """Uno o dos rechazos pueden ser el mercado. Tres seguidos es un problema."""
    lce._avisar_si_el_cierre_no_entra(conn, FILA, _Res(), -20.0)
    assert mails == [], "Aviso al primer intento"
    lce._avisar_si_el_cierre_no_entra(conn, FILA, _Res(), -20.0)
    assert mails == [], "Aviso al segundo intento"

    lce._avisar_si_el_cierre_no_entra(conn, FILA, _Res(), -20.0)
    assert len(mails) == 1, "No aviso al tercero"
    asunto, cuerpo = mails[0]
    assert "NO puede cerrar" in asunto
    assert "oversold/overbought" in cuerpo, "El mail tiene que decir POR QUE lo rechazan"
    assert "REVISÁ TU CUENTA EN SCHWAB" in cuerpo


def test_no_manda_un_mail_por_cada_intento(conn, mails):
    """El 24/08 fueron 8 intentos en 40 minutos: eso serian 6 mails."""
    for _ in range(8):
        lce._avisar_si_el_cierre_no_entra(conn, FILA, _Res(), -20.0)
    assert len(mails) == 1, f"Mando {len(mails)} mails; tiene que ser 1"


def test_cuando_el_cierre_entra_se_rearma_el_aviso(conn, mails):
    """Si despues cierra bien, el contador vuelve a cero y un problema nuevo vuelve a avisar."""
    for _ in range(3):
        lce._avisar_si_el_cierre_no_entra(conn, FILA, _Res(), -20.0)
    assert len(mails) == 1

    lce._reset_cierres_fallidos(conn, FILA["id"])

    for _ in range(3):
        lce._avisar_si_el_cierre_no_entra(conn, FILA, _Res(), -20.0)
    assert len(mails) == 2, "Despues de un cierre exitoso, un problema nuevo tiene que volver a avisar"


def test_cada_posicion_cuenta_por_separado(conn, mails):
    otra = dict(FILA, id=9)
    for _ in range(2):
        lce._avisar_si_el_cierre_no_entra(conn, FILA, _Res(), -20.0)
    for _ in range(2):
        lce._avisar_si_el_cierre_no_entra(conn, otra, _Res(), -20.0)
    assert mails == [], "Se mezclaron los contadores de dos posiciones distintas"
