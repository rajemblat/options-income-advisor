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

    def __init__(self, statuses, *, fill_price=None, place_raises=False, replace_raises=False):
        self._statuses = list(statuses)   # secuencia de estados que devuelve get_order
        self._fill_price = fill_price
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
        info = {"status": status, "filledQuantity": 1}
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
