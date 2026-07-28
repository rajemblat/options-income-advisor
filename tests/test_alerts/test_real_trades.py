from __future__ import annotations

import json
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


def test_detect_and_alert_real_trades_uses_real_fill_price_not_mark(conn, monkeypatch):
    """Bug real reportado 2026-07-28 (posición real de HOOD): la prima/breakeven/riesgo máximo
    se calculaban con el mark price ACTUAL de la cadena en vez del fill real de la operación ya
    ejecutada (`average_price` que Schwab sí reporta por posición). Acá el fill real (6.0)
    difiere a propósito del mid del contrato (5.5, de bid=5.4/ask=5.6) para que el test falle
    si algo vuelve a usar el mark en vez del fill."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    position = _short_put_position(quantity=-1.0, average_price=6.0)
    chain = OptionChain(symbol="TSLA", as_of=TODAY, underlying_price=330.0, contracts=[_tsla_put_contract()])
    quote = Quote(symbol="TSLA", as_of=TODAY, last_price=330.0, bid=329.9, ask=330.1)
    broker = _FakeBroker(positions=[position], chain=chain, quote=quote)

    real_trades.detect_and_alert_real_trades(
        broker, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )

    rows = repo.get_real_trade_alerts(conn)
    assert len(rows) == 1
    # 6.0 x 100 x 1 = $600 (no 5.5 x 100 = $550 con el mid del contrato)
    assert rows[0]["net_premium"] == pytest.approx(600.0, abs=0.01)
    # breakeven: 320 - 6.0 = 314.0 (no 314.5 con el mid)
    breakevens = json.loads(rows[0]["breakevens_json"])
    assert breakevens == pytest.approx([314.0], abs=0.01)


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


# --- Alcance de Fase 1: rolls NO deben generar alerta (aclarado por el usuario 2026-07-28) ---


def _sofi_aug_put(quantity: float = -2.0, account_number: str = "123") -> AccountPosition:
    return AccountPosition(
        account_number=account_number, symbol="SOFI  260821P00021000", asset_type="OPTION",
        quantity=quantity, average_price=0.30, market_value=quantity * 30.0, unrealized_pnl=0.0,
        underlying_symbol="SOFI", option_type="put", strike=21.0, expiration=date(2026, 8, 21),
    )


def _sofi_sep_put(quantity: float = -2.0, account_number: str = "123") -> AccountPosition:
    return AccountPosition(
        account_number=account_number, symbol="SOFI  260918P00021000", asset_type="OPTION",
        quantity=quantity, average_price=0.45, market_value=quantity * 45.0, unrealized_pnl=0.0,
        underlying_symbol="SOFI", option_type="put", strike=21.0, expiration=date(2026, 9, 18),
    )


def _sofi_chain() -> OptionChain:
    contract = OptionContract(
        symbol="SOFI  260918P00021000", option_type="put", strike=21.0, expiration=date(2026, 9, 18),
        bid=0.44, ask=0.46, last_price=0.45, implied_volatility=0.55, open_interest=500, volume=50, greeks=_greeks(),
    )
    return OptionChain(symbol="SOFI", as_of=TODAY, underlying_price=17.0, contracts=[contract])


def test_detect_and_alert_real_trades_suppresses_roll_same_underlying_same_cycle(conn, monkeypatch):
    """El caso real que motivó esta aclaración de alcance: cerrar SOFI Aug21 $21P y abrir SOFI
    Sep18 $21P en la MISMA corrida (un roll) no debe generar ninguna alerta."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    chain = _sofi_chain()
    quote = Quote(symbol="SOFI", as_of=TODAY, last_price=17.0, bid=16.9, ask=17.1)

    # Corrida 1: posición vieja (SOFI Aug21) ya establecida en el snapshot.
    broker_before = _FakeBroker(positions=[_sofi_aug_put()], chain=chain, quote=quote)
    real_trades.detect_and_alert_real_trades(broker_before, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)

    # Corrida 2 (el roll): SOFI Aug21 desapareció (cerrada), SOFI Sep18 es nueva.
    broker_roll = _FakeBroker(positions=[_sofi_sep_put()], chain=chain, quote=quote)
    generated = real_trades.detect_and_alert_real_trades(
        broker_roll, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )

    assert generated == []
    assert repo.get_real_trade_alerts(conn) == []
    # El snapshot SÍ se actualiza al estado actual (para no perder de vista la posición nueva
    # en corridas futuras), aunque no se haya alertado.
    assert repo.get_position_snapshots(conn) == {("123", "SOFI  260918P00021000"): -2.0}


def test_detect_and_alert_real_trades_does_not_suppress_new_open_on_different_underlying(conn, monkeypatch):
    """Cerrar SOFI y abrir TSLA en la misma corrida (subyacentes distintos) NO es un roll —
    TSLA debe alertarse normalmente."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    sofi_chain = _sofi_chain()
    tsla_chain = OptionChain(symbol="TSLA", as_of=TODAY, underlying_price=330.0, contracts=[_tsla_put_contract()])
    sofi_quote = Quote(symbol="SOFI", as_of=TODAY, last_price=17.0, bid=16.9, ask=17.1)
    tsla_quote = Quote(symbol="TSLA", as_of=TODAY, last_price=330.0, bid=329.9, ask=330.1)

    class _MultiChainBroker(_FakeBroker):
        def get_quote(self, symbol):
            return sofi_quote if symbol == "SOFI" else tsla_quote

        def get_option_chain(self, symbol, expiration_range_days=(7, 60)):
            return sofi_chain if symbol == "SOFI" else tsla_chain

    broker_before = _MultiChainBroker(positions=[_sofi_aug_put()], chain=sofi_chain, quote=sofi_quote)
    real_trades.detect_and_alert_real_trades(broker_before, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)

    # SOFI cerrada, TSLA (subyacente distinto) abierta — no es un roll.
    broker_after = _MultiChainBroker(positions=[_short_put_position(quantity=-1.0)], chain=tsla_chain, quote=tsla_quote)
    generated = real_trades.detect_and_alert_real_trades(
        broker_after, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )

    assert len(generated) == 1
    assert generated[0]["symbol"] == "TSLA"


def test_detect_and_alert_real_trades_does_not_suppress_across_different_accounts(conn, monkeypatch):
    """Un cierre en la cuenta 123 no debe suprimir una apertura del mismo subyacente en la
    cuenta 456 — son cuentas distintas, no hay roll posible entre ellas."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    chain = _sofi_chain()
    quote = Quote(symbol="SOFI", as_of=TODAY, last_price=17.0, bid=16.9, ask=17.1)

    broker_before = _FakeBroker(positions=[_sofi_aug_put(account_number="123")], chain=chain, quote=quote)
    real_trades.detect_and_alert_real_trades(broker_before, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)

    # Cuenta 123 cierra SOFI Aug21; cuenta 456 (distinta) abre SOFI Sep18 — no es el mismo roll.
    broker_after = _FakeBroker(positions=[_sofi_sep_put(account_number="456")], chain=chain, quote=quote)
    generated = real_trades.detect_and_alert_real_trades(
        broker_after, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )

    assert len(generated) == 1
    assert generated[0]["symbol"] == "SOFI"


def test_detect_and_alert_real_trades_does_not_suppress_when_no_prior_close_this_cycle(conn, monkeypatch):
    """Sin ningún cierre en la corrida actual, una apertura nueva del mismo subyacente que una
    posición YA existente (ej. vender un segundo strike de SOFI) se alerta normalmente — no
    todo lo que comparte subyacente es un roll, solo cuando algo se cerró en la misma corrida."""
    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    chain = _sofi_chain()
    quote = Quote(symbol="SOFI", as_of=TODAY, last_price=17.0, bid=16.9, ask=17.1)

    broker_before = _FakeBroker(positions=[_sofi_aug_put()], chain=chain, quote=quote)
    real_trades.detect_and_alert_real_trades(broker_before, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None)

    # SOFI Aug21 SIGUE abierta (no se cerró) Y se abre SOFI Sep18 nueva — dos posiciones
    # distintas del mismo subyacente coexistiendo, no un roll.
    broker_after = _FakeBroker(positions=[_sofi_aug_put(), _sofi_sep_put()], chain=chain, quote=quote)
    generated = real_trades.detect_and_alert_real_trades(
        broker_after, conn, _settings(), TODAY, share_positions={}, anthropic_api_key=None, finnhub_api_key=None
    )

    assert len(generated) == 1
    assert generated[0]["symbol"] == "SOFI"
