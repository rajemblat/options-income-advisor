"""Exposición de los naked puts: el récord histórico y el de ahora (usuario 2026-09-14).

"Quiero saber en el momento que más exposición tenía cuánto fue, por ejemplo NVDA 100 shares de un
put x 220 dólares." O sea el NOCIONAL —strike × 100 × contratos—, no el colateral que el broker
traba. Y después: "un cartel rojo donde siempre se quede la última exposición máxima y la fecha; si
otro día la pasa se actualiza y si no llega queda esta".

Lo que se afirma acá:
  · el máximo es lo que hubo VIVO AL MISMO TIEMPO, no la suma del día ni la suma histórica;
  · una posición cerrada deja de contar — su riesgo ya no existe;
  · el récord nunca baja: si hoy no se llega, queda el de antes, con su fecha;
  · las órdenes que murieron (rechazadas, canceladas) no comprometieron nada y no cuentan;
  · el dry-run nunca entra: es simulacro, no exposición real.
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


def _abrir(conn, *, symbol: str, strike: float, contratos: int, ts: datetime,
           dry_run: bool = False, sent: bool = True, estado: str | None = None) -> int:
    oid = repo.insert_live_order_log(
        conn, log_date=ts.date(), log_ts=ts, symbol=symbol, action="SELL_TO_OPEN",
        strike=strike, expiration="2026-10-17", approved=True, final_contracts=contratos,
        start_limit_price=1.0, collateral=strike * 20, dry_run=dry_run, sent=sent,
        reasons="", payload_json="{}", ladder_json="[]",
    )
    if estado:
        conn.execute("UPDATE live_order_log SET order_status = ? WHERE id = ?", (estado, oid))
        conn.commit()
    return oid


def _cerrar(conn, oid: int, ts: datetime) -> None:
    repo.mark_real_position_closed(
        conn, oid, close_ts=ts, close_fill_price=0.1, close_reason="take_profit",
        realized_pnl=50.0, close_schwab_order_id="x",
    )


# ─────────────────── lo esencial ───────────────────

def test_el_maximo_es_lo_que_hubo_vivo_al_mismo_tiempo(conn):
    """Dos posiciones juntas suman; el ejemplo del usuario: NVDA 100 acciones × $220 = $22.000."""
    _abrir(conn, symbol="NVDA", strike=220.0, contratos=1, ts=datetime(2026, 8, 11, 10, 0))
    _abrir(conn, symbol="AAPL", strike=285.0, contratos=1, ts=datetime(2026, 8, 11, 15, 2))
    exp = repo.exposicion_naked(conn)
    assert exp["maximo"] == pytest.approx(22_000 + 28_500)
    assert exp["maximo_fecha"] == "2026-08-11"
    assert exp["maximo_posiciones"] == 2


def test_una_posicion_cerrada_deja_de_contar(conn):
    """Lo que importa es el riesgo VIVO: si la primera cerró antes de que abriera la segunda, nunca
    hubo un momento con las dos encima."""
    a = _abrir(conn, symbol="NVDA", strike=220.0, contratos=1, ts=datetime(2026, 8, 11, 10, 0))
    _cerrar(conn, a, datetime(2026, 8, 11, 12, 0))
    _abrir(conn, symbol="AAPL", strike=285.0, contratos=1, ts=datetime(2026, 8, 11, 14, 0))
    exp = repo.exposicion_naked(conn)
    assert exp["maximo"] == pytest.approx(28_500)     # la más grande sola, no la suma
    assert exp["ahora"] == pytest.approx(28_500)
    assert exp["ahora_posiciones"] == 1


def test_el_record_NO_baja_cuando_hoy_se_expone_menos(conn):
    """El pedido textual: "si otro día la pasa se actualiza y si no llega queda esta"."""
    a = _abrir(conn, symbol="AAPL", strike=285.0, contratos=2, ts=datetime(2026, 8, 11, 15, 2))
    _cerrar(conn, a, datetime(2026, 8, 20, 10, 0))
    _abrir(conn, symbol="AAL", strike=13.0, contratos=1, ts=datetime(2026, 9, 14, 10, 0))
    exp = repo.exposicion_naked(conn)
    assert exp["maximo"] == pytest.approx(57_000)      # el de agosto
    assert exp["maximo_fecha"] == "2026-08-11"
    assert exp["ahora"] == pytest.approx(1_300)        # hoy es mucho menos


def test_el_record_SI_sube_cuando_se_supera(conn):
    _abrir(conn, symbol="AAL", strike=13.0, contratos=1, ts=datetime(2026, 8, 11, 10, 0))
    _abrir(conn, symbol="AAPL", strike=285.0, contratos=1, ts=datetime(2026, 9, 14, 10, 0))
    exp = repo.exposicion_naked(conn)
    assert exp["maximo"] == pytest.approx(1_300 + 28_500)
    assert exp["maximo_fecha"] == "2026-09-14"


def test_varios_contratos_multiplican(conn):
    _abrir(conn, symbol="NVDA", strike=220.0, contratos=5, ts=datetime(2026, 8, 11, 10, 0))
    assert repo.exposicion_naked(conn)["maximo"] == pytest.approx(110_000)


# ─────────────────── lo que NO cuenta ───────────────────

def test_una_orden_rechazada_no_expuso_nada(conn):
    _abrir(conn, symbol="AAPL", strike=285.0, contratos=1, ts=datetime(2026, 8, 11, 10, 0),
           estado="REJECTED")
    assert repo.exposicion_naked(conn)["maximo"] == 0


def test_el_dry_run_no_cuenta(conn):
    _abrir(conn, symbol="AAPL", strike=285.0, contratos=1, ts=datetime(2026, 8, 11, 10, 0),
           dry_run=True)
    assert repo.exposicion_naked(conn)["operaciones"] == 0


def test_una_orden_que_nunca_se_mando_no_cuenta(conn):
    _abrir(conn, symbol="AAPL", strike=285.0, contratos=1, ts=datetime(2026, 8, 11, 10, 0),
           sent=False)
    assert repo.exposicion_naked(conn)["maximo"] == 0


def test_sin_operaciones_devuelve_ceros_sin_romper(conn):
    exp = repo.exposicion_naked(conn)
    assert exp["maximo"] == 0 and exp["ahora"] == 0 and exp["maximo_fecha"] is None


# ─────────────────── el borde que inflaba el pico ───────────────────

def test_cerrar_y_abrir_en_el_mismo_instante_no_infla_el_pico(conn):
    """Si una sale y otra entra en el mismo segundo, no hubo un momento con las dos vivas. Contarlo
    daría un récord que nunca existió."""
    a = _abrir(conn, symbol="NVDA", strike=220.0, contratos=1, ts=datetime(2026, 8, 11, 10, 0))
    momento = datetime(2026, 8, 11, 12, 0, 0)
    _cerrar(conn, a, momento)
    _abrir(conn, symbol="AAPL", strike=285.0, contratos=1, ts=momento)
    assert repo.exposicion_naked(conn)["maximo"] == pytest.approx(28_500)


# ─────────────────── quiénes formaban el pico ───────────────────

def test_el_detalle_lista_solo_lo_que_estaba_vivo_en_el_pico(conn):
    """Usuario 2026-09-14: "para que quede claro, esta es la exposición máxima, lo máximo que una
    vez estuvo, no acumulado". El detalle es lo que hace verificable esa afirmación."""
    a = _abrir(conn, symbol="NVDA", strike=220.0, contratos=1, ts=datetime(2026, 8, 11, 10, 0))
    _cerrar(conn, a, datetime(2026, 8, 11, 11, 0))          # cerró ANTES del pico
    _abrir(conn, symbol="AAPL", strike=285.0, contratos=1, ts=datetime(2026, 8, 11, 12, 0))
    _abrir(conn, symbol="AMZN", strike=220.0, contratos=1, ts=datetime(2026, 8, 11, 13, 0))
    exp = repo.exposicion_naked(conn)

    simbolos = [d["symbol"] for d in exp["maximo_detalle"]]
    assert simbolos == ["AAPL", "AMZN"], "NVDA ya había cerrado: no puede figurar en el pico"
    assert sum(d["nocional"] for d in exp["maximo_detalle"]) == pytest.approx(exp["maximo"])
    assert len(exp["maximo_detalle"]) == exp["maximo_posiciones"]


def test_el_detalle_viene_ordenado_de_mayor_a_menor(conn):
    _abrir(conn, symbol="AAL", strike=13.0, contratos=1, ts=datetime(2026, 8, 11, 10, 0))
    _abrir(conn, symbol="AAPL", strike=285.0, contratos=1, ts=datetime(2026, 8, 11, 11, 0))
    detalle = repo.exposicion_naked(conn)["maximo_detalle"]
    assert [d["symbol"] for d in detalle] == ["AAPL", "AAL"]


def test_el_detalle_suma_exactamente_el_maximo(conn):
    """Si la suma del detalle no diera el máximo, uno de los dos estaría mal y no habría forma de
    saber cuál."""
    _abrir(conn, symbol="NVDA", strike=220.0, contratos=2, ts=datetime(2026, 8, 11, 10, 0))
    _abrir(conn, symbol="AAPL", strike=285.0, contratos=1, ts=datetime(2026, 8, 11, 11, 0))
    _abrir(conn, symbol="AAL", strike=13.0, contratos=5, ts=datetime(2026, 8, 11, 12, 0))
    exp = repo.exposicion_naked(conn)
    assert sum(d["nocional"] for d in exp["maximo_detalle"]) == pytest.approx(exp["maximo"])
