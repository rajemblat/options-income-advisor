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


def test_build_scheduler_registers_robot_scan_job():
    """El robot opera solo con el scheduler prendido (usuario 2026-08): debe existir un job de
    escaneo rápido solo-robot con la cadencia configurada."""
    conn = db.connect(":memory:")
    settings = load_settings()
    scheduler = build_scheduler(broker=None, conn=conn, symbols=[], settings=settings, anthropic_api_key=None)
    job = scheduler.get_job("robot_scan")
    assert job is not None
    field_expressions = {f.name: str(f) for f in job.trigger.fields}
    assert f"*/{settings.scheduler.robot_scan_interval_minutes}" == field_expressions["minute"]


def test_scheduler_serializes_jobs_to_avoid_db_corruption():
    """Anti-corrupción: un solo worker + sin instancias solapadas, para que dos corridas nunca
    escriban la base al mismo tiempo (incidente 'database disk image is malformed', 2026-08)."""
    conn = db.connect(":memory:")
    scheduler = build_scheduler(broker=None, conn=conn, symbols=[], settings=load_settings(), anthropic_api_key=None)
    executor = scheduler._executors["default"]
    assert executor._pool._max_workers == 1
    assert scheduler._job_defaults["max_instances"] == 1
    assert scheduler._job_defaults["coalesce"] is True


def test_build_scheduler_registers_learning_review_job():
    conn = db.connect(":memory:")
    scheduler = build_scheduler(broker=None, conn=conn, symbols=[], settings=load_settings(), anthropic_api_key=None)
    assert scheduler.get_job("learning_review") is not None


def test_butterfly_tick_runs_on_dedicated_executor():
    """El Iron Butterfly (tick de 1 min, 0DTE) corre en SU PROPIO executor para no quedar atrás del
    escaneo pesado de puts, que antes lo salteaba ('maximum number of running instances reached',
    usuario 2026-08-05). Ambos executors son de 1 worker; el aislamiento lo da tenerlos separados
    + conexiones de base distintas."""
    conn = db.connect(":memory:")
    scheduler = build_scheduler(broker=None, conn=conn, symbols=[], settings=load_settings(), anthropic_api_key=None)
    # existe un executor 'butterfly' de un solo worker, aparte del 'default'
    assert "butterfly" in scheduler._executors
    assert scheduler._executors["butterfly"]._pool._max_workers == 1
    # el job del butterfly está asignado a ese executor
    job = scheduler.get_job("butterfly_tick")
    assert job is not None and job.executor == "butterfly"


def test_condor_tick_registered_on_intraday_executor():
    """Iron Condor 0DTE (Estrategia 3): corre en el mismo executor de 1 hilo que el butterfly."""
    conn = db.connect(":memory:")
    scheduler = build_scheduler(broker=None, conn=conn, symbols=[], settings=load_settings(), anthropic_api_key=None)
    job = scheduler.get_job("condor_tick")
    assert job is not None and job.executor == "butterfly"


def test_butterfly_uses_its_own_connection_when_provided():
    """El entrypoint pasa una conexión dedicada para el hilo del Iron; build_scheduler la usa en
    lugar de la compartida (aislamiento de escritura vía WAL entre conexiones distintas)."""
    conn = db.connect(":memory:")
    other = db.connect(":memory:")
    # no debe fallar al registrar con una conexión de butterfly aparte
    scheduler = build_scheduler(
        broker=None, conn=conn, symbols=[], settings=load_settings(), anthropic_api_key=None, butterfly_conn=other
    )
    assert scheduler.get_job("butterfly_tick") is not None
