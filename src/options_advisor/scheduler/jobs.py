from __future__ import annotations

import logging
import sqlite3
from datetime import date

from options_advisor.alerts.engine import process_symbol_alerts
from options_advisor.broker.base import BrokerClient
from options_advisor.config import Settings
from options_advisor.indicators.pipeline import analyze_symbol
from options_advisor.scheduler.market_calendar import is_market_day
from options_advisor.storage import repository as repo

logger = logging.getLogger(__name__)


def job_poll_and_analyze(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    symbols: list[str],
    settings: Settings,
    anthropic_api_key: str | None,
) -> None:
    """Job principal del scheduler: por cada símbolo, calcula indicadores y evalúa alertas.
    Un fallo en un símbolo no debe tumbar el resto (Sección 6 del plan de Fase 1). El mismo
    job corre en cada disparo programado (apertura, cada 30 min, cierre) — la última corrida
    del día deja el snapshot "oficial" gracias al upsert por (symbol, snapshot_date)."""
    today = date.today()
    if not is_market_day(today):
        logger.info("%s no es día de mercado, se salta el polling", today)
        return

    for symbol in symbols:
        try:
            open_positions = repo.get_open_assigned_positions(conn, symbol)
            analysis = analyze_symbol(broker, conn, symbol, settings)
            alerts = process_symbol_alerts(
                conn,
                analysis,
                settings,
                has_open_assigned_position=len(open_positions) > 0,
                anthropic_api_key=anthropic_api_key,
            )
            logger.info("%s: iv_rank=%s, %d alerta(s) nueva(s)", symbol, analysis.snapshot.iv_rank, len(alerts))
        except Exception:
            logger.exception("Fallo al procesar %s; se continúa con el resto de los símbolos", symbol)
