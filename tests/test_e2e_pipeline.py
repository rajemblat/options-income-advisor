from __future__ import annotations

from datetime import date, timedelta

from options_advisor.alerts.engine import process_symbol_alerts
from options_advisor.broker.mock_client import MockBrokerClient
from options_advisor.config import load_settings
from options_advisor.indicators.pipeline import analyze_symbol
from options_advisor.storage import db


def test_full_pipeline_iv_rank_bootstrap_and_alert_dedup(mock_fixtures_dir):
    """Integración completa: MockBrokerClient → indicadores → estrategia → scoring →
    alertas → persistencia en SQLite, simulando múltiples días para validar tanto la
    transición del bootstrap de IV Rank (Sección 4) como el dedup de alertas (Sección 6)."""
    settings = load_settings()
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    conn = db.connect(":memory:")

    start_date = date(2026, 1, 1)
    sources_seen = []
    last_analysis = None
    for day_offset in range(25):
        client.set_as_of_date(start_date + timedelta(days=day_offset))
        last_analysis = analyze_symbol(client, conn, "TST", settings)
        sources_seen.append(last_analysis.snapshot.iv_rank_source)

    # Al principio no hay suficientes snapshots de IV propia: se usa el proxy de HV.
    assert sources_seen[0] == "historical_volatility_proxy"
    # Tras acumular min_sessions_for_real_iv (20 por default) snapshots, pasa a IV real.
    assert sources_seen[-1] == "implied_volatility"
    assert "historical_volatility_proxy" in sources_seen[:19]

    # La fixture termina con IV en su máximo → debería activar candidatos de venta de prima.
    assert last_analysis.snapshot.iv_rank is not None
    assert last_analysis.snapshot.iv_rank > 50

    alerts_first_run = process_symbol_alerts(conn, last_analysis, settings, anthropic_api_key=None)
    assert len(alerts_first_run) > 0
    assert any(a["strategy_type"] == "cash_secured_put" for a in alerts_first_run)

    # Repetir el mismo día (como si el scheduler corriera de nuevo 30 min después) no
    # debe generar alertas duplicadas.
    alerts_second_run = process_symbol_alerts(conn, last_analysis, settings, anthropic_api_key=None)
    assert alerts_second_run == []

    persisted = conn.execute("SELECT COUNT(*) AS n FROM alerts WHERE symbol = 'TST'").fetchone()
    assert persisted["n"] == len(alerts_first_run)

    persisted_candidates = conn.execute("SELECT COUNT(*) AS n FROM candidate_contracts WHERE symbol = 'TST'").fetchone()
    assert persisted_candidates["n"] == len(alerts_first_run)
