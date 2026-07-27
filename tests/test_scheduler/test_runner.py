from __future__ import annotations

from options_advisor.config import load_settings
from options_advisor.scheduler.runner import build_scheduler
from options_advisor.storage import db


def test_build_scheduler_registers_real_trade_detection_job():
    """Pestaña Operaciones (pedido 2026-07-27): la detección de operaciones reales corre en su
    propio job, separado de periodic_poll, para poder tener una cadencia más seguida."""
    conn = db.connect(":memory:")
    scheduler = build_scheduler(broker=None, conn=conn, symbols=[], settings=load_settings(), anthropic_api_key=None)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "real_trade_detection" in job_ids
    assert "periodic_poll" in job_ids


def test_real_trade_detection_job_uses_configured_interval():
    conn = db.connect(":memory:")
    settings = load_settings()
    scheduler = build_scheduler(broker=None, conn=conn, symbols=[], settings=settings, anthropic_api_key=None)
    job = scheduler.get_job("real_trade_detection")
    field_expressions = {f.name: str(f) for f in job.trigger.fields}
    assert f"*/{settings.scheduler.real_trade_poll_interval_minutes}" == field_expressions["minute"]
