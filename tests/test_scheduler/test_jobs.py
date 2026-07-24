from __future__ import annotations

from datetime import date

import pytest

from options_advisor.config import load_settings
from options_advisor.scheduler import jobs
from options_advisor.storage import db
from options_advisor.storage import repository as repo
from options_advisor.storage.models import MacroSnapshot

TODAY = date.today()  # job_premarket_digest usa date.today() internamente (no fecha inyectada);
# fijarla acá haría que el test dependa de correr un día calendario exacto.


@pytest.fixture
def conn():
    return db.connect(":memory:")


def test_refresh_news_for_symbol_persists_items(conn):
    news_rows = [{"headline": "AAPL news", "source": "Yahoo", "url": "https://x/1", "summary": "s", "published_at": None}]
    jobs._refresh_news_for_symbol(conn, "AAPL", TODAY, news_rows)

    result = repo.get_recent_news(conn, symbol="AAPL")
    assert len(result) == 1
    assert result[0]["headline"] == "AAPL news"


def test_refresh_news_for_symbol_never_raises_on_bad_rows(conn):
    jobs._refresh_news_for_symbol(conn, "AAPL", TODAY, [{"headline": None}])  # no debe lanzar
    assert repo.get_recent_news(conn, symbol="AAPL") == []


def test_refresh_news_for_symbol_skips_items_without_url(conn):
    news_rows = [{"headline": "sin url", "source": "Yahoo", "url": None, "summary": "s", "published_at": None}]
    jobs._refresh_news_for_symbol(conn, "AAPL", TODAY, news_rows)
    assert repo.get_recent_news(conn, symbol="AAPL") == []


def test_job_premarket_digest_saves_dashboard_notification_with_risk_events_and_new_alerts(conn, monkeypatch):
    monkeypatch.setattr(jobs, "is_market_day", lambda d: True)
    monkeypatch.setattr(
        jobs,
        "_run_full_analysis",
        lambda *a, **k: [{"symbol": "AAPL", "strategy_type": "cash_secured_put", "score": 80}],
    )
    repo.upsert_macro_snapshot(
        conn,
        MacroSnapshot(
            snapshot_date=TODAY,
            upcoming_events=[{"date": TODAY.isoformat(), "event": "Decisión de tasas de la Fed (FOMC)", "country": "US", "impact": "high"}],
        ),
    )

    jobs.job_premarket_digest(broker=None, conn=conn, symbols=["AAPL"], settings=load_settings(), anthropic_api_key=None)

    assert repo.get_unread_notification_count(conn) == 1
    notification = repo.get_recent_notifications(conn, limit=1)[0]
    assert notification["kind"] == "premarket_digest"
    assert "FOMC" in notification["body"]
    assert "AAPL" in notification["body"]
    assert "Cash-Secured Put" in notification["body"]


def test_job_premarket_digest_skips_on_non_market_day(conn, monkeypatch):
    monkeypatch.setattr(jobs, "is_market_day", lambda d: False)
    called = []
    monkeypatch.setattr(jobs, "_run_full_analysis", lambda *a, **k: called.append(1))

    jobs.job_premarket_digest(broker=None, conn=conn, symbols=["AAPL"], settings=load_settings(), anthropic_api_key=None)
    assert called == []
    assert repo.get_unread_notification_count(conn) == 0


class _FakeBroker:
    """Duck-type mínimo de BrokerClient: solo lo que _run_full_analysis necesita para esta
    prueba de wiring (analyze_symbol/process_symbol_alerts están mockeados, no se llaman de
    verdad)."""

    def __init__(self, share_positions: dict[str, int]):
        self._share_positions = share_positions

    def get_all_share_positions(self) -> dict[str, int]:
        return self._share_positions


class _FakeAnalysis:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.snapshot = type("S", (), {"iv_rank": 50, "symbol": symbol, "snapshot_date": TODAY})()


def test_run_full_analysis_passes_real_share_position_as_has_open_assigned_position(conn, monkeypatch):
    captured: list[tuple[str, bool, str]] = []

    monkeypatch.setattr(jobs, "analyze_symbol", lambda broker, conn, symbol, settings, **k: _FakeAnalysis(symbol))
    monkeypatch.setattr(jobs.finnhub_client, "get_recent_news", lambda *a, **k: [])
    monkeypatch.setattr(jobs, "_refresh_news_for_symbol", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "_refresh_macro_snapshot", lambda *a, **k: None)

    def _fake_process_symbol_alerts(conn, analysis, settings, has_open_assigned_position, risk_level, **k):
        captured.append((analysis.symbol, has_open_assigned_position, risk_level))
        return []

    monkeypatch.setattr(jobs, "process_symbol_alerts", _fake_process_symbol_alerts)

    # NVDA: 300 acciones reales (>= 100, habilita Covered Call/Collar). AAPL: sin acciones.
    broker = _FakeBroker(share_positions={"NVDA": 300})
    settings = load_settings()

    jobs._run_full_analysis(broker, conn, ["NVDA", "AAPL"], settings, TODAY, None, None, None)

    # Una corrida evalúa los 3 perfiles fijos por símbolo (Sección "Correr análisis ahora"
    # cubre los 3 perfiles a la vez, no solo el activo).
    assert captured == [
        ("NVDA", True, "conservador"),
        ("NVDA", True, "moderado"),
        ("NVDA", True, "agresivo"),
        ("AAPL", False, "conservador"),
        ("AAPL", False, "moderado"),
        ("AAPL", False, "agresivo"),
    ]
