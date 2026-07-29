from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from options_advisor.alerts import real_trades
from options_advisor.broker.models import FilledOrder, FilledOrderLeg, Greeks, OptionChain, OptionContract, PriceBar, Quote
from options_advisor.config import load_settings
from options_advisor.storage import db
from options_advisor.storage import repository as repo

TODAY = date(2026, 7, 28)
EXPIRATION = date(2026, 9, 4)


@pytest.fixture
def conn():
    return db.connect(":memory:")


def _settings():
    return load_settings()


def _greeks() -> Greeks:
    return Greeks(delta=-0.25, gamma=0.01, theta=-0.05, vega=0.10, rho=0.02, source="broker")


def _hood_leg(instruction: str = "SELL_TO_OPEN", position_effect: str = "OPENING", quantity: float = 2.0, price: float = 3.15) -> FilledOrderLeg:
    return FilledOrderLeg(
        occ_symbol="HOOD  260904P00075000", instruction=instruction, position_effect=position_effect, quantity=quantity, price=price
    )


def _hood_order(order_id: int = 1007358084142, legs: list[FilledOrderLeg] | None = None) -> FilledOrder:
    return FilledOrder(
        order_id=order_id, account_number="74257810", fill_time=datetime(2026, 7, 28, 14, 7, 26, tzinfo=timezone.utc),
        legs=legs or [_hood_leg()],
    )


def _hood_contract(bid: float = 2.24, ask: float = 3.50) -> OptionContract:
    return OptionContract(
        symbol="HOOD  260904P00075000", option_type="put", strike=75.0, expiration=EXPIRATION,
        bid=bid, ask=ask, last_price=(bid + ask) / 2, implied_volatility=0.75, open_interest=27, volume=3, greeks=_greeks(),
    )


def _hood_chain() -> OptionChain:
    return OptionChain(symbol="HOOD", as_of=TODAY, underlying_price=88.93, contracts=[_hood_contract()])


def _flat_price_history(days: int = 400, price: float = 88.93) -> list[PriceBar]:
    from datetime import timedelta

    start = TODAY - timedelta(days=days)
    return [
        PriceBar(symbol="TST", trade_date=start + timedelta(days=i), open=price, high=price, low=price, close=price, volume=1000)
        for i in range(days)
    ]


class _FakeBroker:
    def __init__(
        self,
        orders: list[FilledOrder],
        chain: OptionChain,
        quote: Quote,
        get_orders_raises: bool = False,
        price_history: list[PriceBar] | None = None,
    ):
        self._orders = orders
        self._chain = chain
        self._quote = quote
        self._get_orders_raises = get_orders_raises
        self._price_history = price_history if price_history is not None else _flat_price_history()

    def get_recent_filled_orders(self, since: datetime) -> list[FilledOrder]:
        if self._get_orders_raises:
            raise RuntimeError("fallo simulado de Schwab")
        return self._orders

    def get_quote(self, symbol: str) -> Quote:
        return self._quote

    def get_option_chain(self, symbol: str, expiration_range_days=(7, 60)) -> OptionChain:
        return self._chain

    def get_price_history(self, symbol: str, lookback_days: int) -> list[PriceBar]:
        return self._price_history[-lookback_days:]


# --- _is_roll ---


def test_is_roll_true_when_order_has_opening_and_closing_leg():
    order = _hood_order(legs=[
        _hood_leg(instruction="SELL_TO_OPEN", position_effect="OPENING"),
        FilledOrderLeg(occ_symbol="SOFI  260821P00021000", instruction="BUY_TO_CLOSE", position_effect="CLOSING", quantity=2.0, price=4.15),
    ])
    assert real_trades._is_roll(order) is True


def test_is_roll_false_for_single_opening_leg():
    assert real_trades._is_roll(_hood_order()) is False


def test_is_roll_false_for_closing_only_order():
    order = _hood_order(legs=[_hood_leg(instruction="BUY_TO_CLOSE", position_effect="CLOSING")])
    assert real_trades._is_roll(order) is False


# --- _resolve_strategy_type (sin cambios de comportamiento) ---


def test_resolve_strategy_type_put_is_always_cash_secured_put():
    assert real_trades._resolve_strategy_type("put", share_positions={}, underlying_symbol="TSLA", contracts=1) == "cash_secured_put"


def test_resolve_strategy_type_call_with_enough_shares_is_covered_call():
    strategy = real_trades._resolve_strategy_type("call", share_positions={"AAPL": 200}, underlying_symbol="AAPL", contracts=2)
    assert strategy == "covered_call"


def test_resolve_strategy_type_call_without_enough_shares_is_naked():
    strategy = real_trades._resolve_strategy_type("call", share_positions={"AAPL": 50}, underlying_symbol="AAPL", contracts=1)
    assert strategy == "short_call_naked"


# --- detect_and_alert_real_trades (integración con broker/chain fake) ---


def test_detect_and_alert_real_trades_generates_alert_for_new_opening_order(conn, monkeypatch):
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    broker = _FakeBroker(orders=[_hood_order()], chain=_hood_chain(), quote=Quote(symbol="HOOD", as_of=TODAY, last_price=88.93, bid=88.9, ask=89.0))

    generated = real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )

    assert len(generated) == 1
    assert generated[0]["symbol"] == "HOOD"
    assert generated[0]["strategy_type"] == "cash_secured_put"
    assert generated[0]["quantity"] == 2

    rows = repo.get_real_trade_alerts(conn)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "HOOD"
    assert rows[0]["strike"] == 75.0
    assert rows[0]["order_id"] == 1007358084142


# --- "check histórico" (pedido 2026-07-28, ver strategy/backtest.py) ---


def test_detect_and_alert_real_trades_persists_historical_move_check_zero_occurrences(conn, monkeypatch):
    """Precio históricamente plano (nunca se movió) — 0 ocurrencias, pero SÍ debe calcular un
    total_windows > 0 (hay historial suficiente, solo que nunca tocó el nivel)."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    broker = _FakeBroker(
        orders=[_hood_order()], chain=_hood_chain(), quote=Quote(symbol="HOOD", as_of=TODAY, last_price=88.93, bid=88.9, ask=89.0),
        price_history=_flat_price_history(price=88.93),
    )
    real_trades.detect_and_alert_real_trades(broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)
    rows = repo.get_real_trade_alerts(conn)
    assert rows[0]["historical_move_occurrences"] == 0
    assert rows[0]["historical_move_total_windows"] > 0


def test_detect_and_alert_real_trades_persists_historical_move_check_with_occurrences(conn, monkeypatch):
    """Un dip real en el historial que sí toca el nivel del strike — occurrences > 0."""
    from datetime import timedelta

    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    bars = _flat_price_history(price=88.93)
    dip_date = TODAY - timedelta(days=200)
    bars = [b if b.trade_date != dip_date else b.model_copy(update={"low": 60.0}) for b in bars]
    broker = _FakeBroker(
        orders=[_hood_order()], chain=_hood_chain(), quote=Quote(symbol="HOOD", as_of=TODAY, last_price=88.93, bid=88.9, ask=89.0),
        price_history=bars,
    )
    real_trades.detect_and_alert_real_trades(broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)
    rows = repo.get_real_trade_alerts(conn)
    assert rows[0]["historical_move_occurrences"] > 0


def test_detect_and_alert_real_trades_uses_exact_order_fill_price_not_mark(conn, monkeypatch):
    """Bug real reportado 2026-07-28 (posición real de HOOD): breakeven/prima/riesgo máximo se
    calculaban con el mark price ACTUAL de la cadena en vez del fill real de la orden. Acá el
    fill real de la orden (3.15) difiere a propósito del mid del contrato (2.87, de
    bid=2.24/ask=3.50) para que el test falle si algo vuelve a usar el mark."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    broker = _FakeBroker(orders=[_hood_order()], chain=_hood_chain(), quote=Quote(symbol="HOOD", as_of=TODAY, last_price=88.93, bid=88.9, ask=89.0))

    real_trades.detect_and_alert_real_trades(broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)

    rows = repo.get_real_trade_alerts(conn)
    # 3.15 x 100 x 2 = $630 (no 2.87 x 100 x 2 = $574 con el mid del contrato)
    assert rows[0]["net_premium"] == pytest.approx(630.0, abs=0.01)
    # breakeven: 75 - 3.15 = 71.85 (no 72.13 con el mid)
    breakevens = json.loads(rows[0]["breakevens_json"])
    assert breakevens == pytest.approx([71.85], abs=0.01)


def test_detect_and_alert_real_trades_dedups_same_order_leg_across_runs(conn, monkeypatch):
    """La ventana de detección se solapa a propósito entre corridas (REAL_TRADE_LOOKBACK_MINUTES
    > cadencia del cron) — ver la misma orden dos veces no debe duplicar la alerta."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    broker = _FakeBroker(orders=[_hood_order()], chain=_hood_chain(), quote=Quote(symbol="HOOD", as_of=TODAY, last_price=88.93, bid=88.9, ask=89.0))

    first = real_trades.detect_and_alert_real_trades(broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)
    second = real_trades.detect_and_alert_real_trades(broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)

    assert len(first) == 1
    assert second == []
    assert len(repo.get_real_trade_alerts(conn)) == 1


def test_detect_and_alert_real_trades_skips_silently_when_another_process_won_the_race(conn, monkeypatch):
    """Incidente real 2026-07-29: el scheduler recién reiniciado y una corrida manual leyeron
    `already_alerted` al mismo tiempo (antes de que cualquiera insertara), y ambos intentaron
    grabar la misma orden — acá se simula forzando que la fila YA exista en la base (otro
    proceso "ganó" la carrera) mientras el chequeo en memoria de esta corrida sigue creyendo que
    es nueva (monkeypatch de `get_alerted_order_leg_keys` a vacío). El índice UNIQUE de la base
    debe rechazar el segundo insert y `detect_and_alert_real_trades` debe devolver `generated`
    vacío en vez de romper o duplicar la notificación."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    broker = _FakeBroker(orders=[_hood_order()], chain=_hood_chain(), quote=Quote(symbol="HOOD", as_of=TODAY, last_price=88.93, bid=88.9, ask=89.0))

    # "Otro proceso" ya insertó esta misma orden/pata.
    real_trades.detect_and_alert_real_trades(broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)
    assert len(repo.get_real_trade_alerts(conn)) == 1

    # Esta corrida no sabe (todavía) que ya se insertó — simula la lectura stale de la carrera.
    monkeypatch.setattr(repo, "get_alerted_order_leg_keys", lambda conn: set())
    generated = real_trades.detect_and_alert_real_trades(broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)

    assert generated == []
    assert len(repo.get_real_trade_alerts(conn)) == 1  # sigue habiendo 1 sola fila, no 2


def test_detect_and_alert_real_trades_ignores_buy_to_open():
    """Comprar opciones (protección, debit spreads) no es una venta de prima — fuera de este
    detector, el resto del motor es un asesor de INGRESO."""
    order = _hood_order(legs=[_hood_leg(instruction="BUY_TO_OPEN", position_effect="OPENING")])
    broker = _FakeBroker(orders=[order], chain=_hood_chain(), quote=Quote(symbol="HOOD", as_of=TODAY, last_price=88.93, bid=88.9, ask=89.0))
    generated = real_trades.detect_and_alert_real_trades(
        broker, db.connect(":memory:"), _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )
    assert generated == []


def test_detect_and_alert_real_trades_no_orders_generates_nothing(conn):
    broker = _FakeBroker(orders=[], chain=_hood_chain(), quote=Quote(symbol="HOOD", as_of=TODAY, last_price=88.93, bid=88.9, ask=89.0))
    generated = real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )
    assert generated == []
    assert repo.get_real_trade_alerts(conn) == []


def test_detect_and_alert_real_trades_never_raises_when_get_orders_fails(conn):
    broker = _FakeBroker(orders=[], chain=_hood_chain(), quote=Quote(symbol="HOOD", as_of=TODAY, last_price=88.93, bid=88.9, ask=89.0), get_orders_raises=True)
    generated = real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )
    assert generated == []


def test_detect_and_alert_real_trades_skips_contract_not_found_in_chain(conn, monkeypatch):
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    empty_chain = OptionChain(symbol="HOOD", as_of=TODAY, underlying_price=88.93, contracts=[])
    broker = _FakeBroker(orders=[_hood_order()], chain=empty_chain, quote=Quote(symbol="HOOD", as_of=TODAY, last_price=88.93, bid=88.9, ask=89.0))

    generated = real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )
    assert generated == []
    assert repo.get_real_trade_alerts(conn) == []


def test_detect_and_alert_real_trades_multiple_opening_legs_generate_separate_alerts(conn, monkeypatch):
    """Cada apertura, su propio registro — pedido explícito 2026-07-28 (no promediar aperturas
    incrementales entre sí). Acá una orden hipotética con 2 patas SELL_TO_OPEN de contratos
    distintos genera 2 alertas independientes."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    order = _hood_order(legs=[
        _hood_leg(quantity=2.0, price=3.15),
        FilledOrderLeg(occ_symbol="HOOD  260904P00070000", instruction="SELL_TO_OPEN", position_effect="OPENING", quantity=1.0, price=2.10),
    ])
    chain = OptionChain(
        symbol="HOOD", as_of=TODAY, underlying_price=88.93,
        contracts=[
            _hood_contract(),
            OptionContract(
                symbol="HOOD  260904P00070000", option_type="put", strike=70.0, expiration=EXPIRATION,
                bid=1.9, ask=2.3, last_price=2.1, implied_volatility=0.7, open_interest=10, volume=1, greeks=_greeks(),
            ),
        ],
    )
    broker = _FakeBroker(orders=[order], chain=chain, quote=Quote(symbol="HOOD", as_of=TODAY, last_price=88.93, bid=88.9, ask=89.0))

    generated = real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )

    assert len(generated) == 2
    rows = repo.get_real_trade_alerts(conn)
    assert len(rows) == 2
    strikes = sorted(r["strike"] for r in rows)
    assert strikes == [70.0, 75.0]


# --- Alcance: rolls NO deben generar alerta (una orden con pata OPENING + CLOSING) ---


def test_detect_and_alert_real_trades_suppresses_roll_same_order(conn, monkeypatch):
    """El caso real que motivó esta aclaración de alcance: un roll de SOFI (Aug21->Sep18 $21P)
    llega como UNA orden con una pata SELL_TO_OPEN + una pata BUY_TO_CLOSE — no debe alertar."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    order = FilledOrder(
        order_id=1007347459242, account_number="74257810", fill_time=datetime(2026, 7, 27, 17, 4, tzinfo=timezone.utc),
        legs=[
            FilledOrderLeg(occ_symbol="SOFI  260918P00021000", instruction="SELL_TO_OPEN", position_effect="OPENING", quantity=2.0, price=4.38),
            FilledOrderLeg(occ_symbol="SOFI  260821P00021000", instruction="BUY_TO_CLOSE", position_effect="CLOSING", quantity=2.0, price=4.15),
        ],
    )
    broker = _FakeBroker(orders=[order], chain=_hood_chain(), quote=Quote(symbol="SOFI", as_of=TODAY, last_price=17.0, bid=16.9, ask=17.1))

    generated = real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )

    assert generated == []
    assert repo.get_real_trade_alerts(conn) == []


def test_detect_and_alert_real_trades_does_not_suppress_separate_close_and_open_orders(conn, monkeypatch):
    """Mejora sobre la heurística anterior: cerrar en UNA orden y abrir en OTRA orden distinta
    (no combinadas) del mismo subyacente ya NO se suprime — solo una orden con ambos efectos
    combinados es un roll. Antes esto era un falso negativo documentado."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    close_order = FilledOrder(
        order_id=1, account_number="74257810", fill_time=datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc),
        legs=[FilledOrderLeg(occ_symbol="SOFI  260821P00021000", instruction="BUY_TO_CLOSE", position_effect="CLOSING", quantity=2.0, price=4.15)],
    )
    open_order = FilledOrder(
        order_id=2, account_number="74257810", fill_time=datetime(2026, 7, 28, 10, 5, tzinfo=timezone.utc),
        legs=[FilledOrderLeg(occ_symbol="SOFI  260918P00021000", instruction="SELL_TO_OPEN", position_effect="OPENING", quantity=2.0, price=4.38)],
    )
    chain = OptionChain(
        symbol="SOFI", as_of=TODAY, underlying_price=17.0,
        contracts=[OptionContract(
            symbol="SOFI  260918P00021000", option_type="put", strike=21.0, expiration=date(2026, 9, 18),
            bid=4.3, ask=4.46, last_price=4.38, implied_volatility=0.55, open_interest=500, volume=50, greeks=_greeks(),
        )],
    )
    broker = _FakeBroker(orders=[close_order, open_order], chain=chain, quote=Quote(symbol="SOFI", as_of=TODAY, last_price=17.0, bid=16.9, ask=17.1))

    generated = real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )

    assert len(generated) == 1
    assert generated[0]["symbol"] == "SOFI"
