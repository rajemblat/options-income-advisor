from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

from options_advisor.broker.models import FilledOrder, FilledOrderLeg, Greeks, OptionChain, OptionContract, PriceBar, Quote
from options_advisor.config import load_settings
from options_advisor.scheduler import healthcheck
from options_advisor.storage import db
from options_advisor.storage import repository as repo

# 2026-07-29 es miércoles (día de mercado real) — 15:00 UTC = 11:00 ET, bien adentro del
# horario regular (9:30-16:00 ET); 21:00 UTC = 17:00 ET, después del cierre.
MARKET_OPEN_NOW = datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)
MARKET_CLOSED_NOW = datetime(2026, 7, 29, 21, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    return db.connect(":memory:")


class _Recorder:
    def __init__(self):
        self.calls: list[str] = []

    def notify(self, message: str) -> None:
        self.calls.append(message)


class _FakeBroker:
    """Sin órdenes por defecto — alcanza para probar la orquestación (reinicio/notificación)
    sin necesitar una cadena de opciones/cotización completa, salvo en los tests que sí quieren
    ejercitar el catch-up encontrando una operación real."""

    def __init__(self, orders=None, chain=None, quote=None, price_history=None):
        self._orders = orders or []
        self._chain = chain
        self._quote = quote
        self._price_history = price_history or []

    def get_all_share_positions(self) -> dict[str, int]:
        return {}

    def get_recent_filled_orders(self, since: datetime) -> list[FilledOrder]:
        return self._orders

    def get_quote(self, symbol: str) -> Quote:
        return self._quote

    def get_option_chain(self, symbol: str, expiration_range_days=(7, 60)) -> OptionChain:
        return self._chain

    def get_price_history(self, symbol: str, lookback_days: int) -> list[PriceBar]:
        return self._price_history[-lookback_days:]


def _settings():
    return load_settings()


def _log_file(tmp_path, age_minutes: float, now: datetime = MARKET_OPEN_NOW):
    path = tmp_path / "scheduler.err.log"
    path.write_text("algo\n")
    mtime = (now - timedelta(minutes=age_minutes)).timestamp()
    os.utime(path, (mtime, mtime))
    return path


# --- is_stale / catchup_lookback_minutes (funciones puras) ---


def test_is_stale_false_within_threshold():
    now = MARKET_OPEN_NOW
    last = now - timedelta(minutes=5)
    assert healthcheck.is_stale(now, last, poll_interval_minutes=3) is False


def test_is_stale_true_beyond_threshold():
    now = MARKET_OPEN_NOW
    last = now - timedelta(minutes=20)
    assert healthcheck.is_stale(now, last, poll_interval_minutes=3) is True


def test_is_stale_uses_floor_for_small_poll_intervals():
    """Con un intervalo configurado muy chico (1 min), el piso de MIN_STALE_MINUTES evita
    marcar "colgado" por una demora normal de pocos minutos."""
    now = MARKET_OPEN_NOW
    last = now - timedelta(minutes=5)
    assert healthcheck.is_stale(now, last, poll_interval_minutes=1) is False


def test_catchup_lookback_minutes_floor_is_fifteen():
    assert healthcheck.catchup_lookback_minutes(2) == 15


def test_catchup_lookback_minutes_scales_with_gap():
    assert healthcheck.catchup_lookback_minutes(60) == 65


def test_catchup_lookback_minutes_capped_at_max():
    assert healthcheck.catchup_lookback_minutes(10_000) == healthcheck.MAX_CATCHUP_MINUTES


# --- run_healthcheck (orquestación) ---


def test_run_healthcheck_noop_when_market_closed(conn, tmp_path):
    recorder = _Recorder()
    log_path = _log_file(tmp_path, age_minutes=60, now=MARKET_CLOSED_NOW)

    result = healthcheck.run_healthcheck(
        settings=_settings(), broker=_FakeBroker(), conn=conn, now=MARKET_CLOSED_NOW, log_path=log_path,
        anthropic_api_key=None, finnhub_api_key=None,
        get_scheduler_pid=lambda: pytest.fail("no debería ni chequear el pid con el mercado cerrado"),
        restart_scheduler=lambda: pytest.fail("no debería reiniciar con el mercado cerrado"),
        notify=recorder.notify,
    )

    assert result == []
    assert recorder.calls == []


def test_run_healthcheck_noop_when_log_fresh(conn, tmp_path):
    recorder = _Recorder()
    log_path = _log_file(tmp_path, age_minutes=1, now=MARKET_OPEN_NOW)

    result = healthcheck.run_healthcheck(
        settings=_settings(), broker=_FakeBroker(), conn=conn, now=MARKET_OPEN_NOW, log_path=log_path,
        anthropic_api_key=None, finnhub_api_key=None,
        get_scheduler_pid=lambda: 123,
        restart_scheduler=lambda: pytest.fail("no debería reiniciar con log fresco"),
        notify=recorder.notify,
    )

    assert result == []
    assert recorder.calls == []


def test_run_healthcheck_restarts_and_notifies_when_pid_missing(conn, tmp_path):
    recorder = _Recorder()
    log_path = _log_file(tmp_path, age_minutes=1, now=MARKET_OPEN_NOW)  # log fresco, pero el proceso ya no existe
    restarted = []

    result = healthcheck.run_healthcheck(
        settings=_settings(), broker=_FakeBroker(), conn=conn, now=MARKET_OPEN_NOW, log_path=log_path,
        anthropic_api_key=None, finnhub_api_key=None,
        get_scheduler_pid=lambda: None,
        restart_scheduler=lambda: restarted.append(True),
        notify=recorder.notify,
    )

    assert result == []
    assert restarted == [True]
    assert len(recorder.calls) == 1
    assert "no está corriendo" in recorder.calls[0]


def test_run_healthcheck_restarts_and_runs_catchup_when_log_stale(conn, tmp_path):
    """Reproduce el incidente real 2026-07-29: proceso vivo, log mudo hace rato durante horario
    de mercado — debe reiniciar y correr el catch-up (acá sin operaciones nuevas que encontrar,
    ver el test siguiente para el caso con hallazgos)."""
    recorder = _Recorder()
    log_path = _log_file(tmp_path, age_minutes=25, now=MARKET_OPEN_NOW)
    restarted = []

    result = healthcheck.run_healthcheck(
        settings=_settings(), broker=_FakeBroker(orders=[]), conn=conn, now=MARKET_OPEN_NOW, log_path=log_path,
        anthropic_api_key=None, finnhub_api_key=None,
        get_scheduler_pid=lambda: 123,
        restart_scheduler=lambda: restarted.append(True),
        notify=recorder.notify,
    )

    assert result == []
    assert restarted == [True]
    assert len(recorder.calls) == 1
    assert "colgado" in recorder.calls[0]
    assert "25 min" in recorder.calls[0]


def _greeks() -> Greeks:
    return Greeks(delta=-0.25, gamma=0.01, theta=-0.05, vega=0.10, rho=0.02, source="broker")


def test_run_healthcheck_notifies_again_when_catchup_finds_a_missed_trade(conn, tmp_path, monkeypatch):
    from options_advisor.alerts import real_trades

    monkeypatch.setattr(real_trades.finnhub_client, "get_recent_news", lambda *a, **k: [])
    recorder = _Recorder()
    log_path = _log_file(tmp_path, age_minutes=40, now=MARKET_OPEN_NOW)

    order = FilledOrder(
        order_id=999, account_number="74257810", fill_time=datetime(2026, 7, 29, 14, 30, tzinfo=timezone.utc),
        legs=[FilledOrderLeg(occ_symbol="HOOD  260904P00075000", instruction="SELL_TO_OPEN", position_effect="OPENING", quantity=1, price=3.15)],
    )
    contract = OptionContract(
        symbol="HOOD  260904P00075000", option_type="put", strike=75.0, expiration=date(2026, 9, 4),
        bid=2.24, ask=3.50, last_price=2.87, implied_volatility=0.75, open_interest=27, volume=3, greeks=_greeks(),
    )
    chain = OptionChain(symbol="HOOD", as_of=date(2026, 7, 29), underlying_price=88.93, contracts=[contract])
    quote = Quote(symbol="HOOD", as_of=date(2026, 7, 29), last_price=88.93, bid=88.9, ask=89.0)
    broker = _FakeBroker(orders=[order], chain=chain, quote=quote)

    result = healthcheck.run_healthcheck(
        settings=_settings(), broker=broker, conn=conn, now=MARKET_OPEN_NOW, log_path=log_path,
        anthropic_api_key=None, finnhub_api_key=None,
        get_scheduler_pid=lambda: 123,
        restart_scheduler=lambda: None,
        notify=recorder.notify,
    )

    assert len(result) == 1
    assert result[0]["symbol"] == "HOOD"
    assert len(recorder.calls) == 2
    assert "colgado" in recorder.calls[0]
    assert "Catch-up encontró 1" in recorder.calls[1]
    assert "HOOD" in recorder.calls[1]
    assert len(repo.get_real_trade_alerts(conn)) == 1
