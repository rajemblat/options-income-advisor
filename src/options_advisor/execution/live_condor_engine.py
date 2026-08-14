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
from options_advisor.simulator import iron_condor, iron_condor_engine, learning
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


def _occ(contract) -> str | None:
    """Símbolo OCC de ESTA opción. Es `occ_symbol`, NUNCA `symbol` — `symbol` es el subyacente
    ("$SPX" para las cuatro patas), y usarlo hacía que la orden combinada se rechazara siempre con
    "hacen falta 4 símbolos OCC distintos" (bug real 2026-08-14, con el condor ya autorizado).
    Sin `occ_symbol` devolvemos None y NO se opera: preferimos no abrir antes que mandar una orden
    sobre un instrumento reconstruido a mano, que en índices (SPX vs. SPXW) es un error caro."""
    return getattr(contract, "occ_symbol", None) or None


def _legs_from_build(build) -> rcs.CondorLegs | None:
    """Saca los 4 símbolos OCC EXACTOS de las patas del condor armado (build.legs trae los
    OptionContract de la cadena en vivo). None si falta alguno."""
    sp = lp = sc = lc = None
    for side, otype, contract in build.legs:
        sym = _occ(contract)
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
    if not all((sp, lp, sc, lc)) or len({sp, lp, sc, lc}) != 4:
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

    # Perillas APRENDIDAS, las MISMAS que aplica el papel (usuario 2026-08-14): lo que el robot
    # aprendió operando en papel se aplica igual acá. Va después de las compuertas de seguridad a
    # propósito — el aprendizaje ajusta CÓMO opera, nunca SI puede operar.
    cfg = learning.effective_condor(conn, cfg)

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

    # 0) Filas que quedaron en 'sending' (se guardó la intención y el proceso murió antes de anotar el
    # resultado): preguntarle a Schwab qué pasó ANTES de cualquier otra cosa.
    _reconcile_sending(conn, broker, account_hash)

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

    # Mismo filtro de VIX en suba que el papel (usuario 2026-08-14) — el cfg de acá ya viene con las
    # perillas aprendidas aplicadas, así que el tope de VIX es el mismo que aprendió operando en papel.
    vix_chg = iron_condor_engine.vix_change_pct(broker)
    signal = iron_condor.evaluate_condor_signal(bars, cfg, vix_change_pct=vix_chg)
    if not (signal.calm and signal.in_window and signal.vix_ok):
        return
    build = iron_condor.build_iron_condor(chain, spot, cfg)
    if build is None:
        return

    _open_real_condor(conn, broker, account_hash, chain, build, spot, as_of, cfg, lt, symbol, signal,
                      vix_chg=vix_chg)


_SENDING_GRACE_MINUTES = 5


def _reconcile_sending(conn, broker, account_hash) -> None:
    """Resuelve las filas que quedaron en 'sending': se guardaron justo antes de mandar la orden y el
    proceso murió antes de poder anotar el resultado (usuario 2026-08-14 — ese día una orden REAL salió
    a Schwab y su fila nunca se escribió, quedando una posición que el robot no gestionaba).

    Con la fila ya guardada, al arrancar se le puede PREGUNTAR a Schwab qué pasó: si aparece una orden
    llenada con las mismas 4 patas, se adopta; si no aparece nada tras la ventana de gracia, se cierra
    como no confirmada y se avisa por mail para que el usuario lo revise en el broker. Nunca se asume
    nada: o lo confirma Schwab, o te avisa."""
    try:
        filas = conn.execute(
            "SELECT * FROM real_condor_positions WHERE status = 'sending' ORDER BY id"
        ).fetchall()
    except Exception:
        return
    if not filas:
        return
    for row in filas:
        try:
            _edad = None
            if row["entry_date"]:
                _edad = (datetime.now() - datetime.fromisoformat(str(row["entry_date"]) + "T00:00:00")).total_seconds() / 60.0
            adoptada = _buscar_orden_en_schwab(broker, row)
            if adoptada is not None:
                credito_ps, oid = adoptada
                repo.mark_real_condor_fill(conn, row["id"], entry_credit_ps=round(credito_ps, 2),
                                           entry_net_credit=round(credito_ps * 100.0 * (row["quantity"] or 1), 2),
                                           open_schwab_order_id=oid, entry_ts=datetime.now())
                logger.warning("Condor-real: id=%s estaba en 'sending' y Schwab confirma que LLENÓ "
                               "(orden %s) — adoptada", row["id"], oid)
                _email("🟢 Lokshn recuperó un Iron Condor REAL que había quedado sin registrar",
                       f"La orden del condor {row['underlying']} sí se ejecutó en Schwab y el robot la "
                       f"volvió a tomar bajo su gestión. Crédito ${credito_ps * 100.0:,.2f}.")
                continue
            if _edad is not None and _edad < _SENDING_GRACE_MINUTES:
                continue   # muy reciente: puede estar todavía negociándose, se revisa el próximo tick
            repo.close_real_condor_position(conn, row["id"], date.today(), close_value=None,
                                            close_reason="no_confirmada", realized_pnl=None,
                                            close_ts=datetime.now())
            logger.error("Condor-real: id=%s quedó en 'sending' y Schwab no confirma ninguna orden — "
                         "marcada NO CONFIRMADA. REVISAR EN EL BROKER.", row["id"])
            _email("⚠️ Lokshn: una orden de Iron Condor quedó SIN CONFIRMAR",
                   f"El robot guardó la intención de abrir un condor {row['underlying']} "
                   f"(put {row['short_put_strike']:.0f} / call {row['short_call_strike']:.0f}) pero no pudo "
                   "confirmar con Schwab si la orden llegó a ejecutarse.\n\n"
                   "REVISÁ TU CUENTA EN SCHWAB. Si la posición existe, el robot NO la está gestionando.")
        except Exception:
            logger.exception("Condor-real: fallo reconciliando la fila 'sending' id=%s", row["id"])


def _buscar_orden_en_schwab(broker, row):
    """¿Schwab tiene una orden LLENADA con exactamente las 4 patas de esta fila? Devuelve
    (crédito por acción, order_id) o None. Es la única fuente de verdad admitida acá."""
    try:
        from datetime import timedelta, timezone
        ordenes = broker.get_recent_filled_orders(datetime.now(timezone.utc) - timedelta(hours=6))
    except Exception:
        return None
    buscadas = {row["short_put_symbol"], row["long_put_symbol"],
                row["short_call_symbol"], row["long_call_symbol"]}
    for o in ordenes or []:
        patas = getattr(o, "legs", []) or []
        if len(patas) != 4:
            continue
        if {getattr(l, "occ_symbol", None) for l in patas} != buscadas:
            continue
        credito = 0.0
        for l in patas:
            precio = float(getattr(l, "price", 0) or 0)
            credito += precio if str(getattr(l, "instruction", "")).upper().startswith("SELL") else -precio
        return credito, str(getattr(o, "order_id", "") or "")
    return None


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
    # Para CERRAR se recompran exactamente las MISMAS 4 patas: los símbolos OCC guardados al abrir
    # son la fuente de verdad; la cadena viva es solo el respaldo si la fila es vieja y no los tiene.
    _cierre = [row["short_put_symbol"] or _occ(sp), row["long_put_symbol"] or _occ(lp),
               row["short_call_symbol"] or _occ(sc), row["long_call_symbol"] or _occ(lc)]
    if not all(_cierre) or len(set(_cierre)) != 4:
        repo.mark_real_condor_position(conn, row["id"], datetime.now(), unrealized)
        logger.error("Condor-real: id=%s sin los 4 símbolos OCC para recomprar — NO se cierra a ciegas", row["id"])
        return
    legs = rcs.CondorLegs(short_put_symbol=_cierre[0], long_put_symbol=_cierre[1],
                          short_call_symbol=_cierre[2], long_call_symbol=_cierre[3])
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


def _log_open_for_learning(conn, as_of: date, pos_id: int, build, spot, signal, entry_total: float,
                           vix_chg: float | None) -> None:
    """Registra la apertura REAL en el mismo log del robot que usa el papel, con las MISMAS features,
    para que el aprendizaje del condor se alimente de las dos (usuario 2026-08-14: "papel y real
    juntos"). `book: real` es lo que evita que un id de papel y uno real se confundan al cruzar."""
    try:
        import json as _json
        sp_d, sc_d = iron_condor.short_leg_deltas(build)
        ctx = {
            "strategy": "iron_condor", "book": "real", "position_id": pos_id, "spot": spot,
            "day_range_pct": signal.day_range_pct,
            "short_put": build.short_put_strike, "short_call": build.short_call_strike,
            "net_credit": entry_total, "max_loss": build.max_loss,
            "short_put_delta": sp_d, "short_call_delta": sc_d,
            "short_delta_avg": (round((sp_d + sc_d) / 2, 4) if sp_d is not None and sc_d is not None else None),
            "vix_change_pct": vix_chg,
        }
        repo.insert_robot_decision(conn, as_of, "SPX", "open", "Entrada condor REAL abierta",
                                   _json.dumps(ctx, default=str), datetime.now())
    except Exception:
        logger.debug("Condor-real: no se pudo registrar la apertura para el aprendizaje", exc_info=True)


def _open_real_condor(conn, broker, account_hash, chain, build, spot, as_of: date, cfg, lt, symbol, signal,
                      vix_chg: float | None = None) -> None:
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

    # ═══ GUARDAR ANTES DE MANDAR (usuario 2026-08-14) ═══
    # El orden anterior era "mando y después guardo". El 14/08 la orden salió a Schwab a las 09:53 y
    # la fila nunca se escribió porque la base estaba trabada: quedó una posición REAL viva que el
    # robot no sabía que existía — no la marcaba, no la cerraba al 50%, no le aplicaba el stop. La
    # tuvo que cerrar el usuario a mano.
    # Ahora la fila se escribe PRIMERO, en estado 'sending'. Si el proceso muere entre el insert y el
    # envío, al arrancar queda esa fila y `_reconcile_sending` le pregunta a Schwab qué pasó con esa
    # orden. Una fila de más se limpia sola; una posición real sin registrar, no.
    entry_estimado = round((build.net_credit / 100.0) * 100.0 * quantity, 2)
    pos_id = repo.insert_real_condor_position(
        conn, underlying=symbol, entry_date=as_of, expiration_date=expiration,
        short_put_strike=build.short_put_strike, short_call_strike=build.short_call_strike,
        long_put_strike=build.long_put_strike, long_call_strike=build.long_call_strike,
        short_put_symbol=legs.short_put_symbol, long_put_symbol=legs.long_put_symbol,
        short_call_symbol=legs.short_call_symbol, long_call_symbol=legs.long_call_symbol,
        quantity=quantity, entry_net_credit=entry_estimado, max_loss=build.max_loss,
        max_profit=build.max_profit, lower_breakeven=build.lower_breakeven,
        upper_breakeven=build.upper_breakeven, entry_spot=spot,
        open_schwab_order_id=None, status="sending", entry_ts=None,
    )
    logger.warning("Condor-real: ABRIENDO %s id=%s — vende put %.0f/call %.0f, alas %.0f/%.0f "
                   "(crédito arranca $%.2f → mid) — fila guardada ANTES de mandar",
                   symbol, pos_id, build.short_put_strike, build.short_call_strike,
                   build.long_put_strike, build.long_call_strike, ladder[0])

    res = rcs.execute_condor_walk(broker, account_hash, rcs.SIDE_OPEN, legs, quantity, ladder,
                                  interval_seconds=10, leave_resting_at_mid=True)
    if not res.ok:
        # Nunca se colocó: la fila se cierra como descartada (P&L None → no ensucia el win rate).
        repo.close_real_condor_position(conn, pos_id, date.today(), close_value=None,
                                        close_reason="no_colocada", realized_pnl=None,
                                        close_ts=datetime.now())
        logger.warning("Condor-real: la apertura id=%s NO se llegó a colocar (%s) — descartada, se reintenta",
                       pos_id, res.error)
        return

    entry_ps = res.fill_price if (res.filled and res.fill_price is not None) else (res.final_limit_price or (build.net_credit / 100.0))
    entry_total = round(entry_ps * 100.0 * quantity, 2)
    status = "open" if res.filled else "working"
    repo.update_real_condor_open_order(conn, pos_id, open_schwab_order_id=res.order_id, status=status)
    if res.filled:
        repo.mark_real_condor_fill(conn, pos_id, entry_credit_ps=round(entry_ps, 2),
                                   entry_net_credit=entry_total, open_schwab_order_id=res.order_id,
                                   entry_ts=datetime.now())
    _log_open_for_learning(conn, as_of, pos_id, build, spot, signal, entry_total, vix_chg)
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
