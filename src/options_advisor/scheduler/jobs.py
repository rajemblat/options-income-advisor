from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime

from options_advisor.alerts.digest import build_premarket_digest_text
from options_advisor.alerts.engine import process_symbol_alerts
from options_advisor.broker.base import BrokerClient
from options_advisor.config import Settings
from options_advisor.indicators.pipeline import analyze_symbol
from options_advisor.market_context import economic_calendar, finnhub_client, fred_client, kalshi_client
from options_advisor.scheduler.market_calendar import is_market_day
from options_advisor.storage import repository as repo
from options_advisor.storage.models import MacroSnapshot, NewsItem, Notification

logger = logging.getLogger(__name__)

MIN_SHARES_FOR_COVERED_STRATEGIES = 100  # 1 contrato de opción cubre 100 acciones


def _refresh_macro_snapshot(conn: sqlite3.Connection, today: date, finnhub_api_key: str | None, fred_api_key: str | None) -> None:
    """Contexto macro: una consulta por job run (no por símbolo, es el mismo dato para todos
    los símbolos ese día). Nunca rompe el job — cada fuente ya devuelve None/[] sola si falla
    (Sección de variables: earnings/Fed/CPI-empleo-PBI)."""
    try:
        target_range = fred_client.get_fed_funds_target_range(fred_api_key)
        macro = fred_client.get_macro_snapshot(fred_api_key)
        fed_probs = kalshi_client.get_fed_decision_probabilities(target_range[1]) if target_range else None
        events = economic_calendar.get_upcoming_macro_events(finnhub_api_key, fred_api_key, today)

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


def _run_full_analysis(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    symbols: list[str],
    settings: Settings,
    today: date,
    anthropic_api_key: str | None,
    finnhub_api_key: str | None,
    fred_api_key: str | None,
) -> list[dict]:
    """Macro + noticias + indicadores + alertas para todos los símbolos — el cuerpo real de una
    corrida, compartido por el polling regular y el digest pre-apertura (job_premarket_digest
    necesita saber qué alertas salieron de SU corrida, no solo que el job terminó). Un fallo en
    un símbolo no tumba el resto (Sección 6 del plan de Fase 1). Devuelve las alertas nuevas
    generadas en esta corrida (lista vacía si no hubo ninguna)."""
    _refresh_macro_snapshot(conn, today, finnhub_api_key, fred_api_key)

    # Una sola consulta de posiciones reales por corrida (no por símbolo) — habilita Covered
    # Call/Collar con la tenencia REAL de la cuenta Schwab en vez de la tabla interna
    # `assigned_positions` (pensada para trackear asignación de CSP propia, hoy sin UI que la
    # llene). {} en modo mock o si falla la consulta (ver broker/base.py::get_all_share_positions).
    share_positions = broker.get_all_share_positions()

    new_alerts: list[dict] = []
    for symbol in symbols:
        try:
            open_positions = repo.get_open_assigned_positions(conn, symbol)
            has_shares = share_positions.get(symbol, 0) >= MIN_SHARES_FOR_COVERED_STRATEGIES
            analysis = analyze_symbol(broker, conn, symbol, settings, finnhub_api_key=finnhub_api_key)
            _refresh_news_for_symbol(conn, symbol, today, finnhub_api_key)
            alerts = process_symbol_alerts(
                conn,
                analysis,
                settings,
                has_open_assigned_position=len(open_positions) > 0 or has_shares,
                anthropic_api_key=anthropic_api_key,
                finnhub_api_key=finnhub_api_key,
            )
            logger.info("%s: iv_rank=%s, %d alerta(s) nueva(s)", symbol, analysis.snapshot.iv_rank, len(alerts))
            new_alerts.extend(alerts)
        except Exception:
            logger.exception("Fallo al procesar %s; se continúa con el resto de los símbolos", symbol)

    return new_alerts


def job_poll_and_analyze(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    symbols: list[str],
    settings: Settings,
    anthropic_api_key: str | None,
    finnhub_api_key: str | None = None,
    fred_api_key: str | None = None,
) -> None:
    """Job principal del scheduler: calcula indicadores y evalúa alertas para todos los
    símbolos. El mismo job corre en cada disparo programado (apertura, cada 30 min, cierre) —
    la última corrida del día deja el snapshot "oficial" gracias al upsert por
    (symbol, snapshot_date)."""
    today = date.today()
    if not is_market_day(today):
        logger.info("%s no es día de mercado, se salta el polling", today)
        return

    _run_full_analysis(broker, conn, symbols, settings, today, anthropic_api_key, finnhub_api_key, fred_api_key)


def job_premarket_digest(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    symbols: list[str],
    settings: Settings,
    anthropic_api_key: str | None,
    finnhub_api_key: str | None = None,
    fred_api_key: str | None = None,
) -> None:
    """Corre antes de la apertura (hora configurable en settings.scheduler.premarket_digest_time):
    hace la misma corrida completa que job_poll_and_analyze (así detecta alertas nuevas de esta
    ventana, no solo repite el cierre del día anterior) y guarda un resumen como notificación del
    dashboard (campanita 🔔) con los eventos de riesgo de HOY (FOMC/CPI/empleo/earnings) y las
    alertas nuevas — pensado para leerlo antes de que abra el mercado. No usa Telegram: ese canal
    (alerts/notifier.py) queda implementado pero inerte para cuando se decida activarlo más
    adelante."""
    today = date.today()
    if not is_market_day(today):
        logger.info("%s no es día de mercado, se salta el digest pre-apertura", today)
        return

    new_alerts = _run_full_analysis(broker, conn, symbols, settings, today, anthropic_api_key, finnhub_api_key, fred_api_key)

    macro = repo.get_latest_macro_snapshot(conn)
    upcoming_events = json.loads(macro["upcoming_events_json"]) if macro and macro["upcoming_events_json"] else []
    earnings_by_symbol = {symbol: repo.get_latest_next_earnings_date(conn, symbol) for symbol in symbols}

    text = build_premarket_digest_text(upcoming_events, earnings_by_symbol, new_alerts, today)
    repo.insert_notification(
        conn,
        Notification(kind="premarket_digest", title=f"Resumen pre-apertura — {today.isoformat()}", body=text, created_at=datetime.now()),
    )
