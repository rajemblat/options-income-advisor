"""Motor de ejecución REAL del Iron Condor 0DTE — el MISMO cerebro del papel, pero con dinero real.

Usuario 2026-08-13: "conectar el condor en real, que el mismo cerebro que venía en papel haga
exactamente igual pero real, mismo stop loss y mismo profit %". Este módulo replica
`simulator.iron_condor_engine.process_condor_cycle` (marcar/cerrar lo abierto → evaluar/abrir) pero:
  · usa la MISMA lógica pura del papel (`simulator.iron_condor`): evaluate_condor_signal,
    build_iron_condor, should_close_condor, condor_close_value — NO se reimplementa nada del cerebro;
  · manda las órdenes COMBINADAS reales a Schwab (build_iron_condor_open/close + caminar el precio neto);
  · guarda las posiciones en su PROPIA tabla (real_condor_positions), con su propio tope diario.

Doble cinturón de seguridad (TODO tiene que estar prendido para que salga una orden real):
  1) settings.live_trading.enabled = True y dry_run = False   (maestro del trading real)
  2) settings.intraday_condor.live_enabled = True             (el condor en particular, en real)
  3) sin kill switch, y el día ARMADO desde el dashboard
  4) tope de live_max_per_day condors reales por día (1/día por default)
Gestionar/cerrar lo YA abierto NO requiere el START del día — siempre hay que poder salir.

Aislado: cualquier fallo se loguea y no tumba el scheduler. Serializado con el resto de las órdenes
reales por el MISMO lock de proceso que los puts (_LIVE_ORDER_LOCK), así nunca actúan dos jobs sobre
órdenes reales a la vez.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from options_advisor.execution import price_walker as pw
from options_advisor.execution import real_condor_sender as rcs
from options_advisor.execution.live_engine import _serialized
from options_advisor.simulator import iron_condor
from options_advisor.simulator.iron_condor_engine import (
    CHAIN_FETCH_RANGE_DAYS,
    _chain_for_expiration,
    _pick_0dte_expiration,
)
from options_advisor.storage import repository as repo

logger = logging.getLogger(__name__)

_MIN_CLOSE_DEBIT = 0.05   # débito mínimo válido para una orden de cierre (Schwab no acepta 0 neto)


def _real_condor_active(conn, settings) -> bool:
    """¿El condor REAL está activo para MANDAR órdenes nuevas? Los dos maestros prendidos, sin dry-run,
    sin kill switch. (El ARMADO del día y el tope diario se chequean aparte, más abajo.)"""
    cfg = settings.intraday_condor
    lt = settings.live_trading
    if not cfg.enabled or not getattr(cfg, "live_enabled", False):
        return False
    if not lt.enabled or lt.dry_run:
        return False
    if repo.is_live_kill_switch(conn):
        return False
    return True


def _legs_from_build(build) -> rcs.CondorLegs | None:
    """Saca los 4 símbolos OCC EXACTOS de las patas del condor armado (build.legs trae los
    OptionContract de la cadena en vivo). None si falta alguno."""
    sp = lp = sc = lc = None
    for side, otype, contract in build.legs:
        sym = getattr(contract, "symbol", None)
        if not sym:
            continue
        if side == "sell" and otype == "put":
            sp = sym
        elif side == "buy" and otype == "put":
            lp = sym
        elif side == "sell" and otype == "call":
            sc = sym
        elif side == "buy" and otype == "call":
            lc = sym
    if not all((sp, lp, sc, lc)):
        return None
    return rcs.CondorLegs(short_put_symbol=sp, long_put_symbol=lp, short_call_symbol=sc, long_call_symbol=lc)


def _combo_quotes(chain, sp_k, sc_k, lp_k, lc_k):
    """(bid, ask) de cada una de las 4 patas por strike, o None si falta alguna (hueco de datos)."""
    def leg(otype, strike):
        for c in chain.contracts:
            if c.option_type == otype and abs(c.strike - strike) < 0.01:
                return c
        return None
    sp, sc = leg("put", sp_k), leg("call", sc_k)
    lp, lc = leg("put", lp_k), leg("call", lc_k)
    if None in (sp, sc, lp, lc):
        return None
    if min(sp.bid, sc.bid, lp.bid, lc.bid) < 0 or min(sp.ask, sc.ask, lp.ask, lc.ask) <= 0:
        return None
    return sp, sc, lp, lc


def _open_credit_ladder(sp, sc, lp, lc) -> list[float]:
    """Escalera de CRÉDITO neto para ABRIR: arranca pidiendo un crédito ALTO y baja hasta el mid
    (nunca por debajo del mid — no regala crédito). combo 'ask' = crédito más alto, 'bid' = más bajo."""
    combo_bid = (sp.bid + sc.bid) - (lp.ask + lc.ask)   # crédito más bajo (marketable)
    combo_ask = (sp.ask + sc.ask) - (lp.bid + lc.bid)   # crédito más alto (resting)
    if combo_ask <= 0:
        return []
    combo_bid = max(combo_bid, 0.0)
    return pw.build_price_ladder(pw.SIDE_SELL, combo_bid, combo_ask, step=0.05, stop_at_mid=True)


def _close_debit_ladder(sp, sc, lp, lc) -> list[float]:
    """Escalera de DÉBITO neto para CERRAR: arranca ofreciendo un débito BAJO y sube hasta el mid
    (nunca por encima del mid — no paga de más). combo 'bid' = débito más bajo, 'ask' = más alto."""
    combo_bid = (sp.bid + sc.bid) - (lp.ask + lc.ask)   # débito más bajo (best)
    combo_ask = (sp.ask + sc.ask) - (lp.bid + lc.bid)   # débito más alto (marketable)
    ladder = pw.build_price_ladder(pw.SIDE_BUY, combo_bid, combo_ask, step=0.05, stop_at_mid=True)
    # Piso duro de $0.05: una orden de cierre a débito neto ≤ 0 no la acepta Schwab. Si el condor no
    # vale casi nada, cerramos por el mínimo válido.
    return [max(round(p, 2), _MIN_CLOSE_DEBIT) for p in ladder] or [_MIN_CLOSE_DEBIT]


def _email(subject: str, body: str) -> None:
    try:
        from options_advisor.alerts import notifier
        notifier.send_email(subject, body)
    except Exception:
        logger.debug("Condor-real: no se pudo mandar el email", exc_info=True)


def _resolve_account(broker, lt):
    try:
        return broker.resolve_account_hash(getattr(lt, "account_number", "") or None)
    except Exception:
        logger.exception("Condor-real: no se pudo resolver la cuenta de Schwab")
        return None


@_serialized
def process_real_condor_cycle(conn, broker, settings, as_of: date) -> None:
    """Un tick del Iron Condor 0DTE en REAL. Mismo orden que el papel: 1) reconciliar/cerrar lo abierto;
    2) si el sistema real está activo, armado y hay cupo del día, evaluar la señal y ABRIR un condor real.
    Todo aislado — cualquier fallo se loguea y nunca tumba el scheduler."""
    cfg = settings.intraday_condor
    lt = settings.live_trading

    # Camino rápido: si el condor real está apagado, no hacemos NADA (ni leemos la cuenta).
    if not (cfg.enabled and getattr(cfg, "live_enabled", False)):
        return
    if broker is None or not hasattr(broker, "place_order"):
        return
    from options_advisor.scheduler.market_calendar import market_session
    if market_session() != "abierto":
        return   # 0DTE real: solo con el mercado abierto (precios frescos, ejecución posible)
    if not _real_condor_active(conn, settings):
        return

    account_hash = _resolve_account(broker, lt)
    if not account_hash:
        return

    symbol = cfg.underlying
    # Datos de mercado (bars para la señal, cadena 0DTE para marcar/armar). Una sola pedida por tick.
    try:
        bars = broker.get_intraday_bars(symbol, as_of, interval_minutes=cfg.timeframe_minutes)
    except Exception as exc:
        logger.debug("Condor-real: sin barras intradía de %s (%s)", symbol, exc)
        bars = None
    try:
        full_chain = broker.get_option_chain(symbol, expiration_range_days=CHAIN_FETCH_RANGE_DAYS)
    except Exception as exc:
        logger.debug("Condor-real: sin cadena 0DTE de %s (%s)", symbol, exc)
        full_chain = None

    chain = None
    if full_chain is not None:
        expiration = _pick_0dte_expiration(full_chain, as_of, cfg.dte)
        if expiration is not None:
            chain = _chain_for_expiration(full_chain, expiration)

    spot = bars[-1].close if bars else (full_chain.underlying_price if full_chain else None)

    # 1) Reconciliar/cerrar lo abierto SIEMPRE (aunque no esté armado ni haya señal — hay que poder salir).
    if chain is not None and spot is not None:
        _reconcile_and_manage_open(conn, broker, account_hash, chain, spot, as_of, cfg)

    # --- A partir de acá, ABRIR uno nuevo (requiere armado PROPIO + señal + cupo) ---
    # Autorización SEPARADA de los naked (usuario 2026-08-13): el condor real tiene su propio botón de
    # START del día. Sin ese START, gestiona/cierra lo abierto pero NO abre condors nuevos.
    if not repo.is_condor_live_armed(conn, as_of):
        return
    # Pausa PROPIA del condor real (usuario 2026-08-14) además de la compartida con el papel y la maestra.
    # Cualquiera de las tres frena SOLO aperturas nuevas — lo abierto ya se gestionó arriba.
    if repo.is_condor_real_paused(conn) or repo.is_condor_paused(conn) or repo.is_all_paused(conn):
        return
    _halt_n = getattr(cfg, "stop_loss_streak_halt", 0)
    if _halt_n > 0 and repo.real_condor_consecutive_stop_losses_today(conn, as_of) >= _halt_n:
        return
    # Cupo diario PROPIO del condor, ajustable en vivo desde el dashboard (override del config para hoy).
    _cap = repo.get_condor_live_max_per_day(conn, getattr(cfg, "live_max_per_day", 1), as_of)
    if _cap > 0 and repo.count_real_condor_opens_today(conn, as_of) >= _cap:
        return
    if bars is None or chain is None or spot is None:
        return

    signal = iron_condor.evaluate_condor_signal(bars, cfg)
    if not (signal.calm and signal.in_window):
        return
    build = iron_condor.build_iron_condor(chain, spot, cfg)
    if build is None:
        return

    _open_real_condor(conn, broker, account_hash, chain, build, spot, as_of, cfg, lt, symbol, signal)


def _reconcile_and_manage_open(conn, broker, account_hash, chain, spot, as_of: date, cfg) -> None:
    """Para cada condor real vivo: si está 'working' reconcilia el fill; si está 'open' lo marca a mercado
    y lo cierra con la MISMA regla del papel (should_close_condor)."""
    for row in repo.get_open_real_condor_positions(conn):
        try:
            if row["status"] == "working":
                _reconcile_working_open(conn, broker, account_hash, row)
            else:
                _manage_open_position(conn, broker, account_hash, chain, spot, as_of, cfg, row)
        except Exception:
            logger.exception("Condor-real: fallo gestionando la posición id=%s (se continúa)", row["id"])


def _manual_close_requested(row) -> bool:
    """¿El usuario pidió cerrar ESTA posición desde el dashboard? Tolerante a filas sin la columna
    (bases viejas antes de la migración, y fixtures de tests que arman la fila a mano)."""
    try:
        return bool(row["manual_close_requested"])
    except (IndexError, KeyError):
        return False


def _reconcile_working_open(conn, broker, account_hash, row) -> None:
    """La apertura combinada quedó puesta (working): ¿ya llenó o murió?"""
    oid = row["open_schwab_order_id"]
    if not oid:
        return
    # Cierre manual pedido sobre una apertura que TODAVÍA no llenó (usuario 2026-08-14): lo correcto no es
    # recomprar (no hay posición), es CANCELAR la orden puesta. Si ya llenó entre medio, el cancel falla o
    # llega tarde y el próximo tick la trata como abierta con la bandera todavía puesta — que la cerrará.
    if _manual_close_requested(row):
        try:
            broker.cancel_order(account_hash, oid)
            logger.warning("Condor-real: apertura id=%s CANCELADA a pedido del usuario (todavía no había llenado)",
                           row["id"])
        except Exception:
            logger.exception("Condor-real: no se pudo cancelar la apertura id=%s (se reintenta el próximo tick)",
                             row["id"])
        # No cerramos la fila acá: el próximo get_order dirá CANCELED (o FILLED) y el flujo de abajo la
        # resuelve con el estado REAL del broker, nunca con lo que asumimos.
    try:
        info = broker.get_order(account_hash, oid)
    except Exception:
        logger.debug("Condor-real: no se pudo leer el estado de la apertura id=%s", row["id"], exc_info=True)
        return
    status = (info.get("status") or "").upper()
    if status == "FILLED":
        qty = row["quantity"] or 1
        credit_ps = row["entry_credit_ps"] if row["entry_credit_ps"] is not None else (
            (row["entry_net_credit"] or 0.0) / (100.0 * qty))
        entry_total = round(credit_ps * 100.0 * qty, 2)
        repo.mark_real_condor_fill(conn, row["id"], entry_credit_ps=round(credit_ps, 2),
                                   entry_net_credit=entry_total, open_schwab_order_id=oid,
                                   entry_ts=datetime.now())
        logger.warning("Condor-real: apertura id=%s LLENÓ — crédito $%.2f", row["id"], entry_total)
        _email("🟢 Lokshn ABRIÓ un Iron Condor REAL",
               f"Iron Condor {row['underlying']} 0DTE ABIERTO en real.\n"
               f"Vende put {row['short_put_strike']:.0f} / call {row['short_call_strike']:.0f}, "
               f"alas {row['long_put_strike']:.0f}/{row['long_call_strike']:.0f}.\n"
               f"Crédito cobrado: ${entry_total:,.2f}.")
    elif status in ("REJECTED", "CANCELED", "EXPIRED"):
        # La apertura murió sin llenar: la posición no existe. La marcamos cerrada/void (ocupa el cupo
        # del día para no reintentar churn, pero con P&L None → no cuenta en el win rate).
        repo.close_real_condor_position(conn, row["id"], date.today(),
                                        close_value=None, close_reason="apertura_no_llenó",
                                        realized_pnl=None, close_ts=datetime.now())
        logger.info("Condor-real: apertura id=%s %s sin llenar — descartada", row["id"], status)


def _manage_open_position(conn, broker, account_hash, chain, spot, as_of: date, cfg, row) -> None:
    """Marca a mercado un condor real abierto y lo cierra con la MISMA regla del papel."""
    expiration = date.fromisoformat(row["expiration_date"])
    expired = expiration < as_of
    qty = row["quantity"] or 1
    close_value_pc = iron_condor.condor_close_value(
        chain, row["short_put_strike"], row["short_call_strike"],
        row["long_put_strike"], row["long_call_strike"])
    if close_value_pc is None:
        if not expired:
            return  # hueco de datos: no cerramos a ciegas
        close_value_pc = iron_condor.condor_intrinsic_close_value(
            spot, row["short_put_strike"], row["short_call_strike"],
            row["long_put_strike"], row["long_call_strike"])
    close_value_total = round(close_value_pc * qty, 2)
    entry_total = row["entry_net_credit"] or 0.0
    unrealized = round(entry_total - close_value_total, 2)

    age_minutes = None
    if row["entry_ts"]:
        try:
            age_minutes = (datetime.now() - datetime.fromisoformat(row["entry_ts"])).total_seconds() / 60.0
        except (ValueError, TypeError):
            age_minutes = None

    do_close, reason = iron_condor.should_close_condor(unrealized, entry_total, expired, cfg, age_minutes=age_minutes)
    # Cierre manual pedido desde el dashboard (usuario 2026-08-14): manda por encima de la regla, tanto si
    # todavía no llegó al objetivo de ganancia como si está en pérdida. El motivo queda como 'manual' para
    # que no se confunda con un profit_target/stop_loss en el historial ni en la racha de stop-loss del día.
    if not do_close and _manual_close_requested(row):
        do_close, reason = True, "manual"
        logger.warning("Condor-real: cierre MANUAL pedido para id=%s (P&L no realizado $%.2f)",
                       row["id"], unrealized)
    if not do_close:
        repo.mark_real_condor_position(conn, row["id"], datetime.now(), unrealized)
        return

    # Vencido y sin cadena: se liquida solo (settlement). Marcamos cerrado con el intrínseco.
    if expired and iron_condor.condor_close_value(chain, row["short_put_strike"], row["short_call_strike"],
                                                  row["long_put_strike"], row["long_call_strike"]) is None:
        realized = round(unrealized, 2)
        repo.close_real_condor_position(conn, row["id"], date.today(), close_value_total, "expired",
                                        realized, close_ts=datetime.now())
        logger.warning("Condor-real: id=%s vencido (settlement) — P&L $%.2f", row["id"], realized)
        return

    # Recompra combinada (NET_DEBIT), caminando el precio hacia el mid.
    q = _combo_quotes(chain, row["short_put_strike"], row["short_call_strike"],
                      row["long_put_strike"], row["long_call_strike"])
    if q is None:
        repo.mark_real_condor_position(conn, row["id"], datetime.now(), unrealized)
        return
    sp, sc, lp, lc = q
    ladder = _close_debit_ladder(sp, sc, lp, lc)
    legs = rcs.CondorLegs(short_put_symbol=sp.symbol, long_put_symbol=lp.symbol,
                          short_call_symbol=sc.symbol, long_call_symbol=lc.symbol)
    logger.warning("Condor-real: CERRANDO id=%s motivo=%s (débito arranca $%.2f → mid)",
                   row["id"], reason, ladder[0] if ladder else 0.0)
    res = rcs.execute_condor_walk(broker, account_hash, rcs.SIDE_CLOSE, legs, qty, ladder,
                                  interval_seconds=10, leave_resting_at_mid=False)
    if res.filled:
        close_debit_total = round((res.fill_price or (close_value_pc)) * 100.0 * qty, 2)
        commission_rt = settings_commission(cfg)  # 0 salvo que se configure
        realized = round(entry_total - close_debit_total - commission_rt, 2)
        repo.close_real_condor_position(conn, row["id"], date.today(), close_debit_total, reason,
                                        realized, close_ts=datetime.now(), close_schwab_order_id=res.order_id)
        logger.warning("Condor-real: id=%s CERRADO — motivo %s — P&L $%.2f", row["id"], reason, realized)
        signo = "🟢" if realized >= 0 else "🔴"
        _email(f"{signo} Lokshn CERRÓ un Iron Condor REAL — P&L ${realized:+,.2f}",
               f"Iron Condor {row['underlying']} cerrado (motivo: {reason}).\n"
               f"Crédito abierto ${entry_total:,.2f} → costo de cierre ${close_debit_total:,.2f}.\n"
               f"Resultado: ${realized:+,.2f}.")
    else:
        # No llenó: dejamos la posición abierta, marcamos el unrealized y reintentamos el próximo tick.
        repo.mark_real_condor_position(conn, row["id"], datetime.now(), unrealized)
        logger.info("Condor-real: la recompra de id=%s no llenó (%s); se reintenta", row["id"], res.status)


def settings_commission(cfg) -> float:
    """Comisión de ida y vuelta del condor (4 patas × 2 lados). El condor usa la comisión del simulador;
    acá, sin acceso directo, devolvemos 0 (Schwab no cobra comisión de opciones de índice al usuario).
    Se deja como función para poder enchufar la comisión real si se configura."""
    return 0.0


def _open_real_condor(conn, broker, account_hash, chain, build, spot, as_of: date, cfg, lt, symbol, signal) -> None:
    """Manda la orden combinada REAL para abrir el condor y registra la posición."""
    legs = _legs_from_build(build)
    if legs is None:
        logger.warning("Condor-real: no se pudieron extraer los símbolos OCC de las patas — no se abre")
        return
    q = _combo_quotes(chain, build.short_put_strike, build.short_call_strike,
                      build.long_put_strike, build.long_call_strike)
    if q is None:
        return
    sp, sc, lp, lc = q
    ladder = _open_credit_ladder(sp, sc, lp, lc)
    if not ladder:
        logger.info("Condor-real: crédito neto no positivo en la cadena viva — no se abre")
        return

    quantity = 1   # usuario 2026-08-13: 1 condor por día, 1 contrato
    # Vencimiento 0DTE de las patas armadas (todas comparten el mismo vencimiento).
    expiration = build.legs[0][2].expiration if build.legs else (chain.contracts[0].expiration if chain.contracts else as_of)

    logger.warning("Condor-real: ABRIENDO %s — vende put %.0f/call %.0f, alas %.0f/%.0f (crédito arranca $%.2f → mid)",
                   symbol, build.short_put_strike, build.short_call_strike,
                   build.long_put_strike, build.long_call_strike, ladder[0])
    res = rcs.execute_condor_walk(broker, account_hash, rcs.SIDE_OPEN, legs, quantity, ladder,
                                  interval_seconds=10, leave_resting_at_mid=True)
    if not res.ok:
        logger.warning("Condor-real: la apertura NO se llegó a colocar (%s) — no se registra, se reintenta", res.error)
        return

    entry_ps = res.fill_price if (res.filled and res.fill_price is not None) else (res.final_limit_price or (build.net_credit / 100.0))
    entry_total = round(entry_ps * 100.0 * quantity, 2)
    status = "open" if res.filled else "working"
    pos_id = repo.insert_real_condor_position(
        conn, underlying=symbol, entry_date=as_of, expiration_date=expiration,
        short_put_strike=build.short_put_strike, short_call_strike=build.short_call_strike,
        long_put_strike=build.long_put_strike, long_call_strike=build.long_call_strike,
        short_put_symbol=legs.short_put_symbol, long_put_symbol=legs.long_put_symbol,
        short_call_symbol=legs.short_call_symbol, long_call_symbol=legs.long_call_symbol,
        quantity=quantity, entry_net_credit=entry_total, max_loss=build.max_loss, max_profit=build.max_profit,
        lower_breakeven=build.lower_breakeven, upper_breakeven=build.upper_breakeven, entry_spot=spot,
        open_schwab_order_id=res.order_id, status=status, entry_ts=datetime.now() if res.filled else None,
    )
    if res.filled:
        logger.warning("Condor-real: ABIERTO id=%s LLENÓ al instante — crédito $%.2f", pos_id, entry_total)
        _email("🟢 Lokshn ABRIÓ un Iron Condor REAL",
               f"Iron Condor {symbol} 0DTE ABIERTO en real.\n"
               f"Vende put {build.short_put_strike:.0f} / call {build.short_call_strike:.0f}, "
               f"alas {build.long_put_strike:.0f}/{build.long_call_strike:.0f}.\n"
               f"Crédito cobrado: ${entry_total:,.2f}.")
    else:
        logger.warning("Condor-real: ABIERTO id=%s puesto y NEGOCIANDO (crédito límite $%.2f)", pos_id, entry_total)
        _email("🟡 Lokshn PUSO un Iron Condor REAL — negociando",
               f"Iron Condor {symbol} 0DTE enviado (esperando fill).\n"
               f"Vende put {build.short_put_strike:.0f} / call {build.short_call_strike:.0f}, "
               f"alas {build.long_put_strike:.0f}/{build.long_call_strike:.0f}.\n"
               f"Crédito límite: ${entry_total:,.2f}. Te aviso cuando LLENE.")
