"""Pausa PROPIA del condor REAL y cierre manual de UNA posición específica (usuario 2026-08-14:
"un botón de pausar operación en iron y un botón de dar la orden de vender la operación aunque no esté
en la ganancia que marque... si hay más operaciones y quiero cerrar manual una en específico").

Lo que se protege acá:
  · la pausa del real NO frena el papel (son banderas distintas) y frena SOLO aperturas nuevas;
  · el cierre manual se pide POR POSICIÓN — pedirlo en una no toca a las otras;
  · el cierre manual gana sobre la regla de ganancia/stop, y se registra con motivo 'manual' para no
    ensuciar la racha de stop-loss del día;
  · el dashboard nunca manda la orden: solo deja la bandera que el scheduler ejecuta.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from options_advisor.broker.models import Greeks, IntradayBar, OptionChain, OptionContract
from options_advisor.config import load_settings
from options_advisor.execution import live_condor_engine as lce
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 8, 14)
SPOT = 7600.0


def _opt(otype: str, strike: float, mid: float) -> OptionContract:
    return OptionContract(
        symbol=f"SPXW  260814{'P' if otype == 'put' else 'C'}{round(strike * 1000):08d}",
        option_type=otype, strike=strike, expiration=AS_OF,
        bid=round(mid - 0.05, 2), ask=round(mid + 0.05, 2), last_price=mid,
        implied_volatility=0.12, open_interest=1000, volume=500,
        greeks=Greeks(delta=-0.10 if otype == "put" else 0.10, gamma=0.01, theta=-0.5, vega=0.1,
                      rho=0.01, source="broker"),
    )


def _chain() -> OptionChain:
    """El condor sigue valiendo casi lo mismo que al abrir: ni profit target ni stop-loss lo tocarían."""
    contracts = []
    for k in range(7480, 7721, 10):
        contracts.append(_opt("put", float(k), 2.0 if k == 7500 else 1.0))
        contracts.append(_opt("call", float(k), 2.0 if k == 7700 else 1.0))
    return OptionChain(symbol="SPX", as_of=AS_OF, underlying_price=SPOT, contracts=contracts)


def _calm_bars() -> list[IntradayBar]:
    """Día calmo dentro de la ventana de entrada (10:00 ET): rango minúsculo, señal válida."""
    start = datetime(2026, 8, 14, 10, 0)
    return [
        IntradayBar(symbol="SPX", timestamp=start + timedelta(minutes=i), open=c, high=c, low=c,
                    close=c, volume=100)
        for i, c in enumerate([7600.0, 7601.0, 7600.5, 7600.0])
    ]


def _settings():
    return load_settings()


def _insert_open_condor(conn, *, short_put=7500.0, short_call=7700.0, credit=200.0) -> int:
    pid = repo.insert_real_condor_position(
        conn, underlying="SPX", entry_date=AS_OF, expiration_date=AS_OF,
        short_put_strike=short_put, short_call_strike=short_call,
        long_put_strike=short_put - 10.0, long_call_strike=short_call + 10.0,
        short_put_symbol="SP", long_put_symbol="LP", short_call_symbol="SC", long_call_symbol="LC",
        quantity=1, entry_net_credit=credit, max_loss=800.0, max_profit=credit,
        lower_breakeven=short_put - 2.0, upper_breakeven=short_call + 2.0, entry_spot=SPOT,
        open_schwab_order_id="OID-1", status="working", entry_ts=datetime.now() - timedelta(minutes=90),
    )
    repo.mark_real_condor_fill(conn, pid, entry_credit_ps=credit / 100.0, entry_net_credit=credit,
                               open_schwab_order_id="OID-1", entry_ts=datetime.now() - timedelta(minutes=90))
    return pid


# --------------------------------- pausa propia del real ---------------------------------

def test_real_pause_is_independent_from_paper_pause():
    conn = db.connect(":memory:")
    assert repo.is_condor_real_paused(conn) is False
    repo.set_condor_real_paused(conn, True)
    assert repo.is_condor_real_paused(conn) is True
    # El papel NO se entera: sigue operando en el Simulador.
    assert repo.is_condor_paused(conn) is False
    repo.set_condor_real_paused(conn, False)
    assert repo.is_condor_real_paused(conn) is False


def test_resume_all_clears_the_real_condor_pause_too():
    conn = db.connect(":memory:")
    repo.set_condor_real_paused(conn, True)
    repo.set_all_paused(conn, True)
    repo.resume_all(conn)
    assert repo.is_condor_real_paused(conn) is False
    assert repo.is_all_paused(conn) is False


def test_paused_real_condor_never_opens_even_armed_and_calm(monkeypatch):
    """El escenario que de verdad importa: TODO prendido (sistema real, condor live, día AUTORIZADO,
    mercado abierto, día calmo con señal válida) pero PAUSADO por el usuario → no sale ninguna orden."""
    from options_advisor.scheduler import market_calendar

    monkeypatch.setattr(market_calendar, "market_session", lambda *a, **k: "abierto")

    conn = db.connect(":memory:")
    repo.arm_condor_live_today(conn, AS_OF)
    repo.set_condor_real_paused(conn, True)

    class _Broker:
        def resolve_account_hash(self, *a, **k):
            return "HASH"

        def get_intraday_bars(self, *a, **k):
            return _calm_bars()

        def get_option_chain(self, *a, **k):
            return _chain()

        def place_order(self, *a, **k):
            raise AssertionError("PAUSADO: no debe mandar ninguna orden de apertura")

    s = _settings()
    object.__setattr__(s.intraday_condor, "live_enabled", True)
    object.__setattr__(s.live_trading, "enabled", True)
    object.__setattr__(s.live_trading, "dry_run", False)

    lce.process_real_condor_cycle(conn, _Broker(), s, AS_OF)   # no debe explotar ni operar
    assert repo.count_real_condor_opens_today(conn, AS_OF) == 0


# --------------------------------- cierre manual por posición ---------------------------------

def test_manual_close_request_only_touches_the_chosen_position():
    conn = db.connect(":memory:")
    a = _insert_open_condor(conn, short_put=7500.0, short_call=7700.0)
    b = _insert_open_condor(conn, short_put=7400.0, short_call=7800.0)
    repo.request_real_condor_manual_close(conn, b)
    assert repo.is_real_condor_manual_close_requested(conn, b) is True
    assert repo.is_real_condor_manual_close_requested(conn, a) is False


def test_manual_close_can_be_undone():
    conn = db.connect(":memory:")
    pid = _insert_open_condor(conn)
    repo.request_real_condor_manual_close(conn, pid)
    repo.cancel_real_condor_manual_close(conn, pid)
    assert repo.is_real_condor_manual_close_requested(conn, pid) is False


def test_manual_close_ignores_already_closed_positions():
    conn = db.connect(":memory:")
    pid = _insert_open_condor(conn)
    repo.close_real_condor_position(conn, pid, AS_OF, 50.0, "profit_target", 150.0, close_ts=datetime.now())
    repo.request_real_condor_manual_close(conn, pid)
    assert repo.is_real_condor_manual_close_requested(conn, pid) is False


def test_manual_close_flag_helper_tolerates_missing_column():
    """Filas de una base vieja (antes de la migración) o fixtures armados a mano: sin columna = sin pedido."""
    class _RowSinColumna:
        def __getitem__(self, key):
            raise IndexError(key)

    assert lce._manual_close_requested(_RowSinColumna()) is False


def test_manual_close_beats_the_profit_rule_and_sends_the_buyback():
    """El condor NO llegó al objetivo de ganancia, pero el usuario pidió cerrarlo: se manda la recompra
    combinada y queda cerrado con motivo 'manual'."""
    conn = db.connect(":memory:")
    pid = _insert_open_condor(conn, credit=200.0)
    repo.request_real_condor_manual_close(conn, pid)
    row = repo.get_open_real_condor_positions(conn)[0]

    enviados = []

    class _Broker:
        def place_order(self, account_hash, payload):
            enviados.append(payload)
            return "CLOSE-1"

        def get_order(self, account_hash, order_id):
            return {"status": "FILLED", "price": "0.60"}

        def cancel_order(self, account_hash, order_id):
            raise AssertionError("una posición LLENA se cierra recomprando, no cancelando")

    cfg = _settings().intraday_condor
    lce._manage_open_position(conn, _Broker(), "HASH", _chain(), SPOT, AS_OF, cfg, row)

    assert enviados, "debería haberse mandado la orden combinada de recompra"
    assert enviados[0]["orderType"] == "NET_DEBIT"
    assert len(enviados[0]["orderLegCollection"]) == 4
    cerrada = [r for r in repo.get_closed_real_condor_positions(conn) if r["id"] == pid]
    assert cerrada and cerrada[0]["close_reason"] == "manual"


def test_manual_close_does_not_count_as_a_stop_loss_streak():
    """El motivo 'manual' no puede frenar el día como si hubieran saltado los stops."""
    conn = db.connect(":memory:")
    pid = _insert_open_condor(conn)
    repo.close_real_condor_position(conn, pid, AS_OF, 300.0, "manual", -100.0, close_ts=datetime.now())
    assert repo.real_condor_consecutive_stop_losses_today(conn, AS_OF) == 0


# ---------------- símbolos OCC de las patas (bug real 2026-08-14) ----------------

def test_legs_use_the_occ_symbol_not_the_underlying():
    """Bug real encontrado con el mercado abierto y el condor ya autorizado: el motor tomaba
    `contract.symbol` para armar la orden combinada, pero ese campo es el SUBYACENTE ("$SPX" en las
    cuatro patas), así que `build_iron_condor_open` la rechazaba cada minuto con "hacen falta 4
    símbolos OCC distintos". El símbolo bueno viene del broker y ahora se guarda en `occ_symbol`."""
    class _Build:
        def __init__(self, legs):
            self.legs = legs

    def _c(occ):
        # symbol = subyacente (igual en las 4 patas, como lo devuelve el parser de la cadena real)
        return type("C", (), {"symbol": "$SPX", "occ_symbol": occ})()

    build = _Build([
        ("sell", "put", _c("SPXW  260814P07765000")),
        ("buy", "put", _c("SPXW  260814P07755000")),
        ("sell", "call", _c("SPXW  260814C07830000")),
        ("buy", "call", _c("SPXW  260814C07840000")),
    ])
    legs = lce._legs_from_build(build)
    assert legs is not None
    assert legs.short_put_symbol == "SPXW  260814P07765000"
    assert legs.long_call_symbol == "SPXW  260814C07840000"
    # Y con esos símbolos la orden combinada SÍ se arma (es la validación que venía fallando).
    from options_advisor.execution import schwab_orders as so
    payload = so.build_iron_condor_open(legs.short_put_symbol, legs.long_put_symbol,
                                        legs.short_call_symbol, legs.long_call_symbol,
                                        quantity=1, net_credit_limit=1.80)
    assert len(payload["orderLegCollection"]) == 4


def test_legs_are_none_without_occ_symbols_so_nothing_is_sent():
    """Sin `occ_symbol` NO se arma nada: preferimos no abrir antes que mandar una orden sobre un
    símbolo reconstruido a mano (en índices, SPX vs. SPXW, eso es operar otro instrumento)."""
    class _Build:
        def __init__(self, legs):
            self.legs = legs

    def _c():
        return type("C", (), {"symbol": "$SPX", "occ_symbol": None})()

    build = _Build([("sell", "put", _c()), ("buy", "put", _c()),
                    ("sell", "call", _c()), ("buy", "call", _c())])
    assert lce._legs_from_build(build) is None


def test_legs_are_none_when_the_four_symbols_are_not_distinct():
    """Cinturón extra: cuatro símbolos iguales (lo que pasaba con el subyacente) se frenan ACÁ,
    antes de llegar al validador de la orden."""
    class _Build:
        def __init__(self, legs):
            self.legs = legs

    def _c():
        return type("C", (), {"symbol": "$SPX", "occ_symbol": "$SPX"})()

    build = _Build([("sell", "put", _c()), ("buy", "put", _c()),
                    ("sell", "call", _c()), ("buy", "call", _c())])
    assert lce._legs_from_build(build) is None
