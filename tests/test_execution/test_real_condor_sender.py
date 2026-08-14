"""Tests del envío REAL de la orden COMBINADA de Iron Condor + caminar el precio neto
(execution/real_condor_sender). Broker falso, sin red ni esperas. Verifica que se manda el payload
combinado correcto (NET_CREDIT al abrir / NET_DEBIT al cerrar, con los símbolos OCC exactos), que camina
y que deja resting (apertura) o cancela (cierre)."""

from __future__ import annotations

from options_advisor.execution import real_condor_sender as rcs

SP = "SPXW  260813P07710000"
LP = "SPXW  260813P07700000"
SC = "SPXW  260813C07790000"
LC = "SPXW  260813C07800000"

LEGS = rcs.CondorLegs(short_put_symbol=SP, long_put_symbol=LP, short_call_symbol=SC, long_call_symbol=LC)


class FakeBroker:
    def __init__(self, statuses, *, place_raises=False):
        self._statuses = list(statuses)
        self.place_raises = place_raises
        self.placed = []
        self.replaced = []
        self.canceled = []
        self._next_id = 1

    def place_order(self, account_hash, payload):
        if self.place_raises:
            raise RuntimeError("boom place")
        self.placed.append(payload)
        oid = f"O{self._next_id}"; self._next_id += 1
        return oid

    def replace_order(self, account_hash, order_id, payload):
        self.replaced.append(payload)
        oid = f"O{self._next_id}"; self._next_id += 1
        return oid

    def cancel_order(self, account_hash, order_id):
        self.canceled.append(order_id)

    def get_order(self, account_hash, order_id):
        status = self._statuses.pop(0) if self._statuses else "WORKING"
        return {"status": status, "filledQuantity": 1}


def _run(broker, side, ladder, **kw):
    return rcs.execute_condor_walk(
        broker, "HASH", side, LEGS, 1, ladder,
        interval_seconds=10, poll_seconds=5, sleep=lambda s: None,
        clock=iter(range(0, 100000, 5)).__next__, **kw,
    )


def test_open_places_net_credit_combo_with_exact_symbols():
    broker = FakeBroker(["WORKING", "FILLED"])
    res = _run(broker, rcs.SIDE_OPEN, [1.90, 1.85])
    assert res.ok and res.filled
    payload = broker.placed[0]
    assert payload["orderType"] == "NET_CREDIT"
    assert payload["complexOrderStrategyType"] == "IRON_CONDOR"
    assert payload["price"] == "1.90"
    syms = [l["instrument"]["symbol"] for l in payload["orderLegCollection"]]
    assert syms == [SP, LP, SC, LC]     # composición y símbolos EXACTOS
    # el fill se registra al límite en el que quedó (conservador para un crédito: recibís ≥ eso)
    assert res.fill_price == 1.90


def test_close_places_net_debit_combo():
    broker = FakeBroker(["FILLED"])
    res = _run(broker, rcs.SIDE_CLOSE, [0.80], leave_resting_at_mid=False)
    assert res.filled
    payload = broker.placed[0]
    assert payload["orderType"] == "NET_DEBIT"
    instrs = [(l["instruction"], l["instrument"]["symbol"]) for l in payload["orderLegCollection"]]
    assert instrs == [("BUY_TO_CLOSE", SP), ("SELL_TO_CLOSE", LP),
                      ("BUY_TO_CLOSE", SC), ("SELL_TO_CLOSE", LC)]


def test_open_walks_then_fills():
    broker = FakeBroker(["WORKING", "WORKING", "WORKING", "FILLED"])
    res = _run(broker, rcs.SIDE_OPEN, [1.95, 1.90, 1.85])
    assert res.filled
    assert res.replacements == 1
    assert res.final_limit_price == 1.90
    assert broker.replaced[0]["price"] == "1.90"


def test_open_rests_at_mid_when_unfilled():
    broker = FakeBroker(["WORKING"] * 30)
    res = _run(broker, rcs.SIDE_OPEN, [1.90, 1.85])   # leave_resting default True
    assert res.ok and not res.filled
    assert res.status == "WORKING"
    assert broker.canceled == []
    assert res.order_id is not None


def test_close_cancels_when_unfilled():
    broker = FakeBroker(["WORKING"] * 30)
    res = _run(broker, rcs.SIDE_CLOSE, [0.80, 0.85], leave_resting_at_mid=False)
    assert res.ok and not res.filled
    assert res.status == "CANCELED"
    assert broker.canceled


def test_place_failure_reported():
    broker = FakeBroker([], place_raises=True)
    res = _run(broker, rcs.SIDE_OPEN, [1.90])
    assert not res.ok and not res.filled
    assert "COLOCAR" in (res.error or "")


def test_empty_ladder_is_noop():
    broker = FakeBroker([])
    res = _run(broker, rcs.SIDE_OPEN, [])
    assert not res.ok
    assert broker.placed == []


def test_bad_side_rejected():
    broker = FakeBroker([])
    res = _run(broker, "sideways", [1.0])
    assert not res.ok and "side" in (res.error or "")
