"""El condor se registra al precio al que Schwab EJECUTÓ, no al límite que mandamos.

Bug real del 2026-09-03, con dinero de verdad: el robot mandó el iron condor de $SPX a $1.65 de crédito
neto y Schwab lo llenó a $1.75. El sender anotaba `fill_price = final_limit_price`, así que quedó
registrado un crédito de $165 en vez de $175. Además de subestimar la ganancia, el objetivo de ganancia
y el STOP LOSS del condor se calculan sobre el crédito de entrada: un crédito equivocado corre los dos
umbrales de la posición.

Se cubre el lector de ejecuciones (`extract_condor_net_fill_price`), el sender, y los dos caminos del
motor que también anotaban el límite: la apertura que llena en un tick posterior y la recompra que
quedaba puesta entre ticks.
"""

from __future__ import annotations

from datetime import date

import pytest

from options_advisor.execution import live_condor_engine as lce
from options_advisor.execution import real_condor_sender as rcs
from options_advisor.storage import repository as repo

SP = "SPXW  260903P07660000"
LP = "SPXW  260903P07650000"
SC = "SPXW  260903C07725000"
LC = "SPXW  260903C07735000"

LEGS = rcs.CondorLegs(short_put_symbol=SP, long_put_symbol=LP, short_call_symbol=SC, long_call_symbol=LC)


def _orden(precios, *, cantidades=(1, 1, 1, 1), instrucciones=None, status="FILLED"):
    """Respuesta de `get_order` de Schwab con las 4 patas ejecutadas a `precios` (por acción).

    `precios` va en el orden put corto, put largo, call corto, call largo. Las instrucciones por
    defecto son las de una APERTURA (vender los cortos, comprar las alas)."""
    instrucciones = instrucciones or ["SELL_TO_OPEN", "BUY_TO_OPEN", "SELL_TO_OPEN", "BUY_TO_OPEN"]
    simbolos = [SP, LP, SC, LC]
    return {
        "status": status,
        "filledQuantity": cantidades[0],
        "orderLegCollection": [
            {"legId": i + 1, "orderLegType": "OPTION", "instruction": instrucciones[i],
             "instrument": {"symbol": simbolos[i], "assetType": "OPTION"}}
            for i in range(4)
        ],
        "orderActivityCollection": [
            {"activityType": "EXECUTION", "executionLegs": [
                {"legId": i + 1, "quantity": cantidades[i], "price": precios[i]}
                for i in range(4)
            ]}
        ],
    }


# ── el lector de ejecuciones ────────────────────────────────────────────────────────────────────

def test_credito_de_apertura_sale_de_las_ejecuciones():
    # vende 12.40 + 9.85, compra 10.15 + 8.85 → crédito neto 3.25
    info = _orden([12.40, 10.15, 9.85, 8.85])
    assert rcs.extract_condor_net_fill_price(info, rcs.SIDE_OPEN) == 3.25


def test_debito_de_cierre_sale_positivo():
    # al cerrar se invierte cada pata: recompra los cortos, vende las alas
    info = _orden([5.00, 3.10, 4.00, 2.60],
                  instrucciones=["BUY_TO_CLOSE", "SELL_TO_CLOSE", "BUY_TO_CLOSE", "SELL_TO_CLOSE"])
    # paga 5.00 + 4.00, cobra 3.10 + 2.60 → débito neto 3.30 (positivo, como el límite)
    assert rcs.extract_condor_net_fill_price(info, rcs.SIDE_CLOSE) == 3.30


def test_promedia_ponderado_varias_ejecuciones_de_la_misma_pata():
    info = _orden([12.40, 10.15, 9.85, 8.85])
    # el put corto llenó en dos tandas: 1 a 12.00 y 1 a 12.80 → promedio 12.40, mismo neto
    info["orderActivityCollection"] = [
        {"executionLegs": [{"legId": 1, "quantity": 1, "price": 12.00},
                           {"legId": 2, "quantity": 2, "price": 10.15},
                           {"legId": 3, "quantity": 2, "price": 9.85},
                           {"legId": 4, "quantity": 2, "price": 8.85}]},
        {"executionLegs": [{"legId": 1, "quantity": 1, "price": 12.80}]},
    ]
    assert rcs.extract_condor_net_fill_price(info, rcs.SIDE_OPEN) == 3.25


def test_sin_ejecuciones_publicadas_devuelve_none():
    info = _orden([12.40, 10.15, 9.85, 8.85])
    info["orderActivityCollection"] = []
    assert rcs.extract_condor_net_fill_price(info, rcs.SIDE_OPEN) is None


def test_menos_de_cuatro_patas_ejecutadas_devuelve_none():
    info = _orden([12.40, 10.15, 9.85, 8.85])
    info["orderActivityCollection"][0]["executionLegs"].pop()   # falta el call largo
    assert rcs.extract_condor_net_fill_price(info, rcs.SIDE_OPEN) is None


def test_patas_con_cantidades_distintas_devuelve_none():
    # condor a medio armar: el "neto" de patas desparejas no significa nada
    info = _orden([12.40, 10.15, 9.85, 8.85], cantidades=(2, 2, 1, 2))
    assert rcs.extract_condor_net_fill_price(info, rcs.SIDE_OPEN) is None


def test_apertura_con_credito_no_positivo_devuelve_none():
    # un iron condor SIEMPRE se abre a crédito: si sale ≤ 0, se leyó mal
    info = _orden([9.00, 10.15, 8.00, 8.85])
    assert rcs.extract_condor_net_fill_price(info, rcs.SIDE_OPEN) is None


def test_side_invalido_devuelve_none():
    assert rcs.extract_condor_net_fill_price(_orden([12.40, 10.15, 9.85, 8.85]), "sideways") is None


# ── el sender ──────────────────────────────────────────────────────────────────────────────────

class FakeBroker:
    def __init__(self, respuestas):
        self._respuestas = list(respuestas)
        self.placed = []
        self._next_id = 1

    def place_order(self, account_hash, payload):
        self.placed.append(payload)
        oid = f"O{self._next_id}"; self._next_id += 1
        return oid

    def replace_order(self, account_hash, order_id, payload):
        oid = f"O{self._next_id}"; self._next_id += 1
        return oid

    def cancel_order(self, account_hash, order_id):
        pass

    def get_order(self, account_hash, order_id):
        return self._respuestas.pop(0) if self._respuestas else {"status": "WORKING"}


def _run(broker, side, ladder, **kw):
    return rcs.execute_condor_walk(
        broker, "HASH", side, LEGS, 1, ladder,
        interval_seconds=10, poll_seconds=5, sleep=lambda s: None,
        clock=iter(range(0, 100000, 5)).__next__, **kw,
    )


def test_2026_09_03_manda_165_y_llena_a_175_se_registra_175():
    """El caso real: límite $1.65, ejecución $1.75. Se registra el crédito que se cobró."""
    # vende 12.40 + 9.85, compra 12.00 + 8.50 → crédito 1.75
    broker = FakeBroker([_orden([12.40, 12.00, 9.85, 8.50])])
    res = _run(broker, rcs.SIDE_OPEN, [1.65])
    assert res.filled
    assert res.fill_price == 1.75          # NO 1.65
    assert res.fill_price_origen == "schwab"
    assert res.final_limit_price == 1.65   # el límite se sigue guardando aparte


def test_cierre_registra_el_debito_realmente_pagado():
    broker = FakeBroker([_orden([5.00, 3.10, 4.00, 2.60],
                                instrucciones=["BUY_TO_CLOSE", "SELL_TO_CLOSE",
                                               "BUY_TO_CLOSE", "SELL_TO_CLOSE"])])
    res = _run(broker, rcs.SIDE_CLOSE, [3.40], leave_resting_at_mid=False)
    assert res.filled
    assert res.fill_price == 3.30
    assert res.fill_price_origen == "schwab"


def test_sin_ejecuciones_cae_al_limite_y_lo_deja_anotado():
    """Schwab dice FILLED pero todavía no publicó los fills: se usa el límite, marcado como respaldo."""
    broker = FakeBroker([{"status": "FILLED", "filledQuantity": 1}])
    res = _run(broker, rcs.SIDE_OPEN, [1.65])
    assert res.filled
    assert res.fill_price == 1.65
    assert res.fill_price_origen == "limite"


def test_el_paso_filled_deja_la_traza_del_fill_real():
    broker = FakeBroker([_orden([12.40, 12.00, 9.85, 8.50])])
    res = _run(broker, rcs.SIDE_OPEN, [1.65])
    paso = [s for s in res.steps if s.get("event") == "filled"][0]
    assert paso["fill_price"] == 1.75
    assert paso["origen"] == "schwab"


def test_camina_y_registra_el_fill_del_ultimo_peldano():
    """Caminó hasta $1.60 y llenó a $1.70: manda la ejecución, no el peldaño."""
    broker = FakeBroker([{"status": "WORKING"}, {"status": "WORKING"}, {"status": "WORKING"},
                         _orden([12.40, 12.05, 9.85, 8.50])])
    res = _run(broker, rcs.SIDE_OPEN, [1.70, 1.60])
    assert res.filled and res.replacements == 1
    assert res.final_limit_price == 1.60
    assert res.fill_price == 1.70


# ── el motor: los otros dos caminos que también anotaban el límite ──────────────────────────────

@pytest.fixture
def conn():
    from options_advisor.storage import db
    return db.connect(":memory:")


@pytest.fixture
def mails(monkeypatch):
    enviados = []
    from options_advisor.alerts import notifier
    monkeypatch.setattr(notifier, "send_email_robot_real",
                        lambda a, c: enviados.append((a, c)) or True)
    return enviados


def _fila(conn, status="working", entry_net_credit=165.0):
    pid = repo.insert_real_condor_position(
        conn, underlying="$SPX", entry_date=date(2026, 9, 3), expiration_date=date(2026, 9, 3),
        short_put_strike=7660, short_call_strike=7725, long_put_strike=7650, long_call_strike=7735,
        short_put_symbol=SP, long_put_symbol=LP, short_call_symbol=SC, long_call_symbol=LC,
        quantity=1, entry_net_credit=entry_net_credit, max_loss=835.0, max_profit=entry_net_credit,
        lower_breakeven=7658.35, upper_breakeven=7726.65, entry_spot=7700.0,
        open_schwab_order_id="ORD-7", status=status)
    return conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (pid,)).fetchone()


class _BrokerFill:
    def __init__(self, info):
        self._info = info

    def get_order(self, account_hash, order_id):
        return self._info

    def get_recent_filled_orders(self, desde):
        return []


def test_apertura_que_llena_despues_registra_el_credito_real(conn, mails):
    """La orden queda puesta y llena en un tick posterior: el crédito sale de la ejecución.

    Este es el camino más frecuente (el condor id=7 del 03/09 llenó al instante, pero muchos quedan
    puestos). Anotaba `entry_credit_ps` = el límite guardado al mandar la orden."""
    fila = _fila(conn, entry_net_credit=165.0)            # se mandó a $1.65
    info = _orden([12.40, 12.00, 9.85, 8.50])            # Schwab ejecutó a $1.75
    lce._reconcile_working_open(conn, _BrokerFill(info), "HASH", fila)

    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["status"] == "open"
    assert r["entry_credit_ps"] == 1.75, "Anotó el límite en vez del crédito realmente cobrado"
    assert r["entry_net_credit"] == 175.0
    assert mails and "175" in mails[0][1]


def test_apertura_sin_ejecuciones_publicadas_conserva_el_limite(conn, mails):
    """Sin datos de ejecución no se inventa nada: queda el límite, como antes."""
    fila = _fila(conn, entry_net_credit=165.0)
    lce._reconcile_working_open(conn, _BrokerFill({"status": "FILLED"}), "HASH", fila)

    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["status"] == "open"
    assert r["entry_credit_ps"] == 1.65
    assert r["entry_net_credit"] == 165.0


def test_recompra_puesta_registra_el_debito_realmente_pagado(conn, mails):
    """La recompra que vive entre ticks: el P&L usa lo que se pagó, no el límite al que quedó puesta."""
    fila = _fila(conn, status="open", entry_net_credit=175.0)
    repo.set_real_condor_close_working(conn, fila["id"], "CIERRE-1", 0.90)
    fila = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    # llenó a $0.80, más barato que el límite de $0.90 que estaba puesto
    info = _orden([5.00, 3.10, 4.00, 5.10],
                  instrucciones=["BUY_TO_CLOSE", "SELL_TO_CLOSE", "BUY_TO_CLOSE", "SELL_TO_CLOSE"])
    est = lce._gestionar_cierre_puesto(conn, _BrokerFill(info), "H", fila, "CIERRE-1", 0.90,
                                       175.0, 1, None, None)

    assert est == "llenó"
    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["status"] == "closed"
    assert r["close_value"] == 80.0, "Anotó el límite puesto en vez del débito realmente pagado"
    assert r["realized_pnl"] == 95.0        # 175 cobrados - 80 pagados
