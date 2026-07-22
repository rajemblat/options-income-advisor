from __future__ import annotations

import sqlite3

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from options_advisor.broker.base import BrokerClient
from options_advisor.config import Settings
from options_advisor.scheduler.jobs import job_poll_and_analyze


def _hh_mm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def build_scheduler(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    symbols: list[str],
    settings: Settings,
    anthropic_api_key: str | None,
) -> BlockingScheduler:
    """Arma el scheduler con los 3 disparos descriptos en la Sección 6 del plan de Fase 1:
    apertura, chequeo periódico durante el horario regular, y snapshot de cierre."""
    scheduler = BlockingScheduler(timezone=settings.scheduler.timezone)

    def run_job() -> None:
        job_poll_and_analyze(broker, conn, symbols, settings, anthropic_api_key)

    open_h, open_m = _hh_mm(settings.scheduler.market_open_snapshot_time)
    close_h, close_m = _hh_mm(settings.scheduler.market_close_snapshot_time)
    start_h, _ = _hh_mm(settings.scheduler.market_hours_start)
    end_h, _ = _hh_mm(settings.scheduler.market_hours_end)

    scheduler.add_job(
        run_job,
        CronTrigger(day_of_week="mon-fri", hour=open_h, minute=open_m, timezone=settings.scheduler.timezone),
        id="market_open_snapshot",
    )
    scheduler.add_job(
        run_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=f"{start_h}-{end_h}",
            minute=f"*/{settings.scheduler.poll_interval_minutes}",
            timezone=settings.scheduler.timezone,
        ),
        id="periodic_poll",
    )
    scheduler.add_job(
        run_job,
        CronTrigger(day_of_week="mon-fri", hour=close_h, minute=close_m, timezone=settings.scheduler.timezone),
        id="market_close_snapshot",
    )
    return scheduler
