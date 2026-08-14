from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime

from options_advisor.alerts import notifier
from options_advisor.alerts.digest import build_premarket_digest_text
from options_advisor.alerts.engine import process_symbol_alerts
from options_advisor.alerts.real_trades import detect_and_alert_real_trades
from options_advisor.alerts.risk_calendar import build_proactive_risk_warnings, is_high_risk_event_day
from options_advisor.broker.base import BrokerClient
from options_advisor.backtest import engine as backtest_engine
from options_advisor.config import Settings, load_priority_watchlist_symbols
from options_advisor.indicators.pipeline import analyze_symbol
from options_advisor.market_context import economic_calendar, finnhub_client, fred_client, kalshi_client
from options_advisor.scheduler.market_calendar import is_market_day, market_session
from options_advisor.simulator import butterfly_engine
from options_advisor.simulator import engine as simulator_engine
from options_advisor.simulator import iron_condor_engine
from options_advisor.simulator import learning
from options_advisor.storage import repository as repo
from options_advisor.storage.models import MacroSnapshot, NewsItem, Notification

logger = logging.getLogger(__name__)

MIN_SHARES_FOR_COVERED_STRATEGIES = 100  # 1 contrato de opción cubre 100 acciones


def _order_symbols_by_day_drop(broker: BrokerClient, symbols: list[str]) -> list[str]:
    """Ordena los símbolos de MÁS caído a menos caído en el día (usuario 2026-08-10: "elegí la más caída
    de mi lista, no la primera"). Como el motor abre la PRIMERA que califica, procesar la más caída primero
    hace que el robot elija la mejor oportunidad de venta de puts (vender en la debilidad). Una sola llamada
    batch de quotes; si falla, devuelve el orden original (nunca rompe el escaneo)."""
    try:
        quotes = broker.get_quotes(symbols)
    except Exception:
        logger.debug("No se pudo ordenar por caída del día; se usa el orden original", exc_info=True)
        return symbols

    def _drop(sym: str) -> float:
        q = quotes.get(sym)
        chg = getattr(q, "net_change_pct", None) if q else None
        return chg if chg is not None else 999.0  # sin dato → al final

    return sorted(symbols, key=_drop)

# Cada corrida evalúa los 3 perfiles fijos, no solo el activo en investor_profile — el
# selector de perfil en el dashboard (Alertas/Configuración) pasó a ser un FILTRO sobre
# alertas ya generadas, no un disparador de análisis (pedido explícito del usuario
# 2026-07-24: "Correr análisis ahora" debe cubrir los 3 perfiles de una sola corrida).
RISK_LEVELS = ("conservador", "moderado", "agresivo")


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
                cpi_yoy_date=macro["cpi_yoy_date"],
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


def _refresh_news_for_symbol(conn: sqlite3.Connection, symbol: str, today: date, news_rows: list[dict]) -> None:
    """Persiste las noticias ya traídas de Finnhub (una sola vez por símbolo en
    `_run_full_analysis`, ver ahí) — separado de la llamada a la API para no pedirlas dos
    veces por símbolo (antes: una acá y otra en process_symbol_alerts para el narrador).
    Falla aislada (igual que el contexto macro): un problema al persistir nunca debe tumbar
    el análisis de indicadores/alertas del símbolo."""
    try:
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
            for row in news_rows
            if row.get("headline") and row.get("url")
        ]
        repo.insert_news_items(conn, items)
    except Exception:
        logger.exception("Fallo al guardar noticias de %s; se continúa con el resto del análisis", symbol)


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
    """Macro + noticias + indicadores + alertas (para los 3 perfiles de riesgo) de todos los
    símbolos — el cuerpo real de una corrida, compartido por el polling regular y el digest
    pre-apertura (job_premarket_digest necesita saber qué alertas salieron de SU corrida, no
    solo que el job terminó). Un fallo en un símbolo no tumba el resto (Sección 6 del plan de
    Fase 1). Devuelve las alertas nuevas generadas en esta corrida (lista vacía si no hubo
    ninguna).

    Lo caro por símbolo (quote/historial/cadena de opciones en analyze_symbol, earnings y
    noticias de Finnhub) se pide UNA sola vez y se reusa para los 3 perfiles — ni Finnhub ni
    el indicator_snapshot del día se triplican, solo se triplica lo que realmente depende del
    perfil (selección de strikes, scoring, narración de Claude)."""
    _refresh_macro_snapshot(conn, today, finnhub_api_key, fred_api_key)
    # Etapa 3 (aplicar lo aprendido): el cerebro usa los pesos ya ajustados por el aprendizaje.
    settings = settings.model_copy(update={"simulator": learning.load_effective_simulator(conn, settings.simulator)})

    # Sección Fed/FRED (pedido 2026-07-26, "bloqueo de días de riesgo CPI/NFP"): se calcula UNA
    # vez por corrida (no por símbolo) contra los eventos macro recién refrescados arriba —
    # nunca bloquea alertas/posiciones ya existentes, solo la generación de candidatos nuevos.
    macro = repo.get_latest_macro_snapshot(conn)
    upcoming_events = json.loads(macro["upcoming_events_json"]) if macro and macro["upcoming_events_json"] else []
    block_new_candidates = settings.strategy.block_new_candidates_on_high_risk_days and is_high_risk_event_day(upcoming_events, today)
    if block_new_candidates:
        logger.info("%s es día de riesgo alto (CPI/NFP/FOMC) — no se generan candidatos nuevos en esta corrida", today)

    # Una sola consulta de posiciones reales por corrida (no por símbolo) — habilita Covered
    # Call/Collar con la tenencia REAL de la cuenta Schwab en vez de la tabla interna
    # `assigned_positions` (pensada para trackear asignación de CSP propia, hoy sin UI que la
    # llene). {} en modo mock o si falla la consulta (ver broker/base.py::get_all_share_positions).
    share_positions = broker.get_all_share_positions()

    symbols = _order_symbols_by_day_drop(broker, symbols)  # la más caída primero (mejor entrada de put)
    new_alerts: list[dict] = []
    for symbol in symbols:
        try:
            open_positions = repo.get_open_assigned_positions(conn, symbol)
            has_shares = share_positions.get(symbol, 0) >= MIN_SHARES_FOR_COVERED_STRATEGIES
            analysis = analyze_symbol(broker, conn, symbol, settings, finnhub_api_key=finnhub_api_key)
            recent_news = finnhub_client.get_recent_news(symbol, analysis.snapshot.snapshot_date, finnhub_api_key)
            _refresh_news_for_symbol(conn, symbol, today, recent_news)

            # Completa los datos de mercado de posiciones abiertas por el código viejo (que no los
            # guardó) reusando este mismo análisis — sin pedir nada extra al broker (usuario 2026-08-05).
            try:
                simulator_engine.enrich_open_decision(conn, symbol, analysis)
            except Exception:
                logger.debug("Robot: no se pudo enriquecer la decisión abierta de %s", symbol, exc_info=True)

            symbol_alert_count = 0
            for risk_level in RISK_LEVELS:
                alerts = process_symbol_alerts(
                    conn,
                    analysis,
                    settings,
                    block_new_candidates=block_new_candidates,
                    has_open_assigned_position=len(open_positions) > 0 or has_shares,
                    anthropic_api_key=anthropic_api_key,
                    finnhub_api_key=finnhub_api_key,
                    risk_level=risk_level,
                    recent_news=recent_news,
                    broker=broker,
                )
                symbol_alert_count += len(alerts)
                new_alerts.extend(alerts)
            logger.info("%s: iv_rank=%s, %d alerta(s) nueva(s) (3 perfiles)", symbol, analysis.snapshot.iv_rank, symbol_alert_count)

            # Simulador de Trading Automático (paper trading, pedido 2026-08-02): reusa el
            # snapshot/cadena/historial que analyze_symbol ya calculó arriba, no pide nada
            # nuevo al broker. Aislado en su propio try — un fallo acá nunca debe perder las
            # alertas de sugerencias ya generadas para este símbolo.
            try:
                if market_session() == "abierto":  # nunca abrir con el mercado cerrado (datos stale)
                    simulator_engine.process_symbol_entry(
                        conn, symbol, analysis.snapshot, analysis.chain, analysis.price_history, settings, broker,
                        day_change_pct=analysis.quote.net_change_pct,
                    )
            except Exception:
                logger.exception("Robot: fallo al evaluar entrada de %s; se continúa con el resto", symbol)
        except Exception:
            logger.exception("Fallo al procesar %s; se continúa con el resto de los símbolos", symbol)

    # Mark-to-market diario de TODAS las posiciones simuladas abiertas (Simulador de Trading
    # Automático) — una sola vez por corrida completa, no por símbolo: una posición simulada
    # puede estar sobre un símbolo que ya no forma parte de la watchlist evaluada arriba.
    try:
        simulator_engine.mark_and_close_positions(conn, broker, settings, today)
    except Exception:
        logger.exception("Simulador: fallo al marcar posiciones abiertas hoy")

    # Cierre REAL: recompra las posiciones reales del robot cuando cumplen las mismas reglas del simulador
    # (usuario 2026-08-10). Aislado: nunca tumba el escaneo. Solo actúa si el real está encendido.
    try:
        from options_advisor.execution import live_engine as _live_engine
        _live_engine.reprice_resting_orders(conn, broker, settings, today)  # seguir negociando lo que quedó puesto
    except Exception:
        logger.exception("Live-reprice: fallo al re-preciar órdenes en espera")
    try:
        from options_advisor.execution import live_engine as _live_engine
        _live_engine.close_real_positions(conn, broker, settings, today)
    except Exception:
        logger.exception("Live-close: fallo al cerrar posiciones reales")
    # Asesor AI (usuario 2026-08-10): manda las sugerencias que el usuario APROBÓ, por el mismo guardián.
    try:
        from options_advisor.execution import live_engine as _live_engine
        _live_engine.process_approved_ai_orders(conn, broker, settings, today)
    except Exception:
        logger.exception("Asesor: fallo al procesar sugerencias aprobadas")
    # Email de apertura idempotente (usuario 2026-08-11): 1 email por cada fill, nunca se pierde.
    try:
        from options_advisor.execution import live_engine as _live_engine
        _live_engine.send_pending_open_emails(conn, settings)
    except Exception:
        logger.exception("Live-email: fallo al mandar emails de apertura pendientes")

    return new_alerts


def _run_robot_scan(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    symbols: list[str],
    settings: Settings,
    today: date,
    finnhub_api_key: str | None,
    fred_api_key: str | None,
) -> int:
    """Escaneo LIVIANO solo para el robot (paper trading). Comparado con `_run_full_analysis`
    salta lo que el robot NO usa y que domina el tiempo de una corrida: las noticias de Finnhub
    por símbolo, y sobre todo `process_symbol_alerts` × 3 perfiles de riesgo, que hace una
    narración de Claude por perfil (≈ 3 llamadas al LLM por símbolo → cientos en el universo).
    Solo calcula indicadores (`analyze_symbol`), evalúa la entrada del robot y marca/cierra las
    posiciones abiertas. Motivo: usuario 2026-08 "el análisis tarda demasiado" — el robot debe
    operar rápido sin esperar la capa de alertas. Devuelve cuántos símbolos evaluó."""
    _refresh_macro_snapshot(conn, today, finnhub_api_key, fred_api_key)
    # Etapa 3 (aplicar lo aprendido): el cerebro usa los pesos ya ajustados por el aprendizaje.
    settings = settings.model_copy(update={"simulator": learning.load_effective_simulator(conn, settings.simulator)})
    symbols = _order_symbols_by_day_drop(broker, symbols)  # la más caída primero (mejor entrada de put)
    evaluated = 0
    for symbol in symbols:
        try:
            analysis = analyze_symbol(broker, conn, symbol, settings, finnhub_api_key=finnhub_api_key)
            # Completa los datos de mercado de posiciones abiertas por el código viejo (usuario
            # 2026-08-05) reusando este análisis — corre siempre, aun con el mercado cerrado.
            try:
                simulator_engine.enrich_open_decision(conn, symbol, analysis)
            except Exception:
                logger.debug("Robot: no se pudo enriquecer la decisión abierta de %s", symbol, exc_info=True)
            try:
                if market_session() == "abierto":  # nunca abrir con el mercado cerrado (datos stale)
                    simulator_engine.process_symbol_entry(
                        conn, symbol, analysis.snapshot, analysis.chain, analysis.price_history, settings, broker,
                        day_change_pct=analysis.quote.net_change_pct,
                    )
            except Exception:
                logger.exception("Robot: fallo al evaluar entrada de %s; se continúa con el resto", symbol)
            evaluated += 1
        except Exception:
            logger.exception("Robot: fallo al calcular indicadores de %s; se continúa con el resto", symbol)

    try:
        simulator_engine.mark_and_close_positions(conn, broker, settings, today)
    except Exception:
        logger.exception("Simulador: fallo al marcar posiciones abiertas hoy")

    # Cierre REAL: recompra las posiciones reales del robot cuando cumplen las mismas reglas del simulador
    # (usuario 2026-08-10). Aislado: nunca tumba el escaneo. Solo actúa si el real está encendido.
    try:
        from options_advisor.execution import live_engine as _live_engine
        _live_engine.reprice_resting_orders(conn, broker, settings, today)  # seguir negociando lo que quedó puesto
    except Exception:
        logger.exception("Live-reprice: fallo al re-preciar órdenes en espera")
    try:
        from options_advisor.execution import live_engine as _live_engine
        _live_engine.close_real_positions(conn, broker, settings, today)
    except Exception:
        logger.exception("Live-close: fallo al cerrar posiciones reales")
    # Asesor AI (usuario 2026-08-10): manda las sugerencias que el usuario APROBÓ, por el mismo guardián.
    try:
        from options_advisor.execution import live_engine as _live_engine
        _live_engine.process_approved_ai_orders(conn, broker, settings, today)
    except Exception:
        logger.exception("Asesor: fallo al procesar sugerencias aprobadas")
    # Email de apertura idempotente (usuario 2026-08-11): 1 email por cada fill, nunca se pierde.
    try:
        from options_advisor.execution import live_engine as _live_engine
        _live_engine.send_pending_open_emails(conn, settings)
    except Exception:
        logger.exception("Live-email: fallo al mandar emails de apertura pendientes")

    logger.info("Escaneo robot (rápido): %d/%d símbolos evaluados", evaluated, len(symbols))
    return evaluated


def job_robot_scan(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    symbols: list[str],
    settings: Settings,
    finnhub_api_key: str | None = None,
    fred_api_key: str | None = None,
    force: bool = False,
) -> None:
    """Job liviano solo-robot: indicadores + entrada del robot + marcado, sin la capa de
    alertas/narración. Es lo que dispara el botón "Correr robot ahora" y lo que conviene correr
    seguido en el scheduler cuando lo único que importa es que el robot opere rápido."""
    today = date.today()
    if not force and not is_market_day(today):
        logger.info("%s no es día de mercado, se salta el escaneo del robot", today)
        return
    if not force and market_session() != "abierto":
        return  # fuera de horario no se escanea (no se abre ni se re-marca con datos stale)
    _run_robot_scan(broker, conn, symbols, settings, today, finnhub_api_key, fred_api_key)


def job_process_chat_orders(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    settings: Settings,
    force: bool = False,
) -> None:
    """Job RÁPIDO del chat (usuario 2026-08-11: 'demora mucho en enviar la orden'): procesa SOLO las
    sugerencias del chat que el usuario APROBÓ, cada ~15s, en su propio hilo y con su propia conexión —
    así la orden sale en segundos en vez de esperar al FINAL del escaneo del universo (que tarda minutos).
    El escaneo del robot también las procesa como red de seguridad; el lock `_LIVE_ORDER_LOCK` de
    live_engine garantiza que las dos NUNCA corran sobre la misma orden a la vez (sin doble envío). Solo
    con el mercado abierto, igual que el escaneo (no abre con datos stale)."""
    today = date.today()
    if not force and not is_market_day(today):
        return
    if not force and market_session() != "abierto":
        return
    try:
        from options_advisor.execution import live_engine as _live_engine
        _live_engine.process_approved_ai_orders(conn, broker, settings, today)
    except Exception:
        logger.exception("Chat-orders: fallo al procesar sugerencias aprobadas del chat")


def job_poll_and_analyze(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    symbols: list[str],
    settings: Settings,
    anthropic_api_key: str | None,
    finnhub_api_key: str | None = None,
    fred_api_key: str | None = None,
    force: bool = False,
) -> None:
    """Job principal del scheduler: calcula indicadores y evalúa alertas para todos los
    símbolos. El mismo job corre en cada disparo programado (apertura, cada 30 min, cierre) —
    la última corrida del día deja el snapshot "oficial" gracias al upsert por
    (symbol, snapshot_date).

    `force=True` (botón "Correr análisis ahora" de la página General, pedido 2026-08-02):
    salta el chequeo de día de mercado — un click explícito del usuario debe correr YA, sin
    importar si hoy es fin de semana/feriado (bug real encontrado 2026-08-02: el botón mostraba
    "Listo" igual aunque no hubiera corrido nada, incluido el Simulador de Trading Automático,
    porque este chequeo cortaba ANTES de llegar a `_run_full_analysis`). El polling automático
    del scheduler (sin `force`) sigue saltándose esos días, para no gastar llamadas de API sin
    necesidad."""
    today = date.today()
    if not force and not is_market_day(today):
        logger.info("%s no es día de mercado, se salta el polling", today)
        return

    _run_full_analysis(broker, conn, symbols, settings, today, anthropic_api_key, finnhub_api_key, fred_api_key)


def job_butterfly_tick(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    settings: Settings,
    force: bool = False,
) -> None:
    """Tick del motor Iron Butterfly 0DTE (Estrategia 2): corre cada minuto durante el mercado
    y delega en butterfly_engine.process_butterfly_cycle (marcar/cerrar + evaluar/abrir). No hace
    nada si la estrategia está apagada (settings.intraday_butterfly.enabled=false). Liviano: no
    toca alertas/IA ni el robot de puts."""
    if not settings.intraday_butterfly.enabled:
        return
    today = date.today()
    if not force and not is_market_day(today):
        return
    if not force and market_session() != "abierto":
        return  # 0DTE: solo opera con el mercado abierto
    try:
        butterfly_engine.process_butterfly_cycle(conn, broker, settings, today)
    except Exception:
        logger.exception("Butterfly: fallo en el tick del motor 0DTE")


def job_condor_tick(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    settings: Settings,
    force: bool = False,
) -> None:
    """Tick del motor Iron Condor 0DTE (Estrategia 3, para días calmos): corre cada minuto durante
    el mercado y delega en iron_condor_engine.process_condor_cycle (marcar/cerrar + evaluar/abrir).
    No hace nada si está apagada (settings.intraday_condor.enabled=false). Liviano."""
    if not settings.intraday_condor.enabled:
        return
    today = date.today()
    if not force and not is_market_day(today):
        return
    if not force and market_session() != "abierto":
        return  # 0DTE: solo con el mercado abierto
    try:
        iron_condor_engine.process_condor_cycle(conn, broker, settings, today)
    except Exception:
        logger.exception("Condor: fallo en el tick del motor 0DTE")
    # Paso a REAL del condor (usuario 2026-08-13): el MISMO cerebro, pero con dinero real. Corre justo
    # después del papel, en el mismo tick/hilo. Apagado por default (intraday_condor.live_enabled=false
    # + live_trading), así que si no está prendido no hace nada. Aislado: su fallo no toca el papel.
    try:
        from options_advisor.execution import live_condor_engine
        live_condor_engine.process_real_condor_cycle(conn, broker, settings, today)
    except Exception:
        logger.exception("Condor-real: fallo en el tick del motor real")


def job_learning_review(conn: sqlite3.Connection, settings: Settings, force: bool = False, broker=None) -> None:
    """Revisión de aprendizaje (Etapa 2), una vez por día tras el cierre: cruza cada decisión con
    tu feedback y su resultado real, ajusta solo los pesos con cambios chicos y deja propuestas para
    los grandes. Aislado — nunca tumba el scheduler."""
    today = date.today()
    if not force and not is_market_day(today):
        return
    # Calibra el margen del simulador al margen REAL de tu broker (maintenanceRequirement de tus
    # posiciones cortas) — así el colateral y el anualizado coinciden con tu cuenta (usuario 2026-08-06).
    if broker is not None:
        try:
            cal = learning.calibrate_broker_margin_factor(conn, broker, settings.simulator)
            logger.info("Calibración de margen al broker: %s", cal)
        except Exception:
            logger.exception("Calibración de margen: fallo al calibrar contra el broker")
    try:
        result = learning.review(conn, settings.simulator)
        logger.info("Aprendizaje (puts): %s", result.get("summary", ""))
    except Exception:
        logger.exception("Aprendizaje: fallo en la revisión diaria de puts")
    try:
        bf = learning.review_butterfly(conn, settings.intraday_butterfly)
        logger.info("Aprendizaje (iron): %s", bf.get("summary", ""))
    except Exception:
        logger.exception("Aprendizaje: fallo en la revisión diaria del iron butterfly")
    # Iron Condor (usuario 2026-08-14): aprende de papel y real JUNTOS y ajusta delta de los cortos,
    # umbral de día calmo, crédito mínimo, tope de VIX en suba y objetivo de ganancia. El stop-loss
    # solo lo aprieta por su cuenta; aflojarlo siempre pasa por tu aprobación.
    try:
        cd = learning.review_condor(conn, settings.intraday_condor)
        logger.info("Aprendizaje (condor): %s", cd.get("summary", ""))
    except Exception:
        logger.exception("Aprendizaje: fallo en la revisión diaria del iron condor")
    # Cruza tus votos POR PARÁMETRO (cada casillero 👍/😐/👎) con los pesos del cerebro y deja
    # propuestas de ajuste para que las apruebes (usuario 2026-08-06).
    try:
        pf = learning.review_param_feedback(conn, settings.simulator)
        logger.info("Aprendizaje (voto por parámetro): %d propuesta(s) sobre %d dimensión(es)",
                    len(pf.get("proposed", [])), pf.get("dimensions_with_votes", 0))
    except Exception:
        logger.exception("Aprendizaje: fallo al cruzar el voto por parámetro")
    # Estudia TUS operaciones reales para aprender tu estilo (tickers favoritos, cobertura, delta,
    # patrones) — usuario 2026-08-05. Por ahora informativo (se ve en Aprendizaje); base para alinear.
    try:
        profile = learning.analyze_real_trade_profile(conn)
        logger.info("Aprendizaje (perfil real): %s", learning.real_trade_profile_summary(profile))
    except Exception:
        logger.exception("Aprendizaje: fallo al analizar el perfil de operaciones reales")

    # Al final del día, tras aprender de TU feedback + los resultados: si al robot le quedaron
    # CONSULTAS (cambios grandes que necesita que apruebes), avisarte (usuario 2026-08-05: "si tiene
    # consulta que me la haga al final del día"). Las chicas ya las aplicó solo; estas son para vos.
    try:
        pending = repo.get_pending_learning_proposals(conn)
        if pending:
            n = len(pending)
            msg = (f"El robot terminó de aprender del día y tiene {n} consulta"
                   f"{'s' if n != 1 else ''} para vos. Abrí el dashboard → Aprendizaje para aprobar o rechazar.")
            notifier.send_native(msg, title="Lokshn", subtitle="Consultas de aprendizaje")
            notifier.send_text(f"🧠 {msg}")
            logger.info("Aprendizaje: %d consulta(s) pendiente(s) — usuario notificado", n)
    except Exception:
        logger.exception("Aprendizaje: fallo al notificar las consultas pendientes")


def job_backtest_review(broker: BrokerClient, conn: sqlite3.Connection, settings: Settings, force: bool = False) -> None:
    """Backtest AUTOMÁTICO SEMANAL (usuario 2026-08-07): corre el backtest de naked puts sobre tu
    watchlist a varios deltas en ~5 años de histórico, guarda el hallazgo (qué delta rindió mejor) en
    Aprendizaje y te avisa. NO cambia parámetros solo — es un informe para que vos decidas. Aislado:
    cualquier fallo se loguea y no tumba el scheduler."""
    try:
        symbols = load_priority_watchlist_symbols()
        if not symbols:
            return
        sim = learning.load_effective_simulator(conn, settings.simulator)
        bars_by = {}
        for s in symbols:
            try:
                bars = broker.get_price_history(s, 365 * 5 + 40)
            except Exception:
                continue
            if bars and len(bars) >= 60:
                bars_by[s] = bars
        if not bars_by:
            logger.warning("Backtest semanal: no se pudo cargar histórico de la watchlist")
            return
        ranking = sorted(backtest_engine.sweep_delta(bars_by, sim), key=lambda r: r["total_pnl"], reverse=True)
        best = ranking[0] if ranking else None
        if not best:
            return
        resumen = (f"Backtest semanal (5 años, {len(bars_by)} símbolos): el delta objetivo que MÁS rindió "
                   f"fue {best['delta']:.2f} (win {best['win_rate']:.1f}%, P&L ${best['total_pnl']:,.0f}, "
                   f"anualizado {best['avg_annualized']:.0f}%). Miralo en Aprendizaje.")
        repo.insert_learning_report(conn, best["n"], resumen,
                                    json.dumps({"source": "weekly_backtest", "ranking": ranking}, default=str))
        notifier.send_native(resumen, title="Lokshn", subtitle="Backtest semanal")
        notifier.send_text(f"📊 {resumen}")
        logger.info("Backtest semanal: %s", resumen)
    except Exception:
        logger.exception("Backtest semanal: fallo al correr el backtest de aprendizaje")


def job_detect_real_trades(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    settings: Settings,
    anthropic_api_key: str | None,
    finnhub_api_key: str | None = None,
) -> None:
    """Job liviano y SEPARADO de `job_poll_and_analyze` (pedido 2026-07-27: la detección de
    operaciones reales debe verse reflejada casi en tiempo real, no esperar los 30 minutos del
    análisis pesado de oportunidades) — solo diffea posiciones cortas de opciones contra el
    snapshot de la corrida anterior, sin indicadores/scoring/narración de candidatos por
    símbolo ni refresh de contexto macro. Pensado para correr cada pocos minutos
    (`settings.scheduler.real_trade_poll_interval_minutes`, ver `scheduler/runner.py`)."""
    today = date.today()
    if not is_market_day(today):
        return
    try:
        share_positions = broker.get_all_share_positions()
        detect_and_alert_real_trades(broker, conn, settings, today, share_positions, anthropic_api_key, finnhub_api_key)
    except Exception:
        logger.exception("Fallo al detectar operaciones reales")


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

    # Sección Fed/FRED ("alertas proactivas", pedido 2026-07-26): a diferencia del resumen de
    # arriba (solo eventos de HOY), esto avisa 2 y 1 día ANTES de un evento de riesgo alto, para
    # planificar vencimientos nuevos con anticipación. Dedup por kind+title exactos (la fecha va
    # en el título) — este job corre una vez por día de mercado, pero por las dudas si se corre
    # más de una vez el mismo día no duplica el aviso.
    for warning in build_proactive_risk_warnings(upcoming_events, today):
        title = f"⚠️ En {warning['days_until']} día(s): {warning['label']} ({warning['date'].isoformat()})"
        if repo.notification_exists(conn, kind="risk_event_proactive", title=title):
            continue
        body = (
            f"{warning['label']} el {warning['date'].isoformat()} — evento de riesgo alto (CPI/NFP/FOMC). "
            "Considerá evitar aperturas nuevas con vencimiento cercano a esa fecha, o revisar la cobertura "
            "de las que ya tenés."
        )
        repo.insert_notification(
            conn, Notification(kind="risk_event_proactive", title=title, body=body, created_at=datetime.now())
        )
