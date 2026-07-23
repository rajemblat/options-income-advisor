from __future__ import annotations

import logging
import sqlite3
from datetime import date

from options_advisor.alerts.engine import process_symbol_alerts
from options_advisor.broker.base import BrokerClient
from options_advisor.config import Settings
from options_advisor.indicators.pipeline import analyze_symbol
from options_advisor.market_context import economic_calendar, finnhub_client, fred_client, kalshi_client
from options_advisor.scheduler.market_calendar import is_market_day
from options_advisor.storage import repository as repo
from options_advisor.storage.models import MacroSnapshot, NewsItem

logger = logging.getLogger(__name__)


def _refresh_macro_snapshot(conn: sqlite3.Connection, today: date, finnhub_api_key: str | None, fred_api_key: str | None) -> None:
    """Contexto macro: una consulta por job run (no por símbolo, es el mismo dato para todos
    los símbolos ese día). Nunca rompe el job — cada fuente ya devuelve None/[] sola si falla
    (Sección de variables: earnings/Fed/CPI-empleo-PBI)."""
    try:
        target_range = fred_client.get_fed_funds_target_range(fred_api_key)
        macro = fred_client.get_macro_snapshot(fred_api_key)
        fed_probs = kalshi_client.get_fed_decision_probabilities(target_range[1]) if target_range else None
        events = economic_calendar.get_upcoming_macro_events(finnhub_api_key, today)

        repo.upsert_macro_snapshot(
            conn,
            MacroSnapshot(
                snapshot_date=today,
                fed_funds_lower=target_range[0] if target_range else None,
                fed_funds_upper=target_range[1] if target_range else None,
                cpi_yoy_pct=macro["cpi_yoy_pct"],
                unemployment_rate_pct=macro["unemployment_rate_pct"],
                gdp_growth_annualized_pct=macro["gdp_growth_annualized_pct"],
                fed_meeting_date=fed_probs.meeting_date if fed_probs else None,
                fed_hike_probability=fed_probs.hike_probability if fed_probs else None,
                fed_hold_probability=fed_probs.hold_probability if fed_probs else None,
                fed_cut_probability=fed_probs.cut_probability if fed_probs else None,
                upcoming_events=events,
            ),
        )
    except Exception:
        logger.exception("Fallo al refrescar el contexto macro; se continúa con el análisis por símbolo")


def _refresh_news_for_symbol(conn: sqlite3.Connection, symbol: str, today: date, finnhub_api_key: str | None) -> None:
    """Noticias recientes por símbolo. Falla aislada (igual que el contexto macro): un
    problema con Finnhub nunca debe tumbar el análisis de indicadores/alertas del símbolo."""
    try:
        rows = finnhub_client.get_recent_news(symbol, today, finnhub_api_key)
        items = [
            NewsItem(
                symbol=symbol,
                published_at=row.get("published_at"),
                headline=row["headline"],
                source=row.get("source"),
                url=row["url"],
                summary=row.get("summary"),
                fetched_date=today,
            )
            for row in rows
            if row.get("headline") and row.get("url")
        ]
        repo.insert_news_items(conn, items)
    except Exception:
        logger.exception("Fallo al refrescar noticias de %s; se continúa con el resto del análisis", symbol)


def job_poll_and_analyze(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    symbols: list[str],
    settings: Settings,
    anthropic_api_key: str | None,
    finnhub_api_key: str | None = None,
    fred_api_key: str | None = None,
) -> None:
    """Job principal del scheduler: por cada símbolo, calcula indicadores y evalúa alertas.
    Un fallo en un símbolo no debe tumbar el resto (Sección 6 del plan de Fase 1). El mismo
    job corre en cada disparo programado (apertura, cada 30 min, cierre) — la última corrida
    del día deja el snapshot "oficial" gracias al upsert por (symbol, snapshot_date)."""
    today = date.today()
    if not is_market_day(today):
        logger.info("%s no es día de mercado, se salta el polling", today)
        return

    _refresh_macro_snapshot(conn, today, finnhub_api_key, fred_api_key)

    for symbol in symbols:
        try:
            open_positions = repo.get_open_assigned_positions(conn, symbol)
            analysis = analyze_symbol(broker, conn, symbol, settings, finnhub_api_key=finnhub_api_key)
            _refresh_news_for_symbol(conn, symbol, today, finnhub_api_key)
            alerts = process_symbol_alerts(
                conn,
                analysis,
                settings,
                has_open_assigned_position=len(open_positions) > 0,
                anthropic_api_key=anthropic_api_key,
                finnhub_api_key=finnhub_api_key,
            )
            logger.info("%s: iv_rank=%s, %d alerta(s) nueva(s)", symbol, analysis.snapshot.iv_rank, len(alerts))
        except Exception:
            logger.exception("Fallo al procesar %s; se continúa con el resto de los símbolos", symbol)
