"""Tests del lazo de trading real en dry-run (sombrea la decisión del simulador y registra el plan)."""

from __future__ import annotations

from datetime import date

import pytest

from options_advisor.broker.models import Greeks, OptionContract
from options_advisor.config import load_settings
from options_advisor.execution import live_engine
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 8, 10)


class _Result:
    def __init__(self, contract, premium):
        self.contract = contract
        self.premium = premium
        self.passed = True
        self.reasons = []
        self.context = {}


class _Snap:
    def __init__(self, price):
        self.price = price
        self.snapshot_date = AS_OF


def _contract(strike=11.0):
    return OptionContract(
        symbol="AAL", option_type="put", strike=strike, expiration=date(2026, 9, 18),
        bid=1.30, ask=1.70, last_price=1.50, implied_volatility=0.4, open_interest=1000, volume=500,
        greeks=Greeks(delta=-0.25, gamma=0.01, theta=-0.5, vega=0.1, rho=0.01, source="broker"),
    )


def _settings(*, enabled=False, dry_run=True, max_orders_per_day=None):
    """Config real DETERMINÍSTICA para el test — no depende del settings.yaml de producción (que ya está
    en real). Por default: dry-run apagado de envío (enabled=False, dry_run=True)."""
    s = load_settings()
    upd = {"enabled": enabled, "dry_run": dry_run}
    if max_orders_per_day is not None:
        upd["max_orders_per_day"] = max_orders_per_day
    lt = s.live_trading.model_copy(update=upd)
    return s.model_copy(update={"live_trading": lt})


class _FakeBroker:
    """Broker mínimo para el camino REAL: siempre llena en el primer intento."""
    def resolve_account_hash(self, account_number=None):
        return "HASH"
    def place_order(self, account_hash, payload):
        return "O1"
    def get_order(self, account_hash, order_id):
        return {"status": "FILLED", "filledQuantity": 1,
                "orderActivityCollection": [{"executionLegs": [{"quantity": 1, "price": 1.55}]}]}
    def replace_order(self, account_hash, order_id, payload):
        return "O2"
    def cancel_order(self, account_hash, order_id):
        pass


def test_logs_dry_run_plan_when_armed():
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contract(), 1.50), _Snap(12.0), _settings(), AS_OF)
    rows = repo.get_live_orders_today(conn, AS_OF)
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "AAL" and r["action"] == "SELL_TO_OPEN"
    assert r["approved"] == 1 and r["dry_run"] == 1 and r["sent"] == 0
    # Strike $11 < $30 → 4 contratos por la regla de strikes baratos (usuario 2026-08-17).
    assert r["final_contracts"] == 4
    assert r["payload_json"] is not None


# ------------------- cantidad según el strike (usuario 2026-08-17) -------------------

def test_a_cheap_strike_asks_for_four_contracts_and_an_expensive_one_stays_at_one():
    """"Cuando el strike es menos de $30 debe abrir más cantidad, mínimo 4" (usuario 2026-08-17).
    Un put de strike $13 traba ~$88 de colateral por contrato y uno de $285 ~$2.500: a 1 contrato la
    operación barata era 28 veces más chica. La regla toca SOLO a los baratos — los caros siguen en 1."""
    lt = _settings().live_trading
    assert live_engine.contracts_for_strike(13.0, lt) == 4, "strike barato → 4"
    assert live_engine.contracts_for_strike(29.99, lt) == 4, "justo por debajo del umbral → 4"
    assert live_engine.contracts_for_strike(30.0, lt) == 1, "el umbral NO entra: $30 exacto ya es caro"
    assert live_engine.contracts_for_strike(285.0, lt) == 1, "el caro no cambia"
    assert live_engine.contracts_for_strike(None, lt) == 1, "sin strike, el base de siempre"


def test_the_cheap_strike_rule_is_off_until_it_is_configured():
    """Nace apagada: sin umbral o sin cantidad configurados devuelve el base y no cambia el comportamiento."""
    base = _settings().live_trading
    assert live_engine.contracts_for_strike(13.0, base.model_copy(update={"cheap_strike_max": 0.0})) == 1
    assert live_engine.contracts_for_strike(13.0, base.model_copy(update={"cheap_strike_contracts": 0})) == 1


def test_the_guard_still_wins_over_the_cheap_strike_rule():
    """Decisión del usuario 2026-08-17: "el guardián manda". La escala es lo que se PIDE; si no entra
    por el techo de contratos, el guardián RECORTA — la regla nunca puede saltear un freno."""
    s = _settings()
    lt = s.live_trading.model_copy(update={"max_contracts_per_order": 2})
    s = s.model_copy(update={"live_trading": lt})
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contract(), 1.50), _Snap(12.0), s, AS_OF)
    r = repo.get_live_orders_today(conn, AS_OF)[0]
    assert r["final_contracts"] == 2, "pidió 4 pero el techo del guardián es 2"
    assert "Recortado" in (r["reasons"] or "")


def test_skips_when_not_armed():
    conn = db.connect(":memory:")
    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contract(), 1.50), _Snap(12.0), _settings(), AS_OF)
    assert repo.get_live_orders_today(conn, AS_OF) == []


def test_skips_symbol_not_in_whitelist():
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    # "ZZZZ" no es un ticker real → nunca está en la whitelist, pase lo que pase con la config.
    live_engine.maybe_log_live_order(conn, "ZZZZ", _Result(_contract(), 1.50), _Snap(12.0), _settings(), AS_OF)
    assert repo.get_live_orders_today(conn, AS_OF) == []


def test_daily_cap_rejects_second_open():
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    s = _settings(enabled=True, dry_run=False, max_orders_per_day=1)  # cupo 1: la 2ª se rechaza
    b = _FakeBroker()
    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contract(), 1.50), _Snap(12.0), s, AS_OF, broker=b)
    live_engine.maybe_log_live_order(conn, "NU", _Result(_contract(), 1.50), _Snap(12.0), s, AS_OF, broker=b)
    rows = repo.get_live_orders_today(conn, AS_OF)
    # 1 orden/día en Fase 1: la primera aprobada (y enviada), la segunda rechazada por el guardián.
    approved = [r for r in rows if r["approved"] == 1]
    rejected = [r for r in rows if r["approved"] == 0]
    assert len(approved) == 1 and len(rejected) == 1
    assert repo.count_live_approved_opens_today(conn, AS_OF) == 1


def test_dry_run_opens_do_not_consume_real_daily_cap():
    """Regresión (usuario 2026-08-10): las órdenes dry-run del día NO deben consumir el cupo real. Antes,
    una aprobada dry-run bloqueaba la primera orden real del día."""
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    # Una apertura dry-run aprobada hoy.
    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contract(), 1.50), _Snap(12.0),
                                     _settings(enabled=False, dry_run=True), AS_OF)
    assert repo.get_live_orders_today(conn, AS_OF)[0]["dry_run"] == 1
    # El cupo REAL sigue en 0 → una orden real todavía puede entrar.
    assert repo.count_live_approved_opens_today(conn, AS_OF) == 0


def test_real_order_is_sent_and_counts():
    """En real (enabled + sin dry-run) con broker, la orden se ENVÍA, se marca sent=1 y consume el cupo."""
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contract(), 1.50), _Snap(12.0),
                                     _settings(enabled=True, dry_run=False), AS_OF, broker=_FakeBroker())
    rows = repo.get_live_orders_today(conn, AS_OF)
    assert len(rows) == 1
    r = rows[0]
    assert r["approved"] == 1 and r["dry_run"] == 0 and r["sent"] == 1
    assert r["order_status"] == "FILLED" and r["schwab_order_id"] == "O1"
    assert repo.count_live_approved_opens_today(conn, AS_OF) == 1


class _FakeBrokerReject:
    """Schwab rechaza la orden (ej. cuenta equivocada sin fondos, caso real 2026-08-10)."""
    def resolve_account_hash(self, account_number=None):
        return "HASH"
    def place_order(self, account_hash, payload):
        return "O1"
    def get_order(self, account_hash, order_id):
        return {"status": "REJECTED"}
    def replace_order(self, account_hash, order_id, payload):
        return "O2"
    def cancel_order(self, account_hash, order_id):
        pass


def test_rejected_real_order_does_not_consume_cap():
    """Regresión (usuario 2026-08-10): una orden REAL rechazada por el broker NO gasta el cupo del día —
    el robot debe poder reintentar hasta abrir 1 posición real."""
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contract(), 1.50), _Snap(12.0),
                                     _settings(enabled=True, dry_run=False), AS_OF, broker=_FakeBrokerReject())
    r = repo.get_live_orders_today(conn, AS_OF)[0]
    assert r["sent"] == 1 and r["order_status"] == "REJECTED"
    # El cupo sigue LIBRE → el próximo escaneo puede reintentar.
    assert repo.count_live_approved_opens_today(conn, AS_OF) == 0


def test_skips_second_order_same_symbol_when_already_committed():
    """Con una posición/orden real viva de un símbolo hoy, NO abre otra del MISMO símbolo (usuario
    2026-08-10: 'una operación más' = otra distinta). Deja el cupo para una oportunidad diferente."""
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    s = _settings(enabled=True, dry_run=False)
    b = _FakeBroker()
    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contract(), 1.50), _Snap(12.0), s, AS_OF, broker=b)
    # segundo intento del MISMO símbolo → se salta (no crea otra fila)
    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contract(), 1.50), _Snap(12.0), s, AS_OF, broker=b)
    rows = [r for r in repo.get_live_orders_today(conn, AS_OF) if r["symbol"] == "AAL"]
    assert len(rows) == 1


def test_daily_override_resets_next_day():
    """El override 'abrir más' vale SOLO para el día en que se seteó (usuario 2026-08-10, punto 2/5):
    al día siguiente el robot vuelve al tope de config, no arrastra el bump de ayer."""
    from datetime import timedelta
    conn = db.connect(":memory:")
    repo.set_max_live_orders_per_day(conn, 5, AS_OF)
    assert repo.get_max_live_orders_per_day(conn, 2, AS_OF) == 5          # hoy: manda el override
    assert repo.get_max_live_orders_per_day(conn, 2, AS_OF + timedelta(days=1)) == 2  # mañana: vuelve al default


def test_dashboard_override_raises_daily_cap():
    """El botón de Real Market (override en la base) sube el cupo diario sin reiniscar (usuario 2026-08-10):
    con config=1 pero override=2, entran dos símbolos distintos."""
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    repo.set_max_live_orders_per_day(conn, 2, AS_OF)
    s = _settings(enabled=True, dry_run=False, max_orders_per_day=1)  # config dice 1; el override manda
    b = _FakeBroker()
    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contract(), 1.50), _Snap(12.0), s, AS_OF, broker=b)
    live_engine.maybe_log_live_order(conn, "NU", _Result(_contract(), 1.50), _Snap(12.0), s, AS_OF, broker=b)
    approved = [r for r in repo.get_live_orders_today(conn, AS_OF) if r["approved"] == 1]
    assert len(approved) == 2


def _close_fake_broker(*, put_mid, underlying_last, strike, expiration, has_position=True):
    from options_advisor.broker.models import AccountPosition, Greeks, OptionChain, OptionContract, Quote

    put = OptionContract(
        symbol="C", option_type="put", strike=strike, expiration=expiration,
        bid=round(put_mid - 0.02, 2), ask=round(put_mid + 0.02, 2), last_price=put_mid,
        implied_volatility=0.4, open_interest=1000, volume=500,
        greeks=Greeks(delta=-0.15, gamma=0.01, theta=-0.5, vega=0.1, rho=0.01, source="broker"))
    chain = OptionChain(symbol="C", as_of=AS_OF, underlying_price=underlying_last, contracts=[put])
    quote = Quote(symbol="C", as_of=AS_OF, last_price=underlying_last, bid=underlying_last, ask=underlying_last)
    poss = []
    if has_position:
        poss = [AccountPosition(
            account_number="74257810", symbol="C   260918P00125000", asset_type="OPTION", quantity=-1,
            average_price=1.50, market_value=-put_mid * 100, unrealized_pnl=0.0,
            option_type="put", strike=strike, expiration=expiration, underlying_symbol="C")]

    class B:
        def resolve_account_hash(self, account_number=None):
            return "HASH"
        def get_all_positions(self):
            return poss
        def get_quote(self, s):
            return quote
        def get_option_chain(self, s, expiration_range_days=(7, 60)):
            return chain
        def place_order(self, h, p):
            return "CO1"
        def get_order(self, h, oid):
            return {"status": "FILLED", "filledQuantity": 1,
                    "orderActivityCollection": [{"executionLegs": [{"quantity": 1, "price": put_mid}]}]}
        def replace_order(self, h, oid, p):
            return "CO2"
        def cancel_order(self, h, oid):
            pass
    return B()


def _insert_filled_open(conn, *, symbol="C", strike=125.0, credit=1.50, expiration="2026-09-18"):
    from datetime import datetime as _dt
    oid = repo.insert_live_order_log(
        conn, log_date=AS_OF, log_ts=_dt(2026, 8, 10, 13, 16), symbol=symbol, action="SELL_TO_OPEN",
        strike=strike, expiration=expiration, approved=True, final_contracts=1, start_limit_price=credit,
        collateral=800.0, dry_run=False, sent=False, reasons=None, payload_json=None, ladder_json=None,
        bid=credit - 0.02, ask=credit + 0.02)
    repo.mark_live_order_sent(conn, oid, schwab_order_id="O1", order_status="FILLED", fill_price=credit,
                              filled_contracts=1, final_limit_price=credit, replacements=0, sent_ts=_dt(2026, 8, 10, 13, 16))
    return oid


def test_close_real_position_on_profit_target(monkeypatch):
    import options_advisor.scheduler.market_calendar as _mc
    monkeypatch.setattr(_mc, "market_session", lambda *a, **k: "abierto")
    conn = db.connect(":memory:")
    oid = _insert_filled_open(conn, credit=1.50)
    # El put ahora vale 0.30 → +80% de la prima → dispara profit_target → recompra y cierra.
    b = _close_fake_broker(put_mid=0.30, underlying_last=140.0, strike=125.0, expiration=date(2026, 9, 18))
    live_engine.close_real_positions(conn, b, _settings(enabled=True, dry_run=False), AS_OF)
    row = [r for r in repo.get_live_orders_today(conn, AS_OF) if r["id"] == oid][0]
    assert row["closed"] == 1
    assert row["close_reason"] == "profit_target"
    assert row["realized_pnl"] == pytest.approx((1.50 - 0.30) * 100)   # +$120
    assert repo.get_open_real_put_positions(conn) == []                 # ya no cuenta como abierta


def test_does_not_close_when_no_trigger(monkeypatch):
    import options_advisor.scheduler.market_calendar as _mc
    monkeypatch.setattr(_mc, "market_session", lambda *a, **k: "abierto")
    conn = db.connect(":memory:")
    oid = _insert_filled_open(conn, credit=1.50)
    # El put sigue valiendo ~1.50 (sin ganancia) → NO cierra.
    b = _close_fake_broker(put_mid=1.50, underlying_last=140.0, strike=125.0, expiration=date(2026, 9, 18))
    live_engine.close_real_positions(conn, b, _settings(enabled=True, dry_run=False), AS_OF)
    row = [r for r in repo.get_live_orders_today(conn, AS_OF) if r["id"] == oid][0]
    assert not row["closed"]


def test_reconciles_position_closed_in_broker(monkeypatch):
    import options_advisor.scheduler.market_calendar as _mc
    monkeypatch.setattr(_mc, "market_session", lambda *a, **k: "abierto")
    conn = db.connect(":memory:")
    oid = _insert_filled_open(conn, credit=1.50)
    # Schwab ya NO tiene la posición (cerrada/asignada afuera) → se marca cerrada sin recomprar.
    b = _close_fake_broker(put_mid=0.30, underlying_last=140.0, strike=125.0, expiration=date(2026, 9, 18),
                           has_position=False)
    live_engine.close_real_positions(conn, b, _settings(enabled=True, dry_run=False), AS_OF)
    row = [r for r in repo.get_live_orders_today(conn, AS_OF) if r["id"] == oid][0]
    assert row["closed"] == 1 and row["close_reason"] == "closed_in_broker"


def test_no_cierra_cuando_la_lectura_al_broker_viene_incompleta(monkeypatch):
    """Regresion del 2026-08-21: una lectura FALLIDA no puede pasar por "ya no esta en el broker".

    Ese dia la API de Schwab fallo, `get_all_positions()` devolvio una lista vacia (es tolerante a
    fallos, loguea y sigue) y la reconciliacion marco `closed_in_broker` 5 posiciones REALES que
    seguian abiertas. Ademas de descuadrar el registro, el robot dejo de vigilarlas: no las cerraba
    por ganancia ni contaban para los topes.

    Ahora la reconciliacion pide `get_all_positions_strict()`, que LANZA si alguna cuenta no
    respondio. Ante esa excepcion no se cierra NADA por reconciliacion."""
    import options_advisor.scheduler.market_calendar as _mc
    monkeypatch.setattr(_mc, "market_session", lambda *a, **k: "abierto")
    conn = db.connect(":memory:")
    oid = _insert_filled_open(conn, credit=1.50)

    # Mid alto = sin ganancia, para que un cierre solo pueda venir de la reconciliacion.
    b = _close_fake_broker(put_mid=1.50, underlying_last=140.0, strike=125.0,
                           expiration=date(2026, 9, 18), has_position=False)

    def _lectura_rota():
        raise RuntimeError("Lectura de posiciones incompleta: cuenta 74257810 no respondio")
    b.get_all_positions_strict = _lectura_rota

    live_engine.close_real_positions(conn, b, _settings(enabled=True, dry_run=False), AS_OF)
    row = [r for r in repo.get_live_orders_today(conn, AS_OF) if r["id"] == oid][0]
    assert not row["closed"], "una lectura fallida NUNCA debe cerrar una posicion real"


def test_si_cierra_cuando_la_lectura_es_confiable_y_la_posicion_no_esta(monkeypatch):
    """La contracara: con lectura ESTRICTA que responde bien y sin la posicion, si se reconcilia.

    Es lo que evita quedar recomprando posiciones fantasma cuando cerraste algo a mano en el broker
    (el 2026-08-20 el condor real quedo reintentando el cierre cada 10s y Schwab lo rechazaba)."""
    import options_advisor.scheduler.market_calendar as _mc
    monkeypatch.setattr(_mc, "market_session", lambda *a, **k: "abierto")
    conn = db.connect(":memory:")
    oid = _insert_filled_open(conn, credit=1.50)
    b = _close_fake_broker(put_mid=0.30, underlying_last=140.0, strike=125.0,
                           expiration=date(2026, 9, 18), has_position=False)
    b.get_all_positions_strict = lambda: []      # lectura completa y de fiar: no hay nada
    live_engine.close_real_positions(conn, b, _settings(enabled=True, dry_run=False), AS_OF)
    row = [r for r in repo.get_live_orders_today(conn, AS_OF) if r["id"] == oid][0]
    assert row["closed"] == 1 and row["close_reason"] == "closed_in_broker"


def _reprice_fake_broker(*, status, chain_mid, strike, expiration):
    from options_advisor.broker.models import Greeks, OptionChain, OptionContract

    put = OptionContract(
        symbol="NVDA", option_type="put", strike=strike, expiration=expiration,
        bid=round(chain_mid - 0.02, 2), ask=round(chain_mid + 0.02, 2), last_price=chain_mid,
        implied_volatility=0.4, open_interest=1000, volume=500,
        greeks=Greeks(delta=-0.2, gamma=0.01, theta=-0.5, vega=0.1, rho=0.01, source="broker"))
    chain = OptionChain(symbol="NVDA", as_of=AS_OF, underlying_price=170.0, contracts=[put])

    class B:
        def resolve_account_hash(self, account_number=None):
            return "HASH"
        def get_order(self, h, oid):
            # `filledQuantity` es lo REALMENTE llenado: 0 mientras la orden sigue viva sin llenar.
            # El stub devolvia 1 siempre, incluso en WORKING — imposible en Schwab, y tapaba el bug
            # de llenados parciales corregido el 2026-08-22.
            info = {"status": status, "filledQuantity": 1 if status == "FILLED" else 0}
            if status == "FILLED":
                info["orderActivityCollection"] = [{"executionLegs": [{"quantity": 1, "price": chain_mid}]}]
            return info
        def get_option_chain(self, s, expiration_range_days=(7, 60)):
            return chain
        def replace_order(self, h, oid, payload):
            return "NEWID"
        def place_order(self, h, p):
            return "X"
        def cancel_order(self, h, o):
            pass
    return B()


def _insert_resting_order(conn, *, limit=0.69, strike=170.0, expiration="2026-09-18"):
    from datetime import datetime as _dt
    oid = repo.insert_live_order_log(
        conn, log_date=AS_OF, log_ts=_dt(2026, 8, 10, 14, 52), symbol="NVDA", action="SELL_TO_OPEN",
        strike=strike, expiration=expiration, approved=True, final_contracts=1, start_limit_price=limit,
        collateral=3400.0, dry_run=False, sent=False, reasons=None, payload_json=None, ladder_json=None,
        bid=limit - 0.01, ask=limit + 0.01)
    repo.mark_live_order_sent(conn, oid, schwab_order_id="OLD", order_status="WORKING", fill_price=None,
                              filled_contracts=0, final_limit_price=limit, replacements=0, sent_ts=_dt(2026, 8, 10, 14, 52))
    return oid


def test_reprices_resting_order_to_current_mid(monkeypatch):
    """La orden puesta al mid que no llenó se RE-PRECIA al mid actual (usuario 2026-08-10: 'a un centavo y
    no negocia')."""
    import options_advisor.scheduler.market_calendar as _mc
    monkeypatch.setattr(_mc, "market_session", lambda *a, **k: "abierto")
    conn = db.connect(":memory:")
    oid = _insert_resting_order(conn, limit=0.69)
    b = _reprice_fake_broker(status="WORKING", chain_mid=0.68, strike=170.0, expiration=date(2026, 9, 18))
    live_engine.reprice_resting_orders(conn, b, _settings(enabled=True, dry_run=False), AS_OF)
    row = [r for r in repo.get_live_orders_today(conn, AS_OF) if r["id"] == oid][0]
    assert abs(row["final_limit_price"] - 0.68) < 0.001   # re-preciada al mid actual
    assert row["schwab_order_id"] == "NEWID"
    assert not row["closed"]


def test_resting_order_fill_is_detected(monkeypatch):
    import options_advisor.scheduler.market_calendar as _mc
    monkeypatch.setattr(_mc, "market_session", lambda *a, **k: "abierto")
    conn = db.connect(":memory:")
    oid = _insert_resting_order(conn, limit=0.69)
    b = _reprice_fake_broker(status="FILLED", chain_mid=0.69, strike=170.0, expiration=date(2026, 9, 18))
    live_engine.reprice_resting_orders(conn, b, _settings(enabled=True, dry_run=False), AS_OF)
    row = [r for r in repo.get_live_orders_today(conn, AS_OF) if r["id"] == oid][0]
    assert row["order_status"] == "FILLED"
    assert abs((row["fill_price"] or 0) - 0.69) < 0.001


def test_kill_switch_rejects():
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    repo.set_live_kill_switch(conn, True)
    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contract(), 1.50), _Snap(12.0), _settings(), AS_OF)
    rows = repo.get_live_orders_today(conn, AS_OF)
    assert len(rows) == 1 and rows[0]["approved"] == 0


def test_pending_open_emails_idempotent(monkeypatch):
    """1 email por fill, y no lo re-manda (usuario 2026-08-11: 'no me llegó el email de NU')."""
    from datetime import datetime
    from options_advisor.execution import live_engine as _le
    from options_advisor.execution import live_guard as _lg
    sent = []
    monkeypatch.setattr("options_advisor.alerts.notifier.send_email", lambda subj, body: sent.append(subj) or True)
    conn = db.connect(":memory:")
    lid = repo.insert_live_order_log(conn, log_date=AS_OF, log_ts=datetime.now(), symbol="NU",
        action=_lg.ACTION_OPEN, strike=12.0, expiration="2026-09-25", approved=True, final_contracts=10,
        start_limit_price=0.21, collateral=12000.0, dry_run=False, sent=True,
        reasons=None, payload_json=None, ladder_json=None)
    repo.mark_live_order_sent(conn, lid, schwab_order_id="O", order_status="FILLED", fill_price=0.21,
        filled_contracts=10, final_limit_price=0.21, replacements=0, sent_ts=datetime.now())
    s = _settings(enabled=True, dry_run=False)
    _le.send_pending_open_emails(conn, s)
    assert len(sent) == 1 and "NU" in sent[0]           # mandó 1
    _le.send_pending_open_emails(conn, s)
    assert len(sent) == 1                                # no lo re-manda


# --------------------------------------------------------------------------------------------
# Escalera de DIVERSIFICACION (usuario 2026-08-21)
# --------------------------------------------------------------------------------------------
# "Si el lunes le dio entrada AAL y vendio -4 put, que el miercoles no agregue 4 mas, sino 2 o 1
# o ninguna." Antes el robot no miraba lo que ya tenia: AAL termino con 9 contratos en 3 entradas
# (17/08, 18/08 y 20/08), todos expuestos al mismo movimiento.

def test_escalera_de_diversificacion_va_a_la_mitad_cada_vez():
    f = live_engine.contracts_after_diversification
    # Empresa barata: la regla por precio/strike le da 4 de base.
    assert f(4, 0) == 4, "1ra entrada: el tamano completo que decide la regla por precio"
    assert f(4, 1) == 2, "2da: la mitad"
    assert f(4, 2) == 1, "3ra: uno solo"
    assert f(4, 3) == 0, "4ta: no entra"
    assert f(4, 9) == 0
    # El caso real de AAL: 4 -> 2 -> 1 -> nada = 7 contratos en vez de 9.
    assert f(4, 0) + f(4, 1) + f(4, 2) + f(4, 3) == 7


def test_escalera_no_deja_pedir_cero_por_redondeo():
    """Empresa cara (base 1): la mitad de 1 redondea a 0, pero no queremos frenar la 2da entrada
    por un redondeo — para eso esta el corte explicito de la 4ta."""
    f = live_engine.contracts_after_diversification
    assert f(1, 0) == 1
    assert f(1, 1) == 1
    assert f(1, 2) == 1
    assert f(1, 3) == 0


def test_escalera_respeta_la_regla_por_precio_de_la_empresa():
    """La escalera NO reemplaza el tamano por precio (usuario 2026-08-07/08-17): solo lo achica en
    las repeticiones. La primera entrada siempre entra con lo que diga esa regla."""
    f = live_engine.contracts_after_diversification
    for base in (1, 3, 4):
        assert f(base, 0) == base


def test_cuenta_entradas_vivas_por_simbolo_ignora_cerradas_y_rechazadas():
    conn = db.connect(":memory:")
    _insert_filled_open(conn, symbol="AAL", strike=13.0, credit=0.22)
    _insert_filled_open(conn, symbol="AAL", strike=12.0, credit=0.35)
    _insert_filled_open(conn, symbol="NVDA", strike=170.0, credit=1.17)
    assert repo.count_open_real_entries_for_symbol(conn, "AAL") == 2, "dos entradas vivas de AAL"
    assert repo.count_open_real_entries_for_symbol(conn, "NVDA") == 1
    assert repo.count_open_real_entries_for_symbol(conn, "AMZN") == 0
    assert repo.count_open_real_entries_for_symbol(conn, " aal ") == 2, "no distingue mayusculas ni espacios"
