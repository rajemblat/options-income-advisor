"""Tests del Asesor AI (usuario 2026-08-10): preferencias aprendidas (auditable), parseo de la
respuesta del LLM (texto + sugerencia + preferencias), y ejecución de la sugerencia APROBADA por el
MISMO guardián real (START/cupo). Nada se abre sin aprobación del usuario Y del guardián."""
from __future__ import annotations

from datetime import date

from options_advisor.advisor import advisor as advisor_mod
from options_advisor.broker.models import Greeks, OptionChain, OptionContract
from options_advisor.config import load_settings
from options_advisor.execution import live_engine
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 8, 10)
EXP = date(2026, 9, 18)


def _settings(*, enabled=False, dry_run=True, max_orders_per_day=None):
    s = load_settings()
    upd = {"enabled": enabled, "dry_run": dry_run}
    if max_orders_per_day is not None:
        upd["max_orders_per_day"] = max_orders_per_day
    lt = s.live_trading.model_copy(update=upd)
    return s.model_copy(update={"live_trading": lt})


def _contract(strike=11.0):
    return OptionContract(
        symbol="AAL", option_type="put", strike=strike, expiration=EXP,
        bid=1.30, ask=1.70, last_price=1.50, implied_volatility=0.4, open_interest=1000, volume=500,
        greeks=Greeks(delta=-0.25, gamma=0.01, theta=-0.5, vega=0.1, rho=0.01, source="broker"),
    )


class _FakeBroker:
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
    def get_option_chain(self, symbol, expiration_range_days=(7, 60)):
        return OptionChain(symbol=symbol, as_of=AS_OF, underlying_price=12.0, contracts=[_contract()])


# --------------------------- Preferencias (aprendizaje auditable) ---------------------------

def test_preferences_add_list_dedupe_delete():
    conn = db.connect(":memory:")
    pid = repo.add_ai_preference(conn, "no me gusta COIN con VIX alto")
    assert pid > 0
    # dedupe case-insensitive
    assert repo.add_ai_preference(conn, "No Me Gusta COIN con VIX alto") == pid
    prefs = repo.list_ai_preferences(conn)
    assert [p["text"] for p in prefs] == ["no me gusta COIN con VIX alto"]
    repo.delete_ai_preference(conn, pid)
    assert repo.list_ai_preferences(conn) == []


# --------------------------- Parseo de la respuesta del LLM ---------------------------

def test_parse_reply_extracts_suggestion_and_prefs():
    text = (
        "Hoy AAL viene cayendo, me gusta vender el put 11.\n"
        '```json\n{"suggestion": {"symbol": "AAL", "strike": 11, "expiration": "2026-09-18", '
        '"contracts": 1, "target_credit": 1.5, "rationale": "cayó fuerte"}, '
        '"new_preferences": ["priorizá caídas fuertes"]}\n```'
    )
    r = advisor_mod._parse_reply(text, ["AAL", "NVDA"])
    assert "```json" not in r.reply_text and "AAL viene cayendo" in r.reply_text
    assert r.suggestion["symbol"] == "AAL" and r.suggestion["strike"] == 11.0
    assert r.suggestion["expiration"] == "2026-09-18" and r.suggestion["contracts"] == 1
    assert r.new_preferences == ["priorizá caídas fuertes"]


def test_parse_reply_rejects_symbol_outside_whitelist():
    text = ('Idea:\n```json\n{"suggestion": {"symbol": "TSLA", "strike": 200, '
            '"expiration": "2026-09-18", "contracts": 1}, "new_preferences": []}\n```')
    r = advisor_mod._parse_reply(text, ["AAL", "NVDA"])
    assert r.suggestion is None  # TSLA no está en la whitelist → no se propone


def test_ta_of_reads_technicals():
    """El resumen técnico lee RSI, medias, tendencia y el soporte más cercano por debajo del precio."""
    snap = {
        "price": 100.0, "rsi_14": 28.4, "atr_14": 3.21, "iv_rank": 62.0,
        "sma_20": 102.0, "sma_50": 105.0, "sma_200": 110.0, "ma_cross_signal": "bajista",
        "support_levels": "[95.0, 90.0, 80.0]", "resistance_levels": "[108.0, 115.0]",
    }
    ta = advisor_mod._ta_of(snap, 100.0)
    assert ta["rsi_14"] == 28.4
    assert ta["soporte_cercano"] == 95.0            # el más cercano por debajo de 100
    assert ta["resistencia_cercana"] == 108.0
    assert ta["trend"] == "bajista"                 # precio < sma50 < sma200
    assert ta["dist_soporte_pct"] == 5.0            # (100-95)/100


def test_ta_of_empty_snapshot():
    assert advisor_mod._ta_of(None, 100.0) == {}


def test_parse_reply_plain_text_no_block():
    r = advisor_mod._parse_reply("Hoy no hay nada que valga la pena, esperaría.", ["AAL"])
    assert r.suggestion is None and r.new_preferences == []
    assert "esperaría" in r.reply_text


# --------------------------- Ejecución de la sugerencia aprobada ---------------------------

def test_approved_suggestion_skipped_when_not_armed():
    """Sin START del día, la sugerencia aprobada queda esperando (no se manda, no se descarta)."""
    conn = db.connect(":memory:")
    sid = repo.add_ai_suggestion(conn, symbol="AAL", strike=11.0, expiration=EXP.isoformat(),
                                 contracts=1, target_credit=1.5, rationale="x")
    repo.approve_ai_suggestion(conn, sid)
    live_engine.process_approved_ai_orders(conn, _FakeBroker(), _settings(enabled=True, dry_run=False), AS_OF)
    assert repo.get_ai_suggestion(conn, sid)["status"] == "approved"  # sigue esperando
    assert repo.get_live_orders_today(conn, AS_OF) == []


def test_approved_suggestion_sent_when_armed_and_real():
    """Con START y modo real, la sugerencia aprobada se manda por el guardián y llena."""
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    sid = repo.add_ai_suggestion(conn, symbol="AAL", strike=11.0, expiration=EXP.isoformat(),
                                 contracts=1, target_credit=1.5, rationale="x")
    repo.approve_ai_suggestion(conn, sid)
    live_engine.process_approved_ai_orders(conn, _FakeBroker(), _settings(enabled=True, dry_run=False), AS_OF)
    assert repo.get_ai_suggestion(conn, sid)["status"] == "sent"
    rows = repo.get_live_orders_today(conn, AS_OF)
    assert len(rows) == 1 and rows[0]["symbol"] == "AAL" and rows[0]["order_status"] == "FILLED"


def test_approved_suggestion_rejected_symbol_not_whitelisted():
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    sid = repo.add_ai_suggestion(conn, symbol="ZZZZ", strike=11.0, expiration=EXP.isoformat(),
                                 contracts=1, target_credit=1.0, rationale="x")
    repo.approve_ai_suggestion(conn, sid)
    live_engine.process_approved_ai_orders(conn, _FakeBroker(), _settings(enabled=True, dry_run=False), AS_OF)
    assert repo.get_ai_suggestion(conn, sid)["status"] == "rejected"


# --------------------------- Cierre manual por chat (usuario 2026-08-11) ---------------------------

def test_parse_reply_close_requires_matching_open_position():
    """Un cierre solo es válido si matchea una posición realmente abierta."""
    text = ('Cerramos.\n```json\n{"suggestion": {"action": "close", "symbol": "AAL", "strike": 11, '
            '"expiration": "2026-09-18", "contracts": 1}, "new_preferences": []}\n```')
    open_pos = [{"symbol": "AAL", "strike": 11.0, "expiration": "2026-09-18"}]
    assert advisor_mod._parse_reply(text, ["AAL"], open_pos).suggestion["action"] == "close"
    # sin esa posición abierta → se descarta
    assert advisor_mod._parse_reply(text, ["AAL"], []).suggestion is None


def test_chat_order_bypasses_contract_and_daily_limits():
    """Una orden pedida por el chat honra los contratos pedidos y NO la frena el cupo diario (usuario
    2026-08-11: 'que el chat haga lo que le pido, que no mire el límite')."""
    from datetime import datetime
    from options_advisor.execution import live_guard
    conn = db.connect(":memory:")
    repo.arm_live_today(conn, AS_OF)
    repo.set_max_live_orders_per_day(conn, 1, AS_OF)                 # cupo diario = 1
    lid = repo.insert_live_order_log(conn, log_date=AS_OF, log_ts=datetime.now(), symbol="C",
        action=live_guard.ACTION_OPEN, strike=100.0, expiration=EXP.isoformat(), approved=True,
        final_contracts=1, start_limit_price=1.0, collateral=100.0, dry_run=False, sent=True,
        reasons=None, payload_json=None, ladder_json=None)
    repo.mark_live_order_sent(conn, lid, schwab_order_id="X", order_status="FILLED", fill_price=1.0,
        filled_contracts=1, final_limit_price=1.0, replacements=0, sent_ts=datetime.now(), send_error=None)
    # cupo YA lleno (1/1). El chat pide 10 contratos de AAL igual.
    sid = repo.add_ai_suggestion(conn, symbol="AAL", strike=11.0, expiration=EXP.isoformat(),
                                 contracts=10, target_credit=None, rationale="x")
    repo.approve_ai_suggestion(conn, sid)
    live_engine.process_approved_ai_orders(conn, _FakeBroker(), _settings(enabled=True, dry_run=False), AS_OF)
    assert repo.get_ai_suggestion(conn, sid)["status"] == "sent"     # NO la frenó el cupo
    row = [r for r in repo.get_live_orders_today(conn, AS_OF) if r["symbol"] == "AAL"][0]
    assert row["final_contracts"] == 10                              # honró los 10 (no recortó a 1/4)


def test_parse_reply_simulador_close_matches_sim_position():
    """Un cierre del simulador (target=simulador) debe matchear una posición del simulador por kind+symbol+strike."""
    text = ('Cerramos el condor.\n```json\n{"suggestion": {"action": "close", "target": "simulador", '
            '"sim_kind": "condor", "symbol": "$SPX", "strike": 7710, "expiration": "2026-08-11", '
            '"contracts": 1}, "new_preferences": []}\n```')
    sim = [{"kind": "condor", "symbol": "$SPX", "strike": 7710.0, "expiration": "2026-08-11"}]
    r = advisor_mod._parse_reply(text, ["AAL"], [], sim)
    assert r.suggestion["target"] == "simulador" and r.suggestion["sim_kind"] == "condor"
    # sin esa posición en el simulador → se descarta
    assert advisor_mod._parse_reply(text, ["AAL"], [], []).suggestion is None


def _open_real_position(conn):
    """Registra una apertura real LLENADA (para después cerrarla)."""
    from datetime import datetime
    from options_advisor.execution import live_guard
    log_id = repo.insert_live_order_log(
        conn, log_date=AS_OF, log_ts=datetime.now(), symbol="AAL", action=live_guard.ACTION_OPEN,
        strike=11.0, expiration=EXP.isoformat(), approved=True, final_contracts=1,
        start_limit_price=1.55, collateral=1100.0, dry_run=False, sent=True, reasons=None,
        payload_json=None, ladder_json=None, bid=1.3, ask=1.7,
    )
    repo.mark_live_order_sent(conn, log_id, schwab_order_id="O1", order_status="FILLED",
                              fill_price=1.55, filled_contracts=1, final_limit_price=1.55,
                              replacements=0, sent_ts=datetime.now(), send_error=None)
    return log_id


class _FakeBrokerMarketOpen(_FakeBroker):
    """Broker que además reporta la posición corta abierta y una quote (para el cierre)."""
    def get_all_positions(self):
        from options_advisor.broker.models import Position
        return [Position(account_number="74257810", symbol="AAL   260918P00011000", asset_type="OPTION",
                         quantity=-1, average_price=1.55, market_value=-30.0, unrealized_pnl=125.0,
                         underlying_symbol="AAL", option_type="put", strike=11.0, expiration=EXP)]
    def get_quote(self, symbol):
        from options_advisor.broker.models import Quote
        return Quote(symbol=symbol, as_of=AS_OF, last_price=12.0, bid=11.99, ask=12.01, net_change_pct=-1.0)


def test_manual_close_forces_recompra(monkeypatch):
    """Cierre manual aprobado: recompra aunque NO toque la regla, con el mercado abierto."""
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda: "abierto")
    conn = db.connect(":memory:")
    _open_real_position(conn)
    sid = repo.add_ai_suggestion(conn, symbol="AAL", strike=11.0, expiration=EXP.isoformat(),
                                 contracts=1, target_credit=None, rationale="me llevo la prima", action="close")
    repo.approve_ai_suggestion(conn, sid)
    assert len(repo.get_open_real_put_positions(conn)) == 1
    live_engine.process_approved_ai_orders(conn, _FakeBrokerMarketOpen(), _settings(enabled=True, dry_run=False), AS_OF)
    assert repo.get_open_real_put_positions(conn) == []           # quedó cerrada
    assert repo.get_ai_suggestion(conn, sid)["status"] == "sent"


def test_manual_close_waits_when_market_closed(monkeypatch):
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda: "cerrado")
    conn = db.connect(":memory:")
    _open_real_position(conn)
    sid = repo.add_ai_suggestion(conn, symbol="AAL", strike=11.0, expiration=EXP.isoformat(),
                                 contracts=1, target_credit=None, rationale="x", action="close")
    repo.approve_ai_suggestion(conn, sid)
    live_engine.process_approved_ai_orders(conn, _FakeBrokerMarketOpen(), _settings(enabled=True, dry_run=False), AS_OF)
    assert repo.get_ai_suggestion(conn, sid)["status"] == "approved"   # sigue esperando la apertura
    assert len(repo.get_open_real_put_positions(conn)) == 1
