from __future__ import annotations

from datetime import date, datetime

import pytest

from options_advisor.alerts import real_trades
from options_advisor.broker.models import AccountPosition, Greeks, OptionChain, OptionContract, Quote
from options_advisor.config import load_settings
from options_advisor.storage import db
from options_advisor.storage import repository as repo
from options_advisor.storage.models import PositionSnapshot

TODAY = date(2026, 7, 27)
EXPIRATION = date(2026, 8, 21)


@pytest.fixture
def conn():
    return db.connect(":memory:")


def _short_put_position(quantity: float = -1.0, account_number: str = "123", average_price: float = 5.5) -> AccountPosition:
    return AccountPosition(
        account_number=account_number,
        symbol="TSLA  260821P00320000",
        asset_type="OPTION",
        quantity=quantity,
        average_price=average_price,
        market_value=quantity * 550.0,
        unrealized_pnl=0.0,
        underlying_symbol="TSLA",
        option_type="put",
        strike=320.0,
        expiration=EXPIRATION,
    )


# --- _detect_new_short_trades ---


def test_detect_new_short_trades_finds_brand_new_short_position():
    position = _short_put_position(quantity=-1.0)
    detected = real_trades._detect_new_short_trades([position], previous={})
    assert detected == [(position, 1)]


def test_detect_new_short_trades_finds_increase_in_existing_short():
    position = _short_put_position(quantity=-3.0)
    previous = {("123", "TSLA  260821P00320000"): -1.0}
    detected = real_trades._detect_new_short_trades([position], previous)
    assert detected == [(position, 2)]  # 2 contratos nuevos: pasó de -1 a -3


def test_detect_new_short_trades_ignores_unchanged_position():
    position = _short_put_position(quantity=-1.0)
    previous = {("123", "TSLA  260821P00320000"): -1.0}
    assert real_trades._detect_new_short_trades([position], previous) == []


def test_detect_new_short_trades_ignores_position_that_shrank():
    """Recomprar (cerrar parcial) una posición corta no es una operación nueva de venta."""
    position = _short_put_position(quantity=-1.0)
    previous = {("123", "TSLA  260821P00320000"): -3.0}
    assert real_trades._detect_new_short_trades([position], previous) == []


def test_detect_new_short_trades_ignores_long_positions():
    long_put = _short_put_position(quantity=1.0)  # comprado, no vendido
    assert real_trades._detect_new_short_trades([long_put], previous={}) == []


def test_detect_new_short_trades_ignores_non_option_positions():
    share_position = AccountPosition(
        account_number="123", symbol="TSLA", asset_type="EQUITY", quantity=100, average_price=300.0, market_value=30000.0, unrealized_pnl=0.0
    )
    assert real_trades._detect_new_short_trades([share_position], previous={}) == []


# --- _resolve_strategy_type ---


def test_resolve_strategy_type_put_is_always_cash_secured_put():
    assert real_trades._resolve_strategy_type("put", share_positions={}, underlying_symbol="TSLA", contracts=1) == "cash_secured_put"


def test_resolve_strategy_type_call_with_enough_shares_is_covered_call():
    strategy = real_trades._resolve_strategy_type("call", share_positions={"AAPL": 200}, underlying_symbol="AAPL", contracts=2)
    assert strategy == "covered_call"


def test_resolve_strategy_type_call_without_enough_shares_is_naked():
    strategy = real_trades._resolve_strategy_type("call", share_positions={"AAPL": 50}, underlying_symbol="AAPL", contracts=1)
    assert strategy == "short_call_naked"


# --- detect_and_alert_real_trades (integración con broker/chain fake) ---


def _greeks() -> Greeks:
    return Greeks(delta=-0.25, gamma=0.01, theta=-0.05, vega=0.10, rho=0.02, source="broker")


def _tsla_put_contract() -> OptionContract:
    return OptionContract(
        symbol="TSLA  260821P00320000",
        option_type="put",
        strike=320.0,
        expiration=EXPIRATION,
        bid=5.4,
        ask=5.6,
        last_price=5.5,
        implied_volatility=0.45,
        open_interest=1000,
        volume=100,
        greeks=_greeks(),
    )


class _FakeBroker:
    def __init__(self, positions: list[AccountPosition], chain: OptionChain, quote: Quote, get_positions_raises: bool = False):
        self._positions = positions
        self._chain = chain
        self._quote = quote
        self._get_positions_raises = get_positions_raises

    def get_all_positions(self) -> list[AccountPosition]:
        if self._get_positions_raises:
            raise RuntimeError("fallo simulado de Schwab")
        return self._positions

    def get_quote(self, symbol: str) -> Quote:
        return self._quote

    def get_option_chain(self, symbol: str, expiration_range_days=(7, 60)) -> OptionChain:
        return self._chain


def _settings():
    return load_settings()


def test_detect_and_alert_real_trades_generates_alert_for_new_short_put(conn, monkeypatch):
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    position = _short_put_position(quantity=-1.0)
    chain = OptionChain(symbol="TSLA", as_of=TODAY, underlying_price=330.0, contracts=[_tsla_put_contract()])
    quote = Quote(symbol="TSLA", as_of=TODAY, last_price=330.0, bid=329.9, ask=330.1)
    broker = _FakeBroker(positions=[position], chain=chain, quote=quote)

    generated = real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )

    assert len(generated) == 1
    assert generated[0]["symbol"] == "TSLA"
    assert generated[0]["strategy_type"] == "cash_secured_put"
    assert generated[0]["quantity"] == 1

    rows = repo.get_real_trade_alerts(conn)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "TSLA"
    assert rows[0]["strike"] == 320.0
    assert rows[0]["max_loss"] is not None


def test_detect_and_alert_real_trades_persists_position_snapshot(conn, monkeypatch):
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    position = _short_put_position(quantity=-1.0)
    chain = OptionChain(symbol="TSLA", as_of=TODAY, underlying_price=330.0, contracts=[_tsla_put_contract()])
    quote = Quote(symbol="TSLA", as_of=TODAY, last_price=330.0, bid=329.9, ask=330.1)
    broker = _FakeBroker(positions=[position], chain=chain, quote=quote)

    real_trades.detect_and_alert_real_trades(broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)

    assert repo.get_position_snapshots(conn) == {("123", "TSLA  260821P00320000"): -1.0}


def test_detect_and_alert_real_trades_no_alert_on_second_run_same_position(conn, monkeypatch):
    """El tick siguiente, sin cambios en la posición, no debe generar una segunda alerta —
    el diseño de reemplazo completo de position_snapshots ya deja registrado el estado actual."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    position = _short_put_position(quantity=-1.0)
    chain = OptionChain(symbol="TSLA", as_of=TODAY, underlying_price=330.0, contracts=[_tsla_put_contract()])
    quote = Quote(symbol="TSLA", as_of=TODAY, last_price=330.0, bid=329.9, ask=330.1)
    broker = _FakeBroker(positions=[position], chain=chain, quote=quote)

    real_trades.detect_and_alert_real_trades(broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)
    second_run = real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )

    assert second_run == []
    assert len(repo.get_real_trade_alerts(conn)) == 1


def test_detect_and_alert_real_trades_no_positions_generates_nothing(conn):
    chain = OptionChain(symbol="TSLA", as_of=TODAY, underlying_price=330.0, contracts=[])
    quote = Quote(symbol="TSLA", as_of=TODAY, last_price=330.0, bid=329.9, ask=330.1)
    broker = _FakeBroker(positions=[], chain=chain, quote=quote)

    generated = real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )
    assert generated == []
    assert repo.get_real_trade_alerts(conn) == []


def test_detect_and_alert_real_trades_never_raises_when_get_all_positions_fails(conn):
    chain = OptionChain(symbol="TSLA", as_of=TODAY, underlying_price=330.0, contracts=[])
    quote = Quote(symbol="TSLA", as_of=TODAY, last_price=330.0, bid=329.9, ask=330.1)
    broker = _FakeBroker(positions=[], chain=chain, quote=quote, get_positions_raises=True)

    generated = real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )
    assert generated == []


def test_detect_and_alert_real_trades_skips_contract_not_found_in_chain(conn, monkeypatch):
    """La cadena en vivo ya no tiene el strike exacto (delisted/vencimiento pasado) — se omite
    la alerta de ESA posición pero no rompe el resto (acá no hay más posiciones, solo confirma
    que no lanza y no persiste nada)."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    position = _short_put_position(quantity=-1.0)
    chain = OptionChain(symbol="TSLA", as_of=TODAY, underlying_price=330.0, contracts=[])  # sin el contrato
    quote = Quote(symbol="TSLA", as_of=TODAY, last_price=330.0, bid=329.9, ask=330.1)
    broker = _FakeBroker(positions=[position], chain=chain, quote=quote)

    generated = real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )
    assert generated == []
    assert repo.get_real_trade_alerts(conn) == []


def test_detect_and_alert_real_trades_reopens_after_full_close(conn, monkeypatch):
    """Posición cerrada del todo (deja de aparecer en get_all_positions) y reabierta más tarde
    con MENOS contratos que antes de cerrarla — debe detectarse como operación nueva, no
    compararse contra el número viejo más grande (motivación de reemplazar el snapshot
    completo en vez de upsert incremental)."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    chain = OptionChain(symbol="TSLA", as_of=TODAY, underlying_price=330.0, contracts=[_tsla_put_contract()])
    quote = Quote(symbol="TSLA", as_of=TODAY, last_price=330.0, bid=329.9, ask=330.1)

    # Corrida 1: -3 contratos
    broker_open = _FakeBroker(positions=[_short_put_position(quantity=-3.0)], chain=chain, quote=quote)
    real_trades.detect_and_alert_real_trades(broker_open, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)

    # Corrida 2: posición cerrada del todo, ya no aparece
    broker_closed = _FakeBroker(positions=[], chain=chain, quote=quote)
    real_trades.detect_and_alert_real_trades(broker_closed, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)
    assert repo.get_position_snapshots(conn) == {}

    # Corrida 3: reabierta con -1 (menos que el -3 original) — debe detectarse como nueva
    broker_reopened = _FakeBroker(positions=[_short_put_position(quantity=-1.0)], chain=chain, quote=quote)
    generated = real_trades.detect_and_alert_real_trades(
        broker_reopened, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )
    assert len(generated) == 1
    assert len(repo.get_real_trade_alerts(conn)) == 2
