"""Tests del envío REAL + caminar el precio (execution/real_sender). Broker falso, sin red ni esperas."""

from __future__ import annotations

from datetime import date

import pytest

from options_advisor.execution import real_sender
from options_advisor.execution.live_executor import Opportunity
from options_advisor.execution.live_guard import ACTION_OPEN


def _opp():
    return Opportunity(
        symbol="AAL", action=ACTION_OPEN, expiration=date(2026, 9, 18), strike=11.0,
        requested_contracts=1, bid=0.68, ask=0.74, underlying_price=12.0,
    )


class FakeBroker:
    """Simula Schwab: cada get_order devuelve el próximo estado programado. Registra las llamadas."""

    def __init__(self, statuses, *, fill_price=None, place_raises=False, replace_raises=False,
                 partial_qty=0):
        self._statuses = list(statuses)   # secuencia de estados que devuelve get_order
        self._fill_price = fill_price
        self.partial_qty = partial_qty    # contratos ya llenados mientras la orden sigue viva
        self.place_raises = place_raises
        self.replace_raises = replace_raises
        self.placed = []
        self.replaced = []
        self.canceled = []
        self._next_id = 1

    def resolve_account_hash(self, account_number=None):
        return "HASH123"

    def place_order(self, account_hash, payload):
        if self.place_raises:
            raise RuntimeError("boom place")
        self.placed.append((account_hash, payload))
        oid = f"O{self._next_id}"
        self._next_id += 1
        return oid

    def replace_order(self, account_hash, order_id, payload):
        if self.replace_raises:
            raise RuntimeError("boom replace")
        self.replaced.append((order_id, payload))
        oid = f"O{self._next_id}"
        self._next_id += 1
        return oid

    def cancel_order(self, account_hash, order_id):
        self.canceled.append(order_id)

    def get_order(self, account_hash, order_id):
        status = self._statuses.pop(0) if self._statuses else "WORKING"
        # `filledQuantity` es lo REALMENTE llenado hasta ahora: 0 mientras la orden sigue viva sin
        # llenar. El stub devolvia 1 siempre, incluso en WORKING, lo que es imposible en Schwab y
        # tapaba el bug de llenados parciales que se arreglo el 2026-08-22. `partial_qty` permite
        # simular un llenado parcial de verdad.
        _llenado = self.partial_qty if status not in ("FILLED",) else (self.partial_qty or 1)
        info = {"status": status, "filledQuantity": _llenado}
        if status == "FILLED":
            info["orderActivityCollection"] = [
                {"executionLegs": [{"quantity": 1, "price": self._fill_price or 0.72}]}
            ]
        return info


def _run(broker, ladder, **kw):
    return real_sender.execute_live_walk(
        broker, "HASH123", _opp(), 1, ladder,
        interval_seconds=10, poll_seconds=5, sleep=lambda s: None,
        clock=iter(range(0, 100000, 5)).__next__, **kw,
    )


def test_fills_on_first_rung():
    broker = FakeBroker(["WORKING", "FILLED"], fill_price=0.72)
    res = _run(broker, [0.72, 0.71])
    assert res.ok and res.filled
    assert res.fill_price == 0.72
    assert res.filled_contracts == 1
    assert res.replacements == 0
    assert broker.replaced == []
    assert broker.canceled == []


def test_walks_then_fills():
    # No llena en el primer peldaño (dos WORKING), reemplaza, después llena.
    broker = FakeBroker(["WORKING", "WORKING", "WORKING", "FILLED"], fill_price=0.71)
    res = _run(broker, [0.72, 0.71, 0.70])
    assert res.filled
    assert res.replacements == 1
    assert len(broker.replaced) == 1
    assert res.final_limit_price == 0.71


def test_never_fills_gets_canceled():
    broker = FakeBroker(["WORKING"] * 30)
    res = _run(broker, [0.72, 0.71], leave_resting_at_mid=False)
    assert res.ok and not res.filled
    assert res.status == "CANCELED"
    assert broker.canceled  # se canceló la orden colgada


def test_never_fills_rests_at_mid_by_default():
    """Por default (usuario 2026-08-10) la orden que no llenó queda PUESTA al mid, NO se cancela."""
    broker = FakeBroker(["WORKING"] * 30)
    res = _run(broker, [0.72, 0.71])  # leave_resting_at_mid=True por default
    assert res.ok and not res.filled
    assert res.status == "WORKING"        # sigue viva
    assert broker.canceled == []          # NO se canceló
    assert res.order_id is not None       # queda una orden id esperando fill


def test_place_failure_is_reported():
    broker = FakeBroker([], place_raises=True)
    res = _run(broker, [0.72])
    assert not res.ok and not res.filled
    assert "COLOCAR" in (res.error or "")
    assert broker.placed == []


def test_rejected_order_stops_immediately():
    broker = FakeBroker(["REJECTED"])
    res = _run(broker, [0.72, 0.71, 0.70])
    assert res.ok and not res.filled
    assert res.status == "REJECTED"
    assert broker.replaced == []      # no se camina una orden rechazada
    assert broker.canceled == []      # nada que cancelar


def test_never_sends_more_than_requested_contracts():
    broker = FakeBroker(["FILLED"], fill_price=0.72)
    _run(broker, [0.72])
    _hash, payload = broker.placed[0]
    assert payload["orderLegCollection"][0]["quantity"] == 1


def test_empty_ladder_is_a_noop():
    broker = FakeBroker([])
    res = _run(broker, [])
    assert not res.ok
    assert broker.placed == []


def test_extract_fill_price_weighted():
    info = {"orderActivityCollection": [
        {"executionLegs": [{"quantity": 1, "price": 0.70}, {"quantity": 1, "price": 0.74}]},
    ]}
    assert real_sender.extract_fill_price(info) == pytest.approx(0.72)


def test_extract_fill_price_none_when_no_executions():
    assert real_sender.extract_fill_price({"orderActivityCollection": []}) is None


def test_un_llenado_parcial_no_hace_que_se_manden_contratos_de_mas():
    """Regresion (auditoria 2026-08-22): el reemplazo pedia SIEMPRE la cantidad completa.

    Schwab no tiene estado 'PARTIALLY_FILLED': una orden de 4 con 2 llenados sigue diciendo
    WORKING con filledQuantity=2. Y `replace_order` cancela el remanente y crea una orden NUEVA
    por la cantidad que se le pase. Con 4 puts de AAL aprobados: el peldaño 1 llenaba 2, el
    reemplazo volvia a pedir 4 y llenaba -> 6 puts cortos. Un 50% mas de lo que aprobo el
    guardian, y colateral que ningun tope conto.

    El assert de live_guard protege la DECISION; esto protege la EJECUCION."""
    broker = FakeBroker(["WORKING", "WORKING", "WORKING", "FILLED"], fill_price=0.71, partial_qty=2)
    res = real_sender.execute_live_walk(
        broker, "HASH123", _opp(), 4, [0.72, 0.71, 0.70],
        interval_seconds=10, poll_seconds=5, sleep=lambda s: None,
        clock=iter(range(0, 100000, 5)).__next__,
    )
    assert broker.replaced, "tiene que haber caminado un peldaño"
    _, payload = broker.replaced[0]
    pedidos = payload["orderLegCollection"][0]["quantity"]
    assert pedidos == 2, (
        f"el reemplazo pidio {pedidos} contratos con 2 ya llenados de 4: "
        "eso deja 6 en la cuenta, mas de lo aprobado"
    )
    assert res.filled_contracts <= 4, "nunca mas contratos de los aprobados"


def test_una_orden_que_muere_con_llenado_parcial_registra_lo_llenado():
    """Regresion: una orden EXPIRED/CANCELED con parte llenada registraba filled_contracts=0.

    `get_open_real_put_positions` exige order_status='FILLED', asi que esos contratos REALES
    quedaban invisibles para el robot: sin objetivo de ganancia, sin stop, y sin contar para
    ningun tope. Es el incidente del condor del 14/08 pero en el camino de los naked puts."""
    broker = FakeBroker(["WORKING", "EXPIRED"], partial_qty=2)
    res = real_sender.execute_live_walk(
        broker, "HASH123", _opp(), 4, [0.72, 0.71],
        interval_seconds=10, poll_seconds=5, sleep=lambda s: None,
        clock=iter(range(0, 100000, 5)).__next__,
    )
    assert res.filled_contracts == 2, "los 2 contratos que si se llenaron tienen que quedar anotados"
