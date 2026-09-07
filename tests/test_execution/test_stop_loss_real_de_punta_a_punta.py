"""El stop del condor REAL, de punta a punta: de la pérdida a la orden que sale a Schwab.

Los tests que ya había cubrían la DECISIÓN (`should_close_condor` con la medición estricta del
2026-09-02) y las piezas sueltas del envío. Faltaba el recorrido completo sobre el motor real: una
posición viva, el mercado en contra, y la comprobación de que efectivamente SALE una orden combinada
de recompra a Schwab y la fila queda cerrada con motivo `stop_loss`.

Es la pregunta del usuario antes de dejarlo abrir mañana: "que el stop esté funcionando al 100%".
El stop del condor no es una orden puesta en el broker — lo dispara el robot mirando el precio cada
minuto, así que la única prueba que vale es esta: dado el mercado en contra, ¿sale la orden?
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from options_advisor.broker.models import Greeks, OptionChain, OptionContract
from options_advisor.config import load_settings
from options_advisor.execution import live_condor_engine as lce
from options_advisor.execution import real_condor_sender as rcs
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 9, 4)
SPOT = 7695.0

SHORT_PUT, LONG_PUT = 7500.0, 7490.0
SHORT_CALL, LONG_CALL = 7700.0, 7710.0

SP, LP = "SPXW  260904P07500000", "SPXW  260904P07490000"
SC, LC = "SPXW  260904C07700000", "SPXW  260904C07710000"

CREDITO = 175.0        # el condor de hoy, con el crédito REAL (no el límite de $165)


def _opt(otype: str, strike: float, mid: float) -> OptionContract:
    return OptionContract(
        symbol=f"SPXW  260904{'P' if otype == 'put' else 'C'}{round(strike * 1000):08d}",
        option_type=otype, strike=strike, expiration=AS_OF,
        bid=round(mid - 0.05, 2), ask=round(mid + 0.05, 2), last_price=mid,
        implied_volatility=0.12, open_interest=1000, volume=500,
        greeks=Greeks(delta=-0.10 if otype == "put" else 0.10, gamma=0.01, theta=-0.5, vega=0.1,
                      rho=0.01, source="broker"),
    )


def _cadena_en_contra() -> OptionChain:
    """SPX pegado al call corto. Al MID la posición marca −$95 (el stop de $100 no dispararía);
    salir de verdad cuesta $290, o sea −$115. Es exactamente el caso del 2026-09-02."""
    contracts = []
    for k in range(7480, 7721, 10):
        mid_put = 0.25 if k in (7500, 7490) else 0.20
        mid_call = 2.95 if k == 7700 else (0.25 if k == 7710 else 0.20)
        contracts.append(_opt("put", float(k), mid_put))
        contracts.append(_opt("call", float(k), mid_call))
    return OptionChain(symbol="SPX", as_of=AS_OF, underlying_price=SPOT, contracts=contracts)


def _cadena_tranquila() -> OptionChain:
    """Todo barato: la posición está en ganancia chica, ni objetivo ni stop la tocan."""
    contracts = []
    for k in range(7480, 7721, 10):
        contracts.append(_opt("put", float(k), 0.40))
        contracts.append(_opt("call", float(k), 0.40))
    return OptionChain(symbol="SPX", as_of=AS_OF, underlying_price=7600.0, contracts=contracts)


@pytest.fixture
def conn():
    return db.connect(":memory:")


@pytest.fixture
def fila(conn):
    pid = repo.insert_real_condor_position(
        conn, underlying="$SPX", entry_date=AS_OF, expiration_date=AS_OF,
        short_put_strike=SHORT_PUT, short_call_strike=SHORT_CALL,
        long_put_strike=LONG_PUT, long_call_strike=LONG_CALL,
        short_put_symbol=SP, long_put_symbol=LP, short_call_symbol=SC, long_call_symbol=LC,
        quantity=1, entry_net_credit=CREDITO, max_loss=825.0, max_profit=CREDITO,
        lower_breakeven=SHORT_PUT - 1.75, upper_breakeven=SHORT_CALL + 1.75, entry_spot=7600.0,
        open_schwab_order_id="OPEN-7", status="working",
        entry_ts=datetime.now() - timedelta(minutes=90))
    repo.mark_real_condor_fill(conn, pid, entry_credit_ps=CREDITO / 100.0, entry_net_credit=CREDITO,
                               open_schwab_order_id="OPEN-7",
                               entry_ts=datetime.now() - timedelta(minutes=90))
    return conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (pid,)).fetchone()


@pytest.fixture
def mails(monkeypatch):
    enviados = []
    from options_advisor.alerts import notifier
    monkeypatch.setattr(notifier, "send_email_robot_real",
                        lambda a, c: enviados.append((a, c)) or True)
    return enviados


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    """El walk duerme entre sondeos; acá no esperamos de verdad."""
    monkeypatch.setattr(rcs.time, "sleep", lambda s: None)


class BrokerQueCumple:
    """Acepta la recompra y la reporta LLENADA, con las ejecuciones reales de las 4 patas."""

    # Los precios son los de la propia cadena: se recompra al ask y se venden las alas al bid, que
    # es justo lo que mide `condor_exit_value`. Llena a $2.90 de débito neto.
    def __init__(self, precios=(0.30, 0.20, 3.00, 0.20)):
        self.ordenes = []
        self._precios = precios

    def place_order(self, account_hash, payload):
        self.ordenes.append(payload)
        return "CIERRE-STOP-1"

    def replace_order(self, account_hash, order_id, payload):
        self.ordenes.append(payload)
        return "CIERRE-STOP-2"

    def cancel_order(self, account_hash, order_id):
        pass

    def get_order(self, account_hash, order_id):
        simbolos = [SP, LP, SC, LC]
        instrucciones = ["BUY_TO_CLOSE", "SELL_TO_CLOSE", "BUY_TO_CLOSE", "SELL_TO_CLOSE"]
        return {
            "status": "FILLED",
            "orderLegCollection": [
                {"legId": i + 1, "orderLegType": "OPTION", "instruction": instrucciones[i],
                 "instrument": {"symbol": simbolos[i], "assetType": "OPTION"}}
                for i in range(4)
            ],
            "orderActivityCollection": [{"executionLegs": [
                {"legId": i + 1, "quantity": 1, "price": self._precios[i]} for i in range(4)
            ]}],
        }


def _cfg():
    return load_settings().intraday_condor


def test_con_el_mercado_en_contra_SALE_la_orden_de_recompra(conn, fila, mails):
    """La prueba que importa: el stop no es una orden en el broker, la manda el robot. Que salga."""
    broker = BrokerQueCumple()
    lce._manage_open_position(conn, broker, "HASH", _cadena_en_contra(), SPOT, AS_OF, _cfg(), fila)

    assert broker.ordenes, "El stop NO mandó ninguna orden: la posición se quedaba sin protección"
    payload = broker.ordenes[0]
    assert payload["orderType"] == "NET_DEBIT"
    assert payload["complexOrderStrategyType"] == "IRON_CONDOR"
    instrucciones = [(l["instruction"], l["instrument"]["symbol"]) for l in payload["orderLegCollection"]]
    assert instrucciones == [("BUY_TO_CLOSE", SP), ("SELL_TO_CLOSE", LP),
                             ("BUY_TO_CLOSE", SC), ("SELL_TO_CLOSE", LC)], \
        "Recompró algo distinto de las 4 patas exactas que tenía abiertas"


def test_la_fila_queda_cerrada_con_motivo_stop_loss_y_el_pnl_real(conn, fila, mails):
    broker = BrokerQueCumple()
    lce._manage_open_position(conn, broker, "HASH", _cadena_en_contra(), SPOT, AS_OF, _cfg(), fila)

    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["status"] == "closed"
    assert r["close_reason"] == "stop_loss"
    # Pagó (0.30 + 3.00) − (0.20 + 0.20) = 2.90 → $290 por salir, el mismo precio con el que se midió
    # el stop. Ahí está el arreglo del 02/09: se mide y se sale al MISMO precio.
    assert r["close_value"] == 290.0
    assert r["realized_pnl"] == round(CREDITO - 290.0, 2)
    assert mails and "CERRÓ" in mails[0][0]


def test_la_perdida_realizada_queda_cerca_del_stop_y_no_se_dispara_de_gusto(conn, fila, mails):
    """El sentido del stop: que la pérdida REAL no se vaya mucho más allá del límite configurado.
    (2026-09-02: el stop de $100 terminó en −$125 porque se medía al mid y se salía al precio real.)"""
    broker = BrokerQueCumple()
    lce._manage_open_position(conn, broker, "HASH", _cadena_en_contra(), SPOT, AS_OF, _cfg(), fila)

    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    tope = _cfg().stop_loss_dollars
    assert r["realized_pnl"] < 0
    assert abs(r["realized_pnl"]) <= tope * 1.2, (
        f"El stop de ${tope:.0f} terminó en una pérdida de ${abs(r['realized_pnl']):.0f}")


def test_sin_perdida_no_toca_nada(conn, fila, mails):
    """Contraprueba: con la posición tranquila no sale ninguna orden. Un stop que dispara solo
    tampoco sirve."""
    class BrokerQueSeQueja(BrokerQueCumple):
        def place_order(self, account_hash, payload):
            raise AssertionError("No había ni pérdida ni objetivo: no se manda nada")

    lce._manage_open_position(conn, BrokerQueSeQueja(), "HASH", _cadena_tranquila(), 7600.0,
                              AS_OF, _cfg(), fila)
    r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert r["status"] == "open"


def test_un_credito_mal_anotado_corre_el_stop(conn, mails):
    """Por qué el arreglo de hoy toca al stop: el P&L se mide contra el crédito de entrada.

    Con el crédito REAL ($175) esta cadena marca −$115 de salida y dispara. Con el que anotaba el
    robot antes ($165, el límite que mandó) la MISMA cadena marca −$125: la posición se cierra en un
    punto distinto del que el usuario configuró. El stop no es más laxo ni más estricto por sí solo
    — es que estaba midiendo contra un número equivocado."""
    from options_advisor.simulator import iron_condor

    cfg = _cfg()
    cadena = _cadena_en_contra()
    salida_pc = iron_condor.condor_exit_value(cadena, SHORT_PUT, SHORT_CALL, LONG_PUT, LONG_CALL)

    real = round(CREDITO - salida_pc, 2)          # crédito verdadero
    anotado = round(165.0 - salida_pc, 2)         # el límite que se anotaba antes
    assert real != anotado
    assert iron_condor.should_close_condor(real, CREDITO, False, cfg,
                                           unrealized_de_salida=real)[1] == "stop_loss"
    assert abs(anotado - real) == pytest.approx(10.0, abs=0.01)
