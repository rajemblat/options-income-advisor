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


def test_real_trade_detection_does_not_share_a_thread_with_the_heavy_scan():
    """CANDADO (usuario 2026-08-17: "demora más de 5 minutos y debe demorar menos de 10 segundos").

    La detección compartía el executor 'default' con `robot_scan`, que tarda varios minutos barriendo
    ~100 símbolos. Con max_workers=1, APScheduler la salteaba minuto a minuto ("maximum number of
    running instances reached") y el log del 17/08 decía textual "Run time of job
    run_real_trade_detection was missed by 0:04:39": una operación llenada 11:28 apareció 11:39.

    Si alguien le saca el executor propio, este test lo frena."""
    conn = db.connect(":memory:")
    scheduler = build_scheduler(broker=None, conn=conn, symbols=[], settings=load_settings(), anthropic_api_key=None)
    deteccion = scheduler.get_job("real_trade_detection")
    escaneo = scheduler.get_job("robot_scan")
    assert deteccion.executor == "trades", "la detección necesita su propio hilo"
    assert deteccion.executor != escaneo.executor, "no puede volver a quedar detrás del escaneo pesado"
    assert scheduler._executors["trades"]._pool._max_workers == 1, "un solo hilo: nunca dos corridas a la vez"


def test_the_dedicated_trades_thread_accepts_its_own_db_connection():
    """Un hilo propio SIN conexión propia sería peor que el problema que arregla: dos hilos sobre la
    MISMA conexión de SQLite fue la causa de la corrupción de agosto. `build_scheduler` tiene que
    aceptar la conexión dedicada igual que las de butterfly y chat."""
    conn = db.connect(":memory:")
    trades_conn = db.connect(":memory:")
    scheduler = build_scheduler(broker=None, conn=conn, symbols=[], settings=load_settings(),
                                anthropic_api_key=None, trades_conn=trades_conn)
    assert scheduler.get_job("real_trade_detection") is not None


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
