from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import OptionChain, PriceBar
from options_advisor.config import Settings
from options_advisor.indicators.intraday import compute_vwap
from options_advisor.scheduler.market_calendar import market_session
from options_advisor.simulator import entry_rules, positions
from options_advisor.storage import repository as repo
from options_advisor.storage.models import IndicatorSnapshot

logger = logging.getLogger(__name__)

# Rango de vencimientos a pedir al marcar posiciones abiertas — arranca en 1 día (una posición
# abierta puede estar a días de vencer) y llega a 95 para cubrir las de 90 DTE.
MARK_CHAIN_FETCH_RANGE_DAYS = (1, 95)


def ensure_account(conn: sqlite3.Connection, settings: Settings) -> None:
    if repo.get_simulated_account(conn) is None:
        repo.init_simulated_account(conn, settings.simulator.initial_capital, datetime.now())


def _account_state(conn: sqlite3.Connection) -> tuple[float, float, float, float]:
    """Devuelve (cash, garantía comprometida, P&L no realizado, EQUITY). El equity resta el
    PASIVO de cada opción vendida (su valor actual) en vez de sumar el no-realizado: el cash ya
    incluye la prima cobrada al abrir, así que sumar el no-realizado la contaba dos veces (bug
    crítico de la auditoría — hacía que cada cierre ganador pareciera perder una prima). Con esta
    fórmula el equity es continuo al cerrar. equity = cash + Σ(garantía − prima_cobrada + no_realizado)."""
    account = repo.get_simulated_account(conn)
    cash = account["cash"]
    open_positions = repo.get_open_simulated_positions(conn)
    committed = 0.0
    unrealized = 0.0
    equity = cash
    for p in open_positions:
        u = p["last_unrealized_pnl"] or 0.0
        premium_collected = p["entry_premium"] * positions.CONTRACT_MULTIPLIER * p["quantity"]
        committed += p["collateral"]
        unrealized += u
        equity += p["collateral"] - premium_collected + u
    return round(cash, 2), round(committed, 2), round(unrealized, 2), round(equity, 2)


def _account_equity(conn: sqlite3.Connection) -> float:
    return _account_state(conn)[3]


def _macro_event_soon(conn: sqlite3.Connection, as_of: date, within_days: int) -> bool:
    """¿Hay una reunión de la Fed dentro de `within_days`? Usado para (a) sumar cobertura al
    entrar y (b) proteger posiciones cerca de vencimiento (no esperar el 50% si viene un evento)."""
    macro = repo.get_latest_macro_snapshot(conn)
    if not macro or not macro["fed_meeting_date"]:
        return False
    try:
        fed_date = date.fromisoformat(macro["fed_meeting_date"])
    except (ValueError, TypeError):
        return False
    return as_of <= fed_date <= as_of + timedelta(days=within_days)


def _price_above_vwap(broker: BrokerClient | None, symbol: str, session_date: date, price: float) -> bool | None:
    """True/False si el precio está por encima del VWAP intradía de hoy; None si no hay dato
    (sin broker, sin barras, o fallo) — el evaluador trata None como 'dato incompleto'."""
    if broker is None:
        return None
    try:
        bars = broker.get_intraday_bars(symbol, session_date)
    except Exception:
        logger.debug("Robot: no se pudieron pedir barras intradía de %s para el VWAP", symbol, exc_info=True)
        return None
    if not bars:
        return None
    vwap_series = compute_vwap(bars)
    last_vwap = next((v for v in reversed(vwap_series) if v is not None), None)
    if last_vwap is None:
        return None
    return price >= last_vwap


def _log_decision(conn: sqlite3.Connection, as_of: date, symbol: str, action: str, reason: str, context: dict | None,
                  position_id: int | None = None) -> None:
    """Registra cada decisión del robot (abrir/saltear/cerrar) con los datos que la motivaron —
    semilla del historial que la capa de aprendizaje va a cruzar. `position_id` enlaza una apertura
    con su posición, para después medir su resultado real."""
    try:
        repo.insert_robot_decision(
            conn, as_of, symbol, action, reason, json.dumps(context or {}, default=str), datetime.now(),
            position_id=position_id,
        )
    except Exception:
        logger.debug("Robot: no se pudo registrar la decisión de %s", symbol, exc_info=True)


def enrich_open_decision(conn: sqlite3.Connection, symbol: str, analysis) -> bool:
    """Completa los datos de MERCADO que faltan en la decisión de apertura de una posición ya
    abierta — precio del subyacente, bid/ask, spread, OI, volumen, POP, % del día, RSI y HV —
    reusando el `analysis` que el scan ya calculó (sin pedir NADA nuevo al broker). Las posiciones
    abiertas por el código viejo no guardaron estos campos, así que la tarjeta de puntuación los
    mostraba en '—' (usuario 2026-08-05: "todos los campos deben tener información"). NO pisa los
    parámetros de apertura ya guardados: solo agrega las claves que falten. Devuelve True si escribió.

    Nota honesta: para una posición ya abierta estos valores son la lectura ACTUAL de mercado (la
    micro-estructura de opciones al momento exacto de abrir no se puede reconstruir); sirven para
    monitorear y puntuar la posición viva. Los parámetros que motivaron la decisión (delta, IV, IV
    rank, cobertura, anualizado, cerca-de-soporte, evento) sí son los de apertura y ya estaban."""
    rows = repo.get_open_simulated_positions(conn, symbol)
    if not rows:
        return False
    pos = rows[0]
    dec = repo.get_latest_open_decision_for_symbol(conn, symbol)
    if dec is None:
        return False
    try:
        ctx = json.loads(dec["context_json"]) if dec["context_json"] else {}
    except (ValueError, TypeError):
        ctx = {}

    # Ya enriquecida DEL TODO (datos de mercado + soporte) → solo asegurar el enlace y salir.
    # `support_used` se agregó después (2026-08-05); sin incluirlo acá, las posiciones ya
    # enriquecidas con los datos de mercado cortaban antes y nunca recibían el soporte.
    if "underlying_price" in ctx and "chosen_bid" in ctx and "support_used" in ctx:
        if dec["position_id"] is None:
            repo.set_decision_position_id(conn, dec["id"], pos["id"])
        return False

    snap = getattr(analysis, "snapshot", None)
    quote = getattr(analysis, "quote", None)
    chain = getattr(analysis, "chain", None)
    hist = getattr(analysis, "price_history", None)
    price = (quote.last_price if quote else None) or (snap.price if snap else None)

    add: dict = {}
    if price:
        add["underlying_price"] = round(price, 2)
    # Soporte fuerte en números para las posiciones viejas (usuario 2026-08-05): recalcula los
    # soportes diarios con el historial que el scan ya trajo, sin pedir nada nuevo.
    if hist and price and "support_used" not in ctx:
        try:
            sups = entry_rules._daily_supports(hist, price)
            if sups:
                add["support_used"] = round(min(sups, key=lambda s: abs(price - s)), 2)
                add["supports_daily"] = [round(s, 2) for s in sorted(sups, reverse=True)]
        except Exception:
            logger.debug("Robot: no se pudo recalcular soporte de %s", symbol, exc_info=True)
    if quote is not None and quote.net_change_pct is not None:
        add["day_change_pct"] = round(quote.net_change_pct, 3)
    if snap is not None:
        if snap.hv_20d is not None:
            add["hv_20d"] = snap.hv_20d
        if snap.rsi_14 is not None:
            add["rsi_14"] = snap.rsi_14

    # La pata EXACTA que tiene la posición (put, mismo strike y vencimiento) → liquidez y POP.
    contract = None
    if chain is not None:
        exp = pos["expiration_date"]
        exp = date.fromisoformat(exp) if isinstance(exp, str) else exp
        for c in chain.contracts:
            if c.option_type == "put" and abs(c.strike - pos["strike"]) < 0.01 and c.expiration == exp:
                contract = c
                break
    if contract is not None:
        add["chosen_bid"] = contract.bid
        add["chosen_ask"] = contract.ask
        spread = contract.ask - contract.bid
        add["chosen_spread_pct"] = round(spread / contract.mid_price, 4) if contract.mid_price > 0 else None
        add["chosen_open_interest"] = contract.open_interest
        add["chosen_volume"] = contract.volume
        if contract.greeks and contract.greeks.delta is not None:
            add["chosen_pop"] = round(1 - abs(contract.greeks.delta), 4)

    # POP también se deriva del delta guardado al abrir si no apareció la pata en la cadena.
    if "chosen_pop" not in add and "chosen_pop" not in ctx:
        dl = ctx.get("chosen_delta")
        if isinstance(dl, (int, float)):
            add["chosen_pop"] = round(1 - abs(dl), 4)

    new_keys = {k: v for k, v in add.items() if k not in ctx and v is not None}
    if not new_keys:
        if dec["position_id"] is None:
            repo.set_decision_position_id(conn, dec["id"], pos["id"])
        return False
    ctx.update(new_keys)
    try:
        repo.update_decision_context(conn, dec["id"], json.dumps(ctx, default=str))
        if dec["position_id"] is None:
            repo.set_decision_position_id(conn, dec["id"], pos["id"])
    except Exception:
        logger.debug("Robot: no se pudo enriquecer la decisión de %s", symbol, exc_info=True)
        return False
    logger.info("Robot: datos de mercado completados en la decisión abierta de %s (%d campos)", symbol, len(new_keys))
    return True


def process_symbol_entry(
    conn: sqlite3.Connection,
    symbol: str,
    snapshot: IndicatorSnapshot,
    chain: OptionChain,
    price_history: list[PriceBar],
    settings: Settings,
    broker: BrokerClient | None = None,
    day_change_pct: float | None = None,
) -> None:
    """Evalúa la entrada combinada del robot para `symbol` y abre una CSP simulada si pasa y hay
    cash/garantía suficiente respetando los límites de riesgo de cartera (máx posiciones abiertas
    y buying power libre). Nunca abre una segunda posición sobre el mismo símbolo."""
    sim = settings.simulator
    if not sim.enabled:
        return
    ensure_account(conn, settings)

    # Interruptor MAESTRO "pausar todo": frena TODO, incluido el trading real. Bloquea SOLO nuevas
    # aperturas — el marcado y cierre de lo ya abierto sigue corriendo aparte (mark_and_close).
    if repo.is_all_paused(conn):
        return
    # Pausa de la venta de puts del SIMULADOR (botón del dashboard). DESACOPLADA del real (usuario
    # 2026-08-10: "apusamos el simulador de ventas de put y dejamos solo iron... iron no lo vamos a
    # poner real hasta que te diga"): esta pausa NO toca el hook real, solo el paper. Se suma abajo a
    # _sim_blocked para que el real siga evaluándose con su propio conteo.
    _puts_paused_sim = repo.is_puts_paused(conn)

    # Límite de PRECIO para naked puts (usuario 2026-08-07): no operar acciones caras (colateral/
    # exposición demasiado grande por contrato). 0 = sin tope. Aplica al simulador Y al real (el real
    # además exime SPY dentro de su guardián). Filtro silencioso.
    if sim.max_underlying_price_puts > 0 and snapshot.price >= sim.max_underlying_price_puts:
        logger.debug("Robot: %s no abre — precio $%.2f >= tope $%.0f", symbol, snapshot.price, sim.max_underlying_price_puts)
        return

    # DESACOPLE simulador / real (usuario 2026-08-10: "para el real debería ser un conteo aparte, no el
    # mismo tope que el simulador"). El trading real lleva su PROPIO conteo (1/día, 5/semana, en
    # live_order_log vía el guardián). Si el real está ACTIVO hoy, evaluamos la entrada aunque el
    # simulador esté bloqueado por SUS topes; el real decide aparte. Si el real NO está activo y el
    # simulador está bloqueado, cortamos temprano (camino rápido, sin gastar evaluate_entry).
    from options_advisor.execution import live_engine
    _real_active = live_engine.is_real_active_today(conn, settings, snapshot.snapshot_date)
    _sim_max_day = repo.get_max_puts_per_day(conn, sim.max_opens_per_day)
    _sim_cap_hit = _sim_max_day > 0 and repo.count_puts_opens_today(conn, snapshot.snapshot_date) >= _sim_max_day
    _sim_has_open = repo.has_open_simulated_position(conn, symbol)
    _sim_full = len(repo.get_open_simulated_positions(conn)) >= sim.max_open_positions
    _sim_blocked = _sim_cap_hit or _sim_has_open or _sim_full or _puts_paused_sim
    if _sim_blocked and not _real_active:
        return

    _, max_dte = sim.dte_range
    price_above_vwap = _price_above_vwap(broker, symbol, snapshot.snapshot_date, snapshot.price)
    has_macro_event = _macro_event_soon(conn, snapshot.snapshot_date, max_dte)

    result = entry_rules.evaluate_entry(
        symbol, snapshot, chain, price_history, sim,
        price_above_vwap=price_above_vwap, has_macro_event=has_macro_event, day_change_pct=day_change_pct,
    )
    if not result.passed:
        _log_decision(conn, snapshot.snapshot_date, symbol, "skip", "; ".join(result.reasons), result.context)
        logger.debug("Robot: %s no calificó hoy — %s", symbol, "; ".join(result.reasons))
        return

    # Hook de TRADING REAL: la entrada calificó. El real evalúa acá con su propio conteo, INDEPENDIENTE
    # del tope del simulador. Aislado: nunca tumba el flujo del simulador (import local anti-ciclo).
    if _real_active:
        try:
            live_engine.maybe_log_live_order(conn, symbol, result, snapshot, settings, snapshot.snapshot_date, day_change_pct, broker=broker)
        except Exception:
            logger.debug("Robot: hook de trading real falló para %s (ignorado)", symbol, exc_info=True)

    # --- De acá para abajo: SOLO el simulador. Si sus propios topes lo bloquean, no abre (el real ya
    # evaluó arriba, por su cuenta). ---
    if _sim_blocked:
        if _sim_full and not _sim_has_open and not _sim_cap_hit:
            _log_decision(conn, snapshot.snapshot_date, symbol, "skip_risk",
                          f"Máximo de posiciones abiertas alcanzado ({sim.max_open_positions})", result.context)
        return

    cash, _committed, _unreal, equity = _account_state(conn)
    reserve = sim.min_free_buying_power_pct * equity
    available_cash = cash - reserve
    sizing = positions.size_position(result.contract.strike, snapshot.price, result.premium, available_cash, equity, sim)
    if sizing is None:
        _log_decision(conn, snapshot.snapshot_date, symbol, "skip_risk",
                      "Sin cash/garantía suficiente manteniendo el buying power libre", result.context)
        logger.info("Robot: %s calificó pero no hay cash suficiente (respetando BP libre) para abrir", symbol)
        return

    position_id = positions.open_position(conn, symbol, result.contract, sizing.quantity, sizing.collateral, snapshot.snapshot_date, sim)
    context = {**result.context, "quantity": sizing.quantity, "collateral": sizing.collateral, "premium": result.premium}
    _log_decision(conn, snapshot.snapshot_date, symbol, "open", "Entrada abierta", context, position_id=position_id)
    logger.info(
        "Robot: posición ABIERTA %s put $%.2f venc %s x%d contrato(s), prima %.2f, cobertura pedida %.0f%%",
        symbol, result.contract.strike, result.contract.expiration, sizing.quantity, result.premium,
        result.context.get("required_coverage_pct", 0) * 100,
    )


def mark_and_close_positions(conn: sqlite3.Connection, broker: BrokerClient, settings: Settings, as_of: date) -> None:
    """Mark-to-market de TODAS las posiciones abiertas + curva de equity. La salida escalonada y el
    stop-loss viven en positions.mark_position. Solo re-marca con el MERCADO ABIERTO: las opciones
    no se mueven fuera de horario, así que después del cierre queda congelada la última marca del
    día (usuario 2026-08-04) — evita mostrar P&L con cotizaciones de after-hours poco confiables."""
    sim = settings.simulator
    if not sim.enabled:
        return
    if market_session() != "abierto":
        logger.debug("Robot: mercado cerrado — no se re-marca (se mantiene la última marca del día)")
        return
    ensure_account(conn, settings)
    open_positions = repo.get_open_simulated_positions(conn)

    by_symbol: dict[str, list[sqlite3.Row]] = {}
    for row in open_positions:
        by_symbol.setdefault(row["symbol"], []).append(row)

    important_news = _macro_event_soon(conn, as_of, sim.near_exp_dte)

    for symbol, rows in by_symbol.items():
        try:
            quote = broker.get_quote(symbol)
            chain = broker.get_option_chain(symbol, expiration_range_days=MARK_CHAIN_FETCH_RANGE_DAYS)
        except Exception:
            logger.warning("Robot: fallo al pedir precio/cadena de %s para marcar; se reintenta mañana", symbol, exc_info=True)
            continue

        for row in rows:
            outcome = positions.mark_position(conn, row, chain, quote.last_price, as_of, sim, important_news=important_news)
            if outcome["closed"]:
                _log_decision(conn, as_of, symbol, "close", outcome["reason"],
                              {"strike": row["strike"], "expiration": row["expiration_date"],
                               "realized_pnl": outcome["unrealized_pnl"], "close_value": outcome["current_value"]})
                logger.info(
                    "Robot: posición CERRADA %s put $%.2f venc %s — motivo=%s, P&L=%.2f",
                    symbol, row["strike"], row["expiration_date"], outcome["reason"], outcome["unrealized_pnl"],
                )

    cash, committed, unrealized, equity = _account_state(conn)
    repo.upsert_simulated_equity_snapshot(conn, as_of, cash, committed, unrealized, equity)
