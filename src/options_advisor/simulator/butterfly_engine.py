from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import OptionChain
from options_advisor.config import Settings
from options_advisor.simulator import butterfly
from options_advisor.storage import repository as repo

logger = logging.getLogger(__name__)

# Rango de vencimientos a pedir para el 0DTE — arranca en 0 (mismo día) y llega a 2 por si un
# feriado/fin de semana corre el vencimiento más cercano.
CHAIN_FETCH_RANGE_DAYS = (0, 2)

# Cada cuántos segundos registrar un mensaje repetido (latido "vigilando" o un error recurrente
# de datos) — para que el usuario vea que el motor está vivo sin llenar el log (un tick es cada
# minuto). Se throttlea por tipo de mensaje.
_WATCH_LOG_INTERVAL_SECONDS = 300
_last_watch_log: dict[str, datetime] = {}


def _watch_log(conn: sqlite3.Connection, as_of: date, action: str, reason: str, context: dict | None) -> None:
    """Registra un mensaje del butterfly THROTTLEADO por tipo (acción + prefijo del texto), para
    no escribir lo mismo en cada tick de 1 min. Se usa para el latido de 'vigilando' y para dejar
    rastro en la base de un problema de datos recurrente (que si no, solo iría a la Terminal)."""
    key = f"{action}:{reason[:30]}"
    now = datetime.now()
    last = _last_watch_log.get(key)
    if last is None or (now - last).total_seconds() >= _WATCH_LOG_INTERVAL_SECONDS:
        _last_watch_log[key] = now
        _log(conn, as_of, action, reason, context)


def _log(conn: sqlite3.Connection, as_of: date, action: str, reason: str, context: dict | None) -> None:
    """Registra la decisión del butterfly en el MISMO log del robot (con strategy en el contexto)
    para que la pestaña Decisiones del dashboard y la futura capa de IA la crucen igual que las
    del robot de puts."""
    try:
        ctx = {"strategy": "iron_butterfly", **(context or {})}
        repo.insert_robot_decision(conn, as_of, "SPX", action, reason, json.dumps(ctx, default=str), datetime.now())
    except Exception:
        logger.debug("Butterfly: no se pudo registrar la decisión", exc_info=True)


def _pick_0dte_expiration(chain: OptionChain, as_of: date, target_dte: int) -> date | None:
    """El vencimiento objetivo del 0DTE: el que cae a `target_dte` días (0 = hoy); si no existe
    exacto, el más cercano hacia adelante (nunca uno ya vencido)."""
    exps = sorted({c.expiration for c in chain.contracts if (c.expiration - as_of).days >= 0})
    if not exps:
        return None
    exact = [e for e in exps if (e - as_of).days == target_dte]
    if exact:
        return exact[0]
    return exps[0]


def _chain_for_expiration(chain: OptionChain, expiration: date) -> OptionChain:
    """Sub-cadena con solo las patas del vencimiento elegido — build_iron_butterfly y el marcado
    trabajan sobre una única expiración."""
    return OptionChain(
        symbol=chain.symbol,
        as_of=chain.as_of,
        underlying_price=chain.underlying_price,
        contracts=[c for c in chain.contracts if c.expiration == expiration],
    )


def _mark_open_positions(
    conn: sqlite3.Connection, chain: OptionChain, spot: float, as_of: date, settings: Settings
) -> None:
    """Marca a mercado cada butterfly abierto y lo cierra si tocó +profit_target / −stop_loss /
    vencimiento. Si falta una pata en la cadena y NO venció, no marca (hueco de datos)."""
    cfg = settings.intraday_butterfly
    for row in repo.get_open_butterfly_positions(conn):
        expiration = date.fromisoformat(row["expiration_date"])
        # 0DTE: durante la sesión del día de vencimiento la posición sigue VIVA (vence al cierre
        # de las 16:00, no por fecha) — se marca contra la cadena en vivo y solo cierra por
        # profit/stop. `expired` (liquidar a intrínseco) es solo si el vencimiento ya QUEDÓ ATRÁS
        # (< hoy), como red de seguridad si una posición sobrevivió al día.
        expired = expiration < as_of
        close_value = butterfly.butterfly_close_value(
            chain, row["body_strike"], row["long_put_strike"], row["long_call_strike"]
        )
        if close_value is None:
            if not expired:
                continue  # hueco de datos: no marcar ni cerrar
            close_value = butterfly.butterfly_intrinsic_close_value(
                spot, row["body_strike"], row["long_put_strike"], row["long_call_strike"]
            )
        unrealized = butterfly.butterfly_unrealized(row["entry_net_credit"], close_value)
        do_close, reason = butterfly.should_close_butterfly(unrealized, expired, cfg, entry_net_credit=row["entry_net_credit"])
        if do_close:
            # Comisión de IDA Y VUELTA: un butterfly son 4 PATAS y se paga por cada una al abrir y al
            # cerrar (usuario 2026-08-07). El disparo de cierre sigue mirando el P&L bruto; el realizado
            # que se guarda ya viene NETO de comisión (4 patas × 2 lados × $/contrato).
            _comm_rt = settings.simulator.commission_per_contract * 4 * 2
            realized = round(unrealized - _comm_rt, 2)
            repo.close_butterfly_position(conn, row["id"], as_of, close_value, reason, realized, close_ts=datetime.now())
            _log(conn, as_of, "close", reason, {
                "body_strike": row["body_strike"], "realized_pnl": realized, "close_value": close_value,
            })
            logger.info("Butterfly: CERRADA id=%s motivo=%s P&L=%.2f (comisión $%.2f)", row["id"], reason, realized, _comm_rt)
        else:
            repo.mark_butterfly_position(conn, row["id"], datetime.now(), unrealized)


def process_butterfly_cycle(
    conn: sqlite3.Connection, broker: BrokerClient, settings: Settings, as_of: date
) -> None:
    """Un tick del motor en vivo del Iron Butterfly 0DTE (llamado cada minuto por el scheduler):
    1) marca/cierra las posiciones abiertas; 2) evalúa la señal de reversión a la SMA8 sobre las
    barras de 1 min; 3) si dispara y hay cupo, arma y abre un butterfly con riesgo acotado. Todo
    aislado: cualquier fallo se loguea y no tumba el resto del scheduler."""
    cfg = settings.intraday_butterfly
    if not cfg.enabled:
        return
    # Etapa 3 (aplicar lo aprendido): el umbral de distancia de entrada puede venir ajustado por el
    # aprendizaje del iron. Import local para evitar ciclo de imports.
    from options_advisor.simulator import learning
    cfg = learning.effective_butterfly(conn, cfg)
    symbol = cfg.underlying

    try:
        bars = broker.get_intraday_bars(symbol, as_of, interval_minutes=cfg.timeframe_minutes)
    except Exception as exc:
        logger.warning("Butterfly: no se pudieron pedir barras intradía de %s", symbol, exc_info=True)
        _watch_log(conn, as_of, "skip", f"No se pudieron pedir barras intradía de {symbol}: {type(exc).__name__} {exc}", {})
        return
    if not bars:
        _watch_log(conn, as_of, "skip", f"Sin barras intradía de {symbol} (respuesta vacía)", {})
        return
    spot = bars[-1].close

    try:
        full_chain = broker.get_option_chain(symbol, expiration_range_days=CHAIN_FETCH_RANGE_DAYS)
    except Exception as exc:
        logger.warning("Butterfly: no se pudo pedir la cadena 0DTE de %s", symbol, exc_info=True)
        _watch_log(conn, as_of, "skip", f"No se pudo pedir la cadena 0DTE de {symbol}: {type(exc).__name__} {exc}", {"spot": spot})
        return
    expiration = _pick_0dte_expiration(full_chain, as_of, cfg.dte)
    if expiration is None:
        _log(conn, as_of, "skip", "Sin vencimiento 0DTE disponible", {"spot": spot})
        return
    chain = _chain_for_expiration(full_chain, expiration)

    # 1) Marcar/cerrar lo abierto primero (puede liberar cupo para una nueva entrada).
    _mark_open_positions(conn, chain, spot, as_of, settings)

    # Pausa manual del Iron Butterfly (botón del dashboard) o el interruptor MAESTRO "pausar todo"
    # (usuario 2026-08). Bloquea SOLO nuevas aperturas — el marcado/cierre de arriba ya corrió.
    if repo.is_butterfly_paused(conn) or repo.is_all_paused(conn):
        _watch_log(conn, as_of, "watch", "Pausado manualmente — no abre butterflies nuevos",
                   {"spot": spot, "paused": True})
        return

    # Freno del día por racha de stop-loss (usuario 2026-08-08): si YA cerró 2 (configurable) butterflies
    # seguidos por stop-loss hoy, no abre más hasta mañana (una ganancia en el medio corta la racha).
    _halt_n = getattr(cfg, "stop_loss_streak_halt", 0)
    if _halt_n > 0 and repo.butterfly_consecutive_stop_losses_today(conn, as_of) >= _halt_n:
        _watch_log(conn, as_of, "watch",
                   f"Freno del día: {_halt_n} stop-loss seguidos — no abre más butterflies hoy",
                   {"spot": spot, "stop_loss_streak_halt": _halt_n})
        return

    # 2) ¿Hay cupo para abrir?
    open_count = len(repo.get_open_butterfly_positions(conn))
    if open_count >= cfg.max_open_positions:
        return

    # 3) Señal de reversión a la SMA8.
    signal = butterfly.evaluate_signal(bars, cfg)
    if signal.direction is None:
        # Latido: registra (throttleado) que sigue vivo y vigilando — así el dashboard muestra el
        # estado actual de SPX vs SMA8 aunque no haya entrada.
        _watch_log(conn, as_of, "watch", f"Vigilando — a {signal.distance_pct:+.3%} de la SMA{cfg.sma_period}",
                   {"spot": spot, "sma8": signal.sma8, "distance_pct": signal.distance_pct})
        return

    build = butterfly.build_iron_butterfly(chain, spot, signal.direction, cfg)
    if build is None:
        _log(conn, as_of, "skip", "No se pudo armar el butterfly dentro del tope de riesgo", {
            "spot": spot, "direction": signal.direction, "sma8": signal.sma8, "distance_pct": signal.distance_pct,
        })
        return

    position_id = repo.insert_butterfly_position(
        conn,
        underlying=symbol,
        direction=signal.direction,
        entry_date=as_of,
        expiration_date=expiration,
        body_strike=build.body_strike,
        long_put_strike=build.body_strike - build.put_wing_width,
        long_call_strike=build.body_strike + build.call_wing_width,
        entry_net_credit=build.net_credit,
        max_loss=build.max_loss,
        max_profit=build.max_profit,
        lower_breakeven=build.lower_breakeven,
        upper_breakeven=build.upper_breakeven,
        entry_spot=spot,
        entry_ts=datetime.now(),
    )
    _log(conn, as_of, "open", "Entrada butterfly abierta", {
        "position_id": position_id, "spot": spot, "direction": signal.direction, "sma8": signal.sma8,
        "distance_pct": signal.distance_pct, "body_strike": build.body_strike,
        "net_credit": build.net_credit, "max_loss": build.max_loss,
    })
    logger.info(
        "Butterfly: ABIERTA id=%s %s cuerpo=%.0f venc=%s crédito=%.2f riesgo_máx=%.2f",
        position_id, signal.direction, build.body_strike, expiration, build.net_credit, build.max_loss,
    )
