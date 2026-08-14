from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import OptionChain
from options_advisor.config import Settings
from options_advisor.simulator import iron_condor
from options_advisor.storage import repository as repo

logger = logging.getLogger(__name__)

# 0DTE: pedir del mismo día hasta +2 por si un feriado/fin de semana corre el vencimiento cercano.
CHAIN_FETCH_RANGE_DAYS = (0, 2)

_WATCH_LOG_INTERVAL_SECONDS = 300
_last_watch_log: dict[str, datetime] = {}


def _watch_log(conn: sqlite3.Connection, as_of: date, action: str, reason: str, context: dict | None) -> None:
    """Latido throttleado (por tipo de mensaje) para no escribir lo mismo en cada tick de 1 min."""
    key = f"{action}:{reason[:30]}"
    now = datetime.now()
    last = _last_watch_log.get(key)
    if last is None or (now - last).total_seconds() >= _WATCH_LOG_INTERVAL_SECONDS:
        _last_watch_log[key] = now
        _log(conn, as_of, action, reason, context)


def _log(conn: sqlite3.Connection, as_of: date, action: str, reason: str, context: dict | None) -> None:
    """Registra la decisión del condor en el MISMO log del robot con strategy=iron_condor en el
    contexto, para que el dashboard y el aprendizaje la distingan de los puts y del butterfly."""
    try:
        ctx = {"strategy": "iron_condor", **(context or {})}
        repo.insert_robot_decision(conn, as_of, "SPX", action, reason, json.dumps(ctx, default=str), datetime.now())
    except Exception:
        logger.debug("Condor: no se pudo registrar la decisión", exc_info=True)


def _pick_0dte_expiration(chain: OptionChain, as_of: date, target_dte: int) -> date | None:
    exps = sorted({c.expiration for c in chain.contracts if (c.expiration - as_of).days >= 0})
    if not exps:
        return None
    exact = [e for e in exps if (e - as_of).days == target_dte]
    return exact[0] if exact else exps[0]


def _chain_for_expiration(chain: OptionChain, expiration: date) -> OptionChain:
    return OptionChain(
        symbol=chain.symbol, as_of=chain.as_of, underlying_price=chain.underlying_price,
        contracts=[c for c in chain.contracts if c.expiration == expiration],
    )


def _mark_open_positions(conn: sqlite3.Connection, chain: OptionChain, spot: float, as_of: date, cfg,
                         commission_per_contract: float = 0.0) -> None:
    """Marca a mercado cada condor abierto y lo cierra a +profit_target / −stop_loss / vencimiento."""
    for row in repo.get_open_condor_positions(conn):
        expiration = date.fromisoformat(row["expiration_date"])
        expired = expiration < as_of   # 0DTE: sigue vivo durante el día del vencimiento
        close_value = iron_condor.condor_close_value(
            chain, row["short_put_strike"], row["short_call_strike"],
            row["long_put_strike"], row["long_call_strike"],
        )
        if close_value is None:
            if not expired:
                continue  # hueco de datos
            close_value = iron_condor.condor_intrinsic_close_value(
                spot, row["short_put_strike"], row["short_call_strike"],
                row["long_put_strike"], row["long_call_strike"],
            )
        unrealized = iron_condor.condor_unrealized(row["entry_net_credit"], close_value)
        # Edad de la posición en minutos (para el cierre temprano al 40% en los primeros 20 min).
        age_minutes = None
        if row["entry_ts"]:
            try:
                age_minutes = (datetime.now() - datetime.fromisoformat(row["entry_ts"])).total_seconds() / 60.0
            except (ValueError, TypeError):
                age_minutes = None
        do_close, reason = iron_condor.should_close_condor(
            unrealized, row["entry_net_credit"], expired, cfg, age_minutes=age_minutes)
        if do_close:
            # Comisión de IDA Y VUELTA: un condor son 4 PATAS y se paga por cada una al abrir y al
            # cerrar (usuario 2026-08-07). El disparo de cierre mira el P&L bruto; el realizado que se
            # guarda ya viene NETO de comisión (4 patas × 2 lados × $/contrato).
            _comm_rt = commission_per_contract * 4 * 2
            realized = round(unrealized - _comm_rt, 2)
            repo.close_condor_position(conn, row["id"], as_of, close_value, reason, realized, close_ts=datetime.now())
            _log(conn, as_of, "close", reason, {
                "short_put": row["short_put_strike"], "short_call": row["short_call_strike"],
                "realized_pnl": realized, "close_value": close_value,
            })
            logger.info("Condor: CERRADO id=%s motivo=%s P&L=%.2f (comisión $%.2f)", row["id"], reason, realized, _comm_rt)
        else:
            repo.mark_condor_position(conn, row["id"], datetime.now(), unrealized)


def process_condor_cycle(conn: sqlite3.Connection, broker: BrokerClient, settings: Settings, as_of: date) -> None:
    """Un tick del Iron Condor 0DTE (cada minuto por el scheduler): 1) marca/cierra lo abierto;
    2) si el día viene CALMO y estamos en la ventana de entrada y hay cupo del día, arma y abre un
    condor con riesgo acotado. Todo aislado — cualquier fallo se loguea y no tumba el scheduler."""
    cfg = settings.intraday_condor
    if not cfg.enabled:
        return
    symbol = cfg.underlying

    try:
        bars = broker.get_intraday_bars(symbol, as_of, interval_minutes=cfg.timeframe_minutes)
    except Exception as exc:
        _watch_log(conn, as_of, "skip", f"No se pudieron pedir barras intradía de {symbol}: {type(exc).__name__} {exc}", {})
        return
    if not bars:
        _watch_log(conn, as_of, "skip", f"Sin barras intradía de {symbol} (respuesta vacía)", {})
        return
    spot = bars[-1].close

    try:
        full_chain = broker.get_option_chain(symbol, expiration_range_days=CHAIN_FETCH_RANGE_DAYS)
    except Exception as exc:
        _watch_log(conn, as_of, "skip", f"No se pudo pedir la cadena 0DTE de {symbol}: {type(exc).__name__} {exc}", {"spot": spot})
        return
    expiration = _pick_0dte_expiration(full_chain, as_of, cfg.dte)
    if expiration is None:
        _watch_log(conn, as_of, "skip", "Sin vencimiento 0DTE disponible", {"spot": spot})
        return
    chain = _chain_for_expiration(full_chain, expiration)

    # 1) Marcar/cerrar lo abierto primero.
    _mark_open_positions(conn, chain, spot, as_of, cfg, settings.simulator.commission_per_contract)

    # Pausa manual del Iron Condor (botón del dashboard) o el interruptor MAESTRO "pausar todo"
    # (usuario 2026-08). Bloquea SOLO nuevas aperturas — el marcado/cierre de arriba ya corrió, así
    # que las posiciones abiertas se siguen manejando aunque esté pausado.
    if repo.is_condor_paused(conn) or repo.is_all_paused(conn):
        _watch_log(conn, as_of, "watch", "Pausado manualmente — no abre condors nuevos",
                   {"spot": spot, "paused": True})
        return

    # Freno del día por racha de stop-loss (usuario 2026-08-08): si YA cerró 2 (configurable) condors
    # seguidos por stop-loss hoy, no abre más hasta mañana. El marcado/cierre de arriba ya corrió, así
    # que lo abierto se sigue manejando; solo se bloquean aperturas nuevas.
    _halt_n = getattr(cfg, "stop_loss_streak_halt", 0)
    if _halt_n > 0 and repo.condor_consecutive_stop_losses_today(conn, as_of) >= _halt_n:
        _watch_log(conn, as_of, "watch",
                   f"Freno del día: {_halt_n} stop-loss seguidos — no abre más condors hoy",
                   {"spot": spot, "stop_loss_streak_halt": _halt_n})
        return

    # 2) ¿Hay cupo (por día y simultáneo)? max_per_day <= 0 = SIN TOPE diario (usuario 2026-08:
    # "todos los que quieras"); el freno real pasa a ser cuántos puede tener abiertos a la vez.
    opens_today = repo.count_condor_opens_today(conn, as_of)
    open_now = len(repo.get_open_condor_positions(conn))
    daily_cap_hit = cfg.max_per_day > 0 and opens_today >= cfg.max_per_day
    open_cap_hit = cfg.max_open_positions > 0 and open_now >= cfg.max_open_positions
    if daily_cap_hit or open_cap_hit:
        return

    # 3) Señal: día calmo + dentro de la ventana horaria.
    signal = iron_condor.evaluate_condor_signal(bars, cfg)
    if not (signal.calm and signal.in_window):
        motivo = ("fuera de la ventana horaria" if not signal.in_window
                  else f"día movido (rango {signal.day_range_pct:+.2%} > {cfg.calm_range_pct:.2%})")
        _watch_log(conn, as_of, "watch", f"Vigilando — {motivo}",
                   {"spot": spot, "day_range_pct": signal.day_range_pct, "calm": signal.calm, "in_window": signal.in_window})
        return

    build = iron_condor.build_iron_condor(chain, spot, cfg)
    if build is None:
        _watch_log(conn, as_of, "skip", "No se pudo armar el condor (sin strikes/crédito dentro del riesgo)",
                   {"spot": spot, "day_range_pct": signal.day_range_pct})
        return

    position_id = repo.insert_condor_position(
        conn, underlying=symbol, entry_date=as_of, expiration_date=expiration,
        short_put_strike=build.short_put_strike, short_call_strike=build.short_call_strike,
        long_put_strike=build.long_put_strike, long_call_strike=build.long_call_strike,
        entry_net_credit=build.net_credit, max_loss=build.max_loss, max_profit=build.max_profit,
        lower_breakeven=build.lower_breakeven, upper_breakeven=build.upper_breakeven,
        entry_spot=spot, entry_ts=datetime.now(),
    )
    _log(conn, as_of, "open", "Entrada condor abierta", {
        "position_id": position_id, "spot": spot, "day_range_pct": signal.day_range_pct,
        "short_put": build.short_put_strike, "short_call": build.short_call_strike,
        "net_credit": build.net_credit, "max_loss": build.max_loss,
    })
    logger.info(
        "Condor: ABIERTO id=%s SP=%.0f/SC=%.0f alas=%.0f venc=%s crédito=%.2f riesgo_máx=%.2f",
        position_id, build.short_put_strike, build.short_call_strike, cfg.wing_width, expiration,
        build.net_credit, build.max_loss,
    )
