from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from options_advisor.broker import token_watch
from options_advisor.broker.base import BrokerClient
from options_advisor.config import Settings
from options_advisor.scheduler.jobs import (
    job_backtest_review,
    job_butterfly_tick,
    job_condor_tick,
    job_detect_real_trades,
    job_learning_review,
    job_live_position_maintenance,
    job_roll_tick,
    job_poll_and_analyze,
    job_premarket_digest,
    job_process_chat_orders,
    job_robot_scan,
)
from options_advisor.storage import repository as repo

logger = logging.getLogger(__name__)


def _hh_mm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def build_scheduler(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    symbols: list[str],
    settings: Settings,
    anthropic_api_key: str | None,
    finnhub_api_key: str | None = None,
    fred_api_key: str | None = None,
    butterfly_conn: sqlite3.Connection | None = None,
    chat_conn: sqlite3.Connection | None = None,
    trades_conn: sqlite3.Connection | None = None,
    live_conn: sqlite3.Connection | None = None,
) -> BlockingScheduler:
    """Arma el scheduler con los disparos de la Sección 6 del plan de Fase 1 (apertura, chequeo
    periódico, cierre) MÁS el escaneo rápido del robot que lo hace operar solo.

    Clave anti-corrupción (usuario 2026-08, tras el incidente "database disk image is malformed"):
    el escaneo pesado (puts + análisis) corre en UN solo worker (`max_workers=1`) con
    `max_instances=1`+`coalesce=True`, para que dos corridas de ESE grupo NUNCA se solapen.

    Hilo dedicado del Iron Butterfly (usuario 2026-08-05, "dejalo lo mejor posible"): el tick de 1
    minuto del butterfly corría en el MISMO worker que el escaneo pesado, así que mientras el
    escaneo tardaba >1 min, el tick del Iron se salteaba ("maximum number of running instances
    reached") y perdía minutos — malo para una estrategia 0DTE que caza reversiones rápidas. Ahora
    el butterfly tiene SU PROPIO executor (otro worker) y SU PROPIA conexión a la base. Es seguro
    porque: (a) el motor del Iron sólo escribe su tabla `butterfly_positions` y agrega filas a
    `robot_decisions` — estado disjunto del de los puts; (b) con conexiones separadas en WAL, SQLite
    serializa las escrituras entre ambas por su lock de WAL + `busy_timeout` (esto SÍ protege entre
    conexiones distintas, a diferencia de dos hilos sobre la MISMA conexión, que fue la causa de la
    corrupción). Cada worker usa una conexión propia y de a un hilo, que es el patrón seguro.
    (Sigue vigente NO tener el dashboard escribiendo a la vez que el scheduler contra la misma base.)"""
    scheduler = BlockingScheduler(
        timezone=settings.scheduler.timezone,
        executors={
            "default": {"type": "threadpool", "max_workers": 1},
            "butterfly": {"type": "threadpool", "max_workers": 1},
            # Hilo propio para el job rápido del chat (cada 15s): así no queda atrás del escaneo pesado
            # de puts que ocupa el worker 'default'. Usa su PROPIA conexión (chat_conn) — dos conexiones en
            # WAL en el mismo proceso son seguras (SQLite serializa escrituras con su lock + busy_timeout),
            # y el lock `_LIVE_ORDER_LOCK` de live_engine serializa la LÓGICA de órdenes reales entre hilos.
            "chat": {"type": "threadpool", "max_workers": 1},
            # Hilo propio para la DETECCIÓN de operaciones reales (usuario 2026-08-17: "demora más de
            # 5 minutos y debe demorar menos de 10 segundos"). Compartía el worker 'default' con el
            # escaneo pesado de puts, que tarda varios minutos, así que APScheduler la salteaba
            # ("maximum number of running instances reached") y el log mostraba textual "Run time of
            # job run_real_trade_detection was missed by 0:04:39". Resultado real del 17/08: una
            # operación llenada a las 11:28 apareció en la página recién a las 11:39. Mismo patrón
            # seguro que butterfly/chat: executor propio + conexión propia.
            "trades": {"type": "threadpool", "max_workers": 1},
            # Hilo propio del MANTENIMIENTO de posiciones reales (usuario 2026-08-19: "debería cerrar
            # sola al 52%"). El cierre por objetivo de ganancia colgaba del final del escaneo pesado,
            # que tarda minutos y se saltea a sí mismo; acá corre cada minuto pase lo que pase.
            "live": {"type": "threadpool", "max_workers": 1},
        },
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
    )

    # Conexión propia del Iron (su executor es un hilo aparte). El entrypoint (run_scheduler.py) pasa
    # una conexión dedicada; si no se pasa (tests), cae a la compartida — inocuo porque el job del
    # butterfly no se ejecuta en los tests, solo se registra. Dos conexiones en WAL en el mismo
    # proceso son seguras: SQLite serializa las escrituras con su lock de WAL + busy_timeout=5s.
    butterfly_conn = butterfly_conn if butterfly_conn is not None else conn
    # Conexión dedicada del job rápido del chat (su executor es otro hilo). En tests no se pasa y cae a la
    # compartida (inocuo: el job del chat no se ejecuta en los tests, solo se registra).
    chat_conn = chat_conn if chat_conn is not None else conn
    # Conexión dedicada de la detección de operaciones reales (su executor es otro hilo). En tests no se
    # pasa y cae a la compartida (inocuo: el job no se ejecuta en los tests, solo se registra).
    trades_conn = trades_conn if trades_conn is not None else conn
    # Conexión dedicada del mantenimiento de posiciones reales (su executor es otro hilo). En tests no
    # se pasa y cae a la compartida (inocuo: el job no se ejecuta en los tests, solo se registra).
    live_conn = live_conn if live_conn is not None else conn

    def run_job() -> None:
        job_poll_and_analyze(broker, conn, symbols, settings, anthropic_api_key, finnhub_api_key=finnhub_api_key, fred_api_key=fred_api_key)

    def run_robot_scan() -> None:
        job_robot_scan(broker, conn, symbols, settings, finnhub_api_key=finnhub_api_key, fred_api_key=fred_api_key)

    def run_chat_orders() -> None:
        job_process_chat_orders(broker, chat_conn, settings)

    def run_butterfly_tick() -> None:
        job_butterfly_tick(broker, butterfly_conn, settings)

    def run_condor_tick() -> None:
        # Iron Condor 0DTE (Estrategia 3): corre en el MISMO executor de 1 hilo y la MISMA conexión
        # que el butterfly, así los dos intradía SPX se serializan (nunca concurrentes) — una sola
        # conexión por hilo, el patrón seguro. Ambos ticks son livianos y entran holgados en 1 min.
        job_condor_tick(broker, butterfly_conn, settings)

    def run_learning_review() -> None:
        job_learning_review(conn, settings, broker=broker)

    def run_backtest_review() -> None:
        job_backtest_review(broker, conn, settings)

    def run_premarket_digest() -> None:
        job_premarket_digest(broker, conn, symbols, settings, anthropic_api_key, finnhub_api_key=finnhub_api_key, fred_api_key=fred_api_key)

    def run_real_trade_detection() -> None:
        # Conexión propia: corre en el executor 'trades', un hilo distinto del escaneo pesado.
        job_detect_real_trades(broker, trades_conn, settings, anthropic_api_key, finnhub_api_key=finnhub_api_key)

    def run_live_maintenance() -> None:
        # Conexión propia: corre en el executor 'live', un hilo distinto del escaneo pesado.
        job_live_position_maintenance(broker, live_conn, settings)

    def run_roll_tick() -> None:
        # Conexión propia, executor liviano: unas pocas posiciones, nunca detrás del escaneo pesado.
        job_roll_tick(broker, live_conn, settings)

    def run_peticiones_manuales() -> None:
        """Atiende los pedidos de corrida que deja el dashboard (auditoria 2026-08-22).

        Los botones "Correr robot ahora" y "Analisis completo" ejecutaban los jobs DENTRO del
        proceso de Streamlit. Esos jobs abren y cierran posiciones con plata real, y el lock que los
        protege es de proceso, asi que no cruzaba al dashboard: un clic con el robot andando podia
        mandar la misma orden dos veces. Ahora el boton solo deja el pedido y la corrida ocurre aca,
        en el executor 'default' -- un solo worker, max_instances=1, un solo escritor de la base."""
        try:
            tipo = repo.tomar_corrida_pendiente(chat_conn)
        except Exception:
            logger.debug("No se pudo leer el pedido de corrida manual", exc_info=True)
            return
        if not tipo:
            return
        logger.warning("Pedido de corrida manual desde el dashboard: %s", tipo)
        scheduler.add_job(
            run_job if tipo == "completa" else run_robot_scan_forzado,
            id="corrida_manual", replace_existing=True, executor="default",
            next_run_time=datetime.now(),
        )

    def run_robot_scan_forzado() -> None:
        # force=True: el pedido es explicito del usuario, corre aunque el mercado este cerrado.
        job_robot_scan(broker, conn, symbols, settings,
                       finnhub_api_key=finnhub_api_key, fred_api_key=fred_api_key, force=True)

    def run_token_watch() -> None:
        # Usa la conexion del chat porque comparte su executor liviano: solo lee/escribe una fila de
        # `robot_flags` y nunca debe quedar detras del escaneo pesado de puts.
        token_watch.check_and_warn(chat_conn)

    digest_h, digest_m = _hh_mm(settings.scheduler.premarket_digest_time)
    open_h, open_m = _hh_mm(settings.scheduler.market_open_snapshot_time)
    close_h, close_m = _hh_mm(settings.scheduler.market_close_snapshot_time)
    start_h, _ = _hh_mm(settings.scheduler.market_hours_start)
    end_h, _ = _hh_mm(settings.scheduler.market_hours_end)

    scheduler.add_job(
        run_premarket_digest,
        CronTrigger(day_of_week="mon-fri", hour=digest_h, minute=digest_m, timezone=settings.scheduler.timezone),
        id="premarket_digest",
    )
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
    # Escaneo RÁPIDO del robot (usuario 2026-08: "no quiero dar tantos pasos cada vez"): corre
    # solo indicadores + entrada del robot + marcado, sin la capa de alertas/IA, cada pocos
    # minutos durante el horario de mercado. Es lo que hace que el robot opere SOLO con solo tener
    # el scheduler prendido (doble clic en "Iniciar Robot.command"), sin abrir el dashboard.
    scheduler.add_job(
        run_robot_scan,
        CronTrigger(
            day_of_week="mon-fri",
            hour=f"{start_h}-{end_h}",
            minute=f"*/{settings.scheduler.robot_scan_interval_minutes}",
            timezone=settings.scheduler.timezone,
        ),
        id="robot_scan",
    )
    # Job RÁPIDO del chat (usuario 2026-08-11: "demora mucho en enviar la orden"): cada 15 segundos durante
    # el mercado, procesa SOLO las sugerencias del chat que el usuario aprobó → la orden sale en segundos, no
    # al final del escaneo del universo. Hilo/conexión propios; el lock de live_engine evita choques con el
    # escaneo. Liviano: si no hay nada aprobado, no hace nada.
    scheduler.add_job(
        run_chat_orders,
        CronTrigger(
            day_of_week="mon-fri",
            hour=f"{start_h}-{end_h}",
            minute="*",
            second="*/15",
            timezone=settings.scheduler.timezone,
        ),
        id="chat_orders_tick",
        executor="chat",
    )
    # Pestaña Operaciones (pedido 2026-07-27): cadencia propia, mucho más seguida que
    # periodic_poll — solo diffea posiciones, no corre el análisis pesado de oportunidades.
    scheduler.add_job(
        run_real_trade_detection,
        CronTrigger(
            day_of_week="mon-fri",
            hour=f"{start_h}-{end_h}",
            minute=f"*/{settings.scheduler.real_trade_poll_interval_minutes}",
            timezone=settings.scheduler.timezone,
        ),
        id="real_trade_detection",
        executor="trades",   # hilo propio: no lo frena el escaneo pesado de puts (usuario 2026-08-17)
    )
    # MANTENIMIENTO de posiciones REALES (usuario 2026-08-19): cierre por objetivo de ganancia,
    # re-precio y email de apertura. Cada minuto, hilo propio. Es el job que decide cuándo te llevás
    # la ganancia, así que NO puede depender de que el escaneo del universo haya terminado.
    scheduler.add_job(
        run_live_maintenance,
        CronTrigger(
            day_of_week="mon-fri",
            hour=f"{start_h}-{end_h}",
            minute="*",
            timezone=settings.scheduler.timezone,
        ),
        id="live_position_maintenance",
        executor="live",
    )
    # ROLL de puts cortos (usuario 2026-09-09/14/15). Cada 3 minutos durante el mercado, en el
    # mismo hilo liviano que el mantenimiento: son unas pocas posiciones abiertas, no el universo.
    #
    # Cada 3 y no cada minuto porque lo que hace es caro (pide la cadena de opciones hasta 90 días)
    # y no es urgente al segundo: se dispara faltando 20 días o menos para el vencimiento, no en una
    # ventana de minutos. Y el tick empieza mandando lo que el usuario ya aprobó, así una aprobación
    # nunca espera más de tres minutos para salir.
    #
    # El job se registra siempre; si `roll.enabled` está en false no hace nada y ni siquiera le pega
    # a Schwab.
    scheduler.add_job(
        run_roll_tick,
        CronTrigger(
            day_of_week="mon-fri",
            hour=f"{start_h}-{end_h}",
            minute="*/3",
            timezone=settings.scheduler.timezone,
        ),
        id="roll_tick",
        executor="live",
    )
    # Estrategia 2 — Iron Butterfly 0DTE intradía (usuario 2026-08): tick de 1 minuto durante el
    # mercado. El job no hace nada si la estrategia está apagada, así que siempre se registra;
    # prenderla es solo `intraday_butterfly.enabled: true` en el settings.
    scheduler.add_job(
        run_butterfly_tick,
        CronTrigger(
            day_of_week="mon-fri",
            hour=f"{start_h}-{end_h}",
            minute="*",
            timezone=settings.scheduler.timezone,
        ),
        id="butterfly_tick",
        executor="butterfly",  # hilo propio: no lo frena el escaneo pesado de puts
    )
    # Iron Condor 0DTE (Estrategia 3, 2026-08-05): mismo executor/conexión que el butterfly (los dos
    # intradía SPX serializados en un hilo). Prenderla es `intraday_condor.enabled: true`.
    scheduler.add_job(
        run_condor_tick,
        CronTrigger(
            day_of_week="mon-fri",
            hour=f"{start_h}-{end_h}",
            minute="*",
            timezone=settings.scheduler.timezone,
        ),
        id="condor_tick",
        executor="butterfly",
    )
    # Revisión de aprendizaje (Etapa 2, usuario 2026-08): una vez por día, 20 min DESPUÉS del cierre
    # del mercado, cuando ya están los resultados y tu feedback del día. Ajusta pesos chicos solo y
    # deja propuestas para los grandes.
    end_h, end_m = _hh_mm(settings.scheduler.market_hours_end)
    scheduler.add_job(
        run_learning_review,
        CronTrigger(day_of_week="mon-fri", hour=end_h, minute=min(59, end_m + 20), timezone=settings.scheduler.timezone),
        id="learning_review",
    )
    # Pedidos de corrida manual del dashboard: cada 15s, junto al job del chat (los dos son
    # baratisimos y viven en el mismo hilo liviano). TODOS los dias y a toda hora: el usuario puede
    # querer correr un analisis con el mercado cerrado, que es justo para lo que sirve el boton.
    scheduler.add_job(
        run_peticiones_manuales,
        CronTrigger(second="*/15", timezone=settings.scheduler.timezone),
        id="peticiones_manuales",
        executor="chat",
    )
    # Vigilante del token de Schwab (usuario 2026-08-18: "me avisas 24 horas antes"). Cada hora, TODOS
    # los dias — a proposito, no solo en horario de mercado: los 7 dias del refresh_token corren
    # tambien de noche y los fines de semana, y el 18/08 vencio justo fuera de hora. Avisar un sabado
    # es lo que te deja reconectar tranquilo antes de que abra el lunes. Es un job baratisimo: lee un
    # archivo JSON y una fila de `robot_flags`.
    scheduler.add_job(
        run_token_watch,
        CronTrigger(minute=7, timezone=settings.scheduler.timezone),
        id="schwab_token_watch",
        executor="chat",
    )
    # Backtest AUTOMÁTICO SEMANAL (usuario 2026-08-07): domingo 18:00, mercado cerrado. Corre el
    # backtest sobre la watchlist y deja el hallazgo en Aprendizaje + te avisa.
    scheduler.add_job(
        run_backtest_review,
        CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=settings.scheduler.timezone),
        id="backtest_review",
    )
    return scheduler
