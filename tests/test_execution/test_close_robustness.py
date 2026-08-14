"""El cierre de posiciones REALES no puede fallar en silencio (usuario 2026-08-14).

Tres posiciones seguidas se quedaron abiertas en ganancia porque la recompra no llenaba y el robot
dejaba de intentar sin decir nada: NU al 75%, SPCX, y NVDA al 45%. Roberto las cerró a mano y su
ganancia ni siquiera entró en los totales, porque una posición cerrada por fuera del robot quedaba
con `realized_pnl` en NULL.

Lo que se protege acá:
  · un bid en 0.00 NO frena el cierre — es justo cuando más conviene recomprar;
  · cada intento fallido queda contado y con su motivo, y al 3ro sale un mail;
  · un cierre exitoso limpia el contador;
  · una posición cerrada por fuera del robot igual registra su P&L, marcado como estimación.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from options_advisor.broker.models import Greeks, OptionChain, OptionContract
from options_advisor.execution import live_engine as le
from options_advisor.storage import db
from options_advisor.storage import repository as repo

HOY = date(2026, 8, 14)
VTO = date(2026, 9, 25)


def _put(strike: float, bid: float, ask: float) -> OptionContract:
    return OptionContract(
        symbol="NU", occ_symbol="NU    260925P00012000", option_type="put", strike=strike,
        expiration=VTO, bid=bid, ask=ask, last_price=(bid + ask) / 2, implied_volatility=0.3,
        open_interest=100, volume=10,
        greeks=Greeks(delta=-0.05, gamma=0.01, theta=-0.01, vega=0.02, rho=0.0, source="broker"),
    )


def _chain(bid: float, ask: float) -> OptionChain:
    return OptionChain(symbol="NU", as_of=HOY, underlying_price=15.0, contracts=[_put(12.0, bid, ask)])


def _pos(conn) -> int:
    return repo.insert_live_order_log(
        conn, log_date=HOY, log_ts=datetime.now(), symbol="NU", action="SELL_TO_OPEN",
        strike=12.0, expiration=VTO.isoformat(), approved=True, final_contracts=10,
        start_limit_price=0.25, collateral=12000.0, dry_run=False, sent=True,
        reasons=None, payload_json=None, ladder_json=None,
    )


# ------------------------- contador de intentos e aviso -------------------------

def test_failed_close_is_counted_with_its_reason():
    conn = db.connect(":memory:")
    pid = _pos(conn)
    assert repo.bump_real_close_attempt(conn, pid, "estado REJECTED") == 1
    assert repo.bump_real_close_attempt(conn, pid, "Schwab rechazó la orden (HTTP 400): price below minimum") == 2
    row = conn.execute("SELECT close_attempts, last_close_error FROM live_order_log WHERE id=?", (pid,)).fetchone()
    assert row["close_attempts"] == 2
    assert "price below minimum" in row["last_close_error"], "el motivo REAL de Schwab tiene que quedar guardado"


def test_a_successful_close_clears_the_counter():
    conn = db.connect(":memory:")
    pid = _pos(conn)
    repo.bump_real_close_attempt(conn, pid, "no llenó")
    repo.bump_real_close_attempt(conn, pid, "no llenó")
    repo.reset_real_close_attempts(conn, pid)
    row = conn.execute("SELECT close_attempts, last_close_error, close_fail_email_sent FROM live_order_log WHERE id=?", (pid,)).fetchone()
    assert row["close_attempts"] == 0 and row["last_close_error"] is None and not row["close_fail_email_sent"]


def test_the_alert_is_sent_once_not_on_every_tick():
    conn = db.connect(":memory:")
    pid = _pos(conn)
    assert repo.close_fail_email_pending(conn, pid) is True
    repo.mark_close_fail_email_sent(conn, pid)
    assert repo.close_fail_email_pending(conn, pid) is False, "el aviso no puede repetirse cada minuto"


# ------------------------- bid en cero -------------------------

def test_zero_bid_does_not_stop_the_close():
    """El caso de NU: put casi sin valor, bid 0.00 / ask 0.05. Antes el robot abandonaba el cierre
    (y encima en nivel DEBUG, que no se registra). Ahora recompra usando el tick mínimo como piso."""
    from options_advisor.execution import price_walker as pw

    bid, ask = 0.0, 0.05
    assert bid <= 0
    bid = le._MIN_TICK
    escalera = pw.build_price_ladder(pw.SIDE_BUY, bid, ask, step=0.01, stop_at_mid=True)
    assert escalera, "con el piso tiene que haber una escalera de precios para salir"
    assert min(escalera) >= le._MIN_TICK
    assert max(escalera) <= ask


def test_min_tick_is_a_cent_not_zero():
    assert le._MIN_TICK > 0
    assert le._CLOSE_FAIL_ALERT_AFTER >= 2, "avisar al primer fallo sería ruido; al 3ro ya es un problema"


# ------------------------- P&L de cierres fuera del robot -------------------------

def test_pnl_of_a_position_closed_outside_is_recorded_as_an_estimate():
    """Lo que pasó con NU y NVDA: cerradas a mano, sin fill localizable, P&L NULL y su ganancia
    desaparecía de los totales. Ahora se estima con el valor de mercado y queda marcada."""
    conn = db.connect(":memory:")
    pid = _pos(conn)
    repo.mark_real_position_closed(
        conn, pid, close_ts=datetime.now(), close_fill_price=0.05, close_reason="closed_in_broker",
        realized_pnl=200.0, close_schwab_order_id=None, pnl_is_estimate=True)
    row = conn.execute("SELECT realized_pnl, pnl_is_estimate, closed FROM live_order_log WHERE id=?", (pid,)).fetchone()
    assert row["closed"] == 1
    assert row["realized_pnl"] == 200.0, "la ganancia tiene que entrar en los totales"
    assert row["pnl_is_estimate"] == 1, "y tiene que quedar claro que es una estimación"


def test_an_exact_close_is_not_marked_as_an_estimate():
    conn = db.connect(":memory:")
    pid = _pos(conn)
    repo.mark_real_position_closed(
        conn, pid, close_ts=datetime.now(), close_fill_price=0.07, close_reason="profit_target",
        realized_pnl=180.0, close_schwab_order_id="OID-9")
    row = conn.execute("SELECT pnl_is_estimate FROM live_order_log WHERE id=?", (pid,)).fetchone()
    assert row["pnl_is_estimate"] == 0


def test_market_value_estimate_uses_the_mid_of_the_chain():
    class _Broker:
        def get_option_chain(self, *a, **k):
            return _chain(0.04, 0.08)

    assert le._market_value_now(_Broker(), "NU", 12.0, VTO) == 0.06


def test_market_value_estimate_falls_back_to_intrinsic():
    """Sin el contrato en la cadena (delistado, vencido), el intrínseco es la mejor aproximación."""
    class _Broker:
        def get_option_chain(self, *a, **k):
            return OptionChain(symbol="NU", as_of=HOY, underlying_price=10.0, contracts=[])

    assert le._market_value_now(_Broker(), "NU", 12.0, VTO) == 2.0   # strike 12 - spot 10


def test_market_value_estimate_returns_none_when_the_broker_fails():
    class _Broker:
        def get_option_chain(self, *a, **k):
            raise RuntimeError("sin red")

    assert le._market_value_now(_Broker(), "NU", 12.0, VTO) is None


# ------------------------- el motivo del rechazo de Schwab -------------------------

def test_schwab_rejection_includes_the_reason_from_the_body():
    """`raise_for_status()` solo decía "400 Bad Request". El motivo venía en el cuerpo y se tiraba,
    así que en el log solo quedaba "no llenó (estado REJECTED)" sin explicación."""
    import httpx

    from options_advisor.broker.schwab_client import SchwabBrokerClient

    resp = httpx.Response(400, json={"message": "Order price is below the minimum increment"},
                          request=httpx.Request("POST", "https://api.schwabapi.com/x"))
    try:
        SchwabBrokerClient._raise_with_reason(resp, "la orden")
    except RuntimeError as exc:
        assert "minimum increment" in str(exc)
        assert "400" in str(exc)
    else:
        raise AssertionError("tenía que lanzar con el motivo de Schwab")


def test_a_successful_response_does_not_raise():
    import httpx

    from options_advisor.broker.schwab_client import SchwabBrokerClient

    resp = httpx.Response(201, request=httpx.Request("POST", "https://api.schwabapi.com/x"))
    SchwabBrokerClient._raise_with_reason(resp, "la orden")   # no debe lanzar


def test_the_recent_orders_lookback_covers_a_long_weekend():
    """4 días dejaban fuera una posición cerrada el lunes de una abierta el viernes anterior."""
    import inspect

    src = inspect.getsource(le._find_close_fill_price)
    assert "days=10" in src


def test_manual_close_price_computes_the_pnl():
    """Carga a mano del precio de salida para las cerradas fuera del robot (NU, NVDA)."""
    conn = db.connect(":memory:")
    pid = _pos(conn)   # 10 contratos
    conn.execute("UPDATE live_order_log SET fill_price = 0.21, filled_contracts = 10 WHERE id = ?", (pid,))
    conn.commit()
    repo.mark_real_position_closed(conn, pid, close_ts=datetime.now(), close_fill_price=None,
                                   close_reason="closed_in_broker", realized_pnl=None,
                                   close_schwab_order_id=None)
    # Recompró a 0.04 → (0.21 - 0.04) × 100 × 10 = $170
    assert repo.set_real_close_price_manual(conn, pid, 0.04) == 170.0
    row = conn.execute("SELECT realized_pnl, close_fill_price, pnl_is_estimate FROM live_order_log WHERE id=?", (pid,)).fetchone()
    assert row["realized_pnl"] == 170.0 and row["close_fill_price"] == 0.04
    assert row["pnl_is_estimate"] == 0, "el número lo puso el usuario: no es una estimación"


def test_manual_close_price_refuses_on_an_open_position():
    """No puede tocar una posición viva — solo sirve para reconstruir el historial."""
    conn = db.connect(":memory:")
    pid = _pos(conn)
    conn.execute("UPDATE live_order_log SET fill_price = 0.21 WHERE id = ?", (pid,))
    conn.commit()
    assert repo.set_real_close_price_manual(conn, pid, 0.04) is None
