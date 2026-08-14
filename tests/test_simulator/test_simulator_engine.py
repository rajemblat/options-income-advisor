from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import Greeks, OptionChain, OptionContract, PriceBar, Quote
from options_advisor.indicators.pipeline import SymbolAnalysis
from options_advisor.config import (
    BrokerSettings,
    ConvictionThresholds,
    DatabaseSettings,
    InvestorProfileSettings,
    IvRankSettings,
    LlmSettings,
    MarketSettings,
    RiskLevelFloatParams,
    RiskLevelSupportSmaParams,
    SchedulerSettings,
    Settings,
    SimulatorSettings,
    StrategySettings,
)
from options_advisor.simulator import engine
from options_advisor.storage import db
from options_advisor.storage import repository as repo
from options_advisor.storage.models import IndicatorSnapshot

AS_OF = date(2026, 3, 2)
UNDERLYING_PRICE = 93.0


class FakeBroker(BrokerClient):
    """Broker mínimo para tests de engine.py — solo implementa get_quote/get_option_chain
    (lo único que usa mark_and_close_positions), el resto no debería llamarse nunca acá."""

    def __init__(self, price: float, chain: OptionChain):
        self._price = price
        self._chain = chain

    def get_quote(self, symbol: str) -> Quote:
        return Quote(symbol=symbol, as_of=AS_OF, last_price=self._price, bid=self._price, ask=self._price)

    def get_quotes(self, symbols):
        raise NotImplementedError

    def get_option_chain(self, symbol: str, expiration_range_days=(7, 60)) -> OptionChain:
        return self._chain

    def get_price_history(self, symbol: str, lookback_days: int):
        raise NotImplementedError

    def get_intraday_bars(self, symbol, session_date, interval_minutes=1):
        raise NotImplementedError

    def is_authenticated(self) -> bool:
        return True

    def get_all_share_positions(self):
        return {}

    def get_all_positions(self):
        return []

    def get_recent_filled_orders(self, since):
        return []

    def get_movers(self, index, sort, frequency=0):
        return []

    def screen_universe(self, symbols, max_shortlist=60):
        return symbols


@pytest.fixture
def conn():
    return db.connect(":memory:")


def _settings() -> Settings:
    return Settings(
        broker=BrokerSettings(mode="mock", fixtures_dir="data/fixtures"),
        database=DatabaseSettings(path="data/app.db"),
        market=MarketSettings(risk_free_rate=0.045),
        llm=LlmSettings(model="claude-haiku-4-5-20251001", max_tokens=300),
        scheduler=SchedulerSettings(
            timezone="America/New_York", poll_interval_minutes=30, real_trade_poll_interval_minutes=3,
            market_open_snapshot_time="09:35", market_close_snapshot_time="15:55",
            market_hours_start="09:30", market_hours_end="16:00", premarket_digest_time="09:15",
        ),
        investor_profile=InvestorProfileSettings(
            capital_available=50_000.0, loss_tolerance_pct=2.0, experience_level="intermedio",
            risk_preference="defined", risk_level="moderado",
        ),
        conviction_thresholds=ConvictionThresholds(conservador=75, moderado=65, agresivo=55),
        iv_rank=IvRankSettings(min_sessions_for_real_iv=20, full_window_sessions=252, hv_window_days=20),
        strategy=StrategySettings(
            enabled=["cash_secured_put"],
            target_short_delta=RiskLevelFloatParams(conservador=0.15, moderado=0.20, agresivo=0.35),
            iv_rank_high_threshold=RiskLevelFloatParams(conservador=60, moderado=50, agresivo=40),
            min_coverage_pct=RiskLevelFloatParams(conservador=0.0, moderado=0.12, agresivo=0.08),
            support_sma_periods=RiskLevelSupportSmaParams(conservador=[8], moderado=[8, 20], agresivo=[8, 20]),
        ),
        simulator=SimulatorSettings(
            enabled=True, initial_capital=100_000.0, max_position_pct=0.10, profit_target_pct=0.30,
            dte_range=(30, 45), max_delta=0.18, rsi_range=(30.0, 40.0), iv_rank_min=50.0, iv_percentile_min=50.0,
            support_max_distance_pct=0.05, weekly_support_max_distance_pct=0.09,
            sma_periods=[8, 20, 50], sma_min_distance_pct=0.03,
        ),
    )


def _snapshot(**overrides) -> IndicatorSnapshot:
    defaults = dict(
        symbol="TST", snapshot_date=AS_OF, snapshot_ts=datetime.combine(AS_OF, datetime.min.time()),
        price=UNDERLYING_PRICE, iv_rank=65.0, iv_rank_source="implied_volatility", rsi_14=35.0,
        sma_8=UNDERLYING_PRICE * 1.05, sma_20=UNDERLYING_PRICE * 1.06, sma_50=UNDERLYING_PRICE * 1.08,
        next_earnings_date=None,
    )
    defaults.update(overrides)
    return IndicatorSnapshot(**defaults)


def _price_history_with_support(low: float = 90.0, high: float = 100.0, days: int = 90) -> list[PriceBar]:
    pattern = [(high, low), (high + 5, high - 5), (high, low), (high - 2, low + 2), (high, low)]
    start = date(2025, 11, 1)
    return [
        PriceBar(symbol="TST", trade_date=start + timedelta(days=i), open=(h + l) / 2, high=h, low=l, close=(h + l) / 2, volume=1000)
        for i, (h, l) in enumerate([pattern[i % len(pattern)] for i in range(days)])
    ]


def _put(strike: float, dte: int, mid: float) -> OptionContract:
    half_spread = 0.05
    return OptionContract(
        symbol="TST", option_type="put", strike=strike, expiration=AS_OF + timedelta(days=dte),
        bid=round(mid - half_spread, 2), ask=round(mid + half_spread, 2), last_price=mid,
        implied_volatility=0.30, open_interest=500, volume=50,
        greeks=Greeks(delta=-0.12, gamma=0.01, theta=-0.02, vega=0.05, rho=0.01, source="calculated"),
    )


def _eligible_chain() -> OptionChain:
    return OptionChain(symbol="TST", as_of=AS_OF, underlying_price=UNDERLYING_PRICE, contracts=[_put(75, 40, 1.80)])


def test_process_symbol_entry_opens_position_when_criteria_pass(conn):
    settings = _settings()
    engine.process_symbol_entry(conn, "TST", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)
    open_positions = repo.get_open_simulated_positions(conn, "TST")
    assert len(open_positions) == 1
    assert open_positions[0]["strike"] == 75


def test_process_symbol_entry_skips_when_criteria_fail(conn):
    settings = _settings()
    engine.process_symbol_entry(conn, "TST", _snapshot(rsi_14=80.0), _eligible_chain(), _price_history_with_support(), settings)
    assert repo.get_open_simulated_positions(conn, "TST") == []


def test_process_symbol_entry_skips_when_already_has_open_position(conn):
    settings = _settings()
    engine.process_symbol_entry(conn, "TST", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)
    assert len(repo.get_open_simulated_positions(conn, "TST")) == 1
    # Segunda corrida el mismo día (o uno posterior) no debe abrir una segunda posición.
    engine.process_symbol_entry(conn, "TST", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)
    assert len(repo.get_open_simulated_positions(conn, "TST")) == 1


def test_process_symbol_entry_noop_when_simulator_disabled(conn):
    settings = _settings()
    settings.simulator.enabled = False
    engine.process_symbol_entry(conn, "TST", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)
    assert repo.get_simulated_account(conn) is None


def test_process_symbol_entry_blocked_when_puts_paused(conn):
    """Botón de pausa (usuario 2026-08): pausada, NO abre puts nuevos; al reanudar, vuelve a abrir."""
    settings = _settings()
    repo.set_puts_paused(conn, True)
    engine.process_symbol_entry(conn, "TST", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)
    assert repo.get_open_simulated_positions(conn) == []
    repo.set_puts_paused(conn, False)
    engine.process_symbol_entry(conn, "TST", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)
    assert len(repo.get_open_simulated_positions(conn)) == 1


def test_process_symbol_entry_respects_daily_open_limit(conn):
    """Tope de N tickets de puts por día (usuario 2026-08): al llegar al tope, no abre más hoy."""
    settings = _settings()
    settings.simulator.max_opens_per_day = 1
    engine.process_symbol_entry(conn, "AAA", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)
    assert len(repo.get_open_simulated_positions(conn)) == 1
    engine.process_symbol_entry(conn, "BBB", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)
    assert len(repo.get_open_simulated_positions(conn)) == 1  # bloqueado por el tope diario
    assert repo.count_puts_opens_today(conn, AS_OF) == 1


def test_mark_and_close_positions_records_equity_snapshot(conn, monkeypatch):
    monkeypatch.setattr(engine, "market_session", lambda *a, **k: "abierto")
    settings = _settings()
    engine.process_symbol_entry(conn, "TST", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)

    open_position = repo.get_open_simulated_positions(conn, "TST")[0]
    # Mismo vencimiento (dte=40 desde AS_OF) que el contrato abierto — `find_contract` matchea
    # por strike+vencimiento exactos, no por "días restantes" al momento de marcar.
    live_chain = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=91.0, contracts=[_put(75, 40, 1.60)])
    broker = FakeBroker(price=91.0, chain=live_chain)

    tomorrow = AS_OF + timedelta(days=1)
    engine.mark_and_close_positions(conn, broker, settings, tomorrow)

    history = repo.get_simulated_equity_history(conn)
    assert len(history) == 1
    assert history[0]["snapshot_date"] == tomorrow.isoformat()
    marked = repo.get_open_simulated_positions(conn, "TST")[0]
    assert marked["id"] == open_position["id"]
    assert marked["last_marked_date"] == tomorrow.isoformat()


def test_equity_is_continuous_no_premium_double_count(conn, monkeypatch):
    monkeypatch.setattr(engine, "market_session", lambda *a, **k: "abierto")
    # Fix CRÍTICO de auditoría: el equity no debe inflarse por la prima cobrada. Abrir strike 75
    # (garantía 7500, prima 1.80 -> cash 92,680) deja el equity en 100,000, no 100,180.
    settings = _settings()
    engine.process_symbol_entry(conn, "TST", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)

    # Marcar sin cerrar (valor 1.60 -> no realizado +20): equity = 100,020, NO 100,200.
    chain_mark = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=91.0, contracts=[_put(75, 40, 1.60)])
    engine.mark_and_close_positions(conn, FakeBroker(91.0, chain_mark), settings, AS_OF + timedelta(days=1))
    assert repo.get_simulated_equity_history(conn)[-1]["equity"] == pytest.approx(100_020.0)

    # Cerrar ganador (valor 1.20 -> ganancia realizada 60): equity continuo en 100,060, sin caída.
    chain_close = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=91.0, contracts=[_put(75, 40, 1.20)])
    engine.mark_and_close_positions(conn, FakeBroker(91.0, chain_close), settings, AS_OF + timedelta(days=2))
    assert repo.get_open_simulated_positions(conn, "TST") == []
    assert repo.get_simulated_equity_history(conn)[-1]["equity"] == pytest.approx(100_060.0)


def test_mark_and_close_skips_when_market_closed(conn, monkeypatch):
    """Con el mercado cerrado no se re-marca: se conserva la última marca del día (usuario 2026-08-04)."""
    monkeypatch.setattr(engine, "market_session", lambda *a, **k: "abierto")
    settings = _settings()
    engine.process_symbol_entry(conn, "TST", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)
    # ahora el mercado "cierra"
    monkeypatch.setattr(engine, "market_session", lambda *a, **k: "cerrado")

    class _BoomBroker:
        def get_quote(self, symbol):
            raise AssertionError("no debería pedir cotización con el mercado cerrado")
        def get_option_chain(self, symbol, expiration_range_days=(1, 95)):
            raise AssertionError("no debería pedir cadena con el mercado cerrado")

    # no re-marca ni pide datos; no crea snapshot de equity nuevo
    before = len(repo.get_simulated_equity_history(conn))
    engine.mark_and_close_positions(conn, _BoomBroker(), settings, AS_OF + timedelta(days=1))
    assert len(repo.get_simulated_equity_history(conn)) == before


def _analysis_for_enrich() -> SymbolAnalysis:
    return SymbolAnalysis(
        snapshot=_snapshot(hv_20d=0.28, rsi_14=34.0),
        chain=_eligible_chain(),
        quote=Quote(symbol="TST", as_of=AS_OF, last_price=93.0, bid=93.0, ask=93.0, net_change_pct=-1.4),
        price_history=[],
    )


def test_enrich_open_decision_fills_missing_market_fields(conn):
    """Una posición abierta por el código viejo (sin datos de mercado) se completa reusando el
    análisis del scan, sin pisar los parámetros de apertura (usuario 2026-08-05)."""
    settings = _settings()
    engine.process_symbol_entry(conn, "TST", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)
    dec = repo.get_latest_open_decision_for_symbol(conn, "TST")
    ctx = json.loads(dec["context_json"])
    delta_at_open = ctx["chosen_delta"]
    # simular la apertura "vieja": sacar los campos de mercado que el código nuevo sí guarda
    for k in ("underlying_price", "chosen_bid", "chosen_ask", "chosen_spread_pct",
              "chosen_open_interest", "chosen_volume", "chosen_pop", "hv_20d", "rsi_14", "day_change_pct"):
        ctx.pop(k, None)
    repo.update_decision_context(conn, dec["id"], json.dumps(ctx))

    assert engine.enrich_open_decision(conn, "TST", _analysis_for_enrich()) is True
    ctx2 = json.loads(repo.get_latest_open_decision_for_symbol(conn, "TST")["context_json"])
    assert ctx2["underlying_price"] == 93.0
    assert ctx2["day_change_pct"] == -1.4
    assert ctx2["chosen_bid"] == 1.75 and ctx2["chosen_ask"] == 1.85
    assert ctx2["chosen_open_interest"] == 500 and ctx2["chosen_volume"] == 50
    assert abs(ctx2["chosen_pop"] - (1 - 0.12)) < 1e-6  # 1 − |delta| del contrato
    assert ctx2["hv_20d"] == 0.28 and ctx2["rsi_14"] == 34.0
    assert ctx2["chosen_delta"] == delta_at_open  # no pisa lo de apertura


def test_enrich_open_decision_noop_when_already_enriched(conn):
    """Si la decisión ya tiene los datos de mercado (apertura con el código nuevo) no reescribe."""
    settings = _settings()
    engine.process_symbol_entry(conn, "TST", _snapshot(), _eligible_chain(), _price_history_with_support(), settings)
    assert engine.enrich_open_decision(conn, "TST", _analysis_for_enrich()) is False


def test_enrich_open_decision_noop_when_no_position(conn):
    assert engine.enrich_open_decision(conn, "TST", _analysis_for_enrich()) is False
