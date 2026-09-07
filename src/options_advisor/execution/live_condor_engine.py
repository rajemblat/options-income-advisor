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

from options_advisor.execution import real_condor_sender as rcs
from options_advisor.execution import schwab_orders as so
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


def _a_la_grilla(precios: list[float], *, hacia_abajo: bool) -> list[float]:
    """Lleva cada peldaño a la grilla de 5 centavos que exige SPX y saca los repetidos, conservando el
    orden. La escalera camina de 5 en 5 pero el ÚLTIMO peldaño se clava en el mid exacto, que casi
    nunca cae en la grilla: así nació el $1.82 que Schwab rechazó el 2026-09-02. `build_iron_condor_*`
    igual redondea antes de mandar; acá se hace también para que el precio que el robot GUARDA y
    LOGUEA sea el mismo que el que sale, y no dos números distintos en la auditoría."""
    vistos: list[float] = []
    for precio in precios:
        ajustado = so.redondear_a_tick(precio, hacia_abajo=hacia_abajo)
        if ajustado > 0 and ajustado not in vistos:
            vistos.append(ajustado)
    return vistos


# ═══ SE OPERA AL PRECIO QUE EL MERCADO OFRECE, NO AL MID (usuario 2026-09-02) ═══
#
# "cuando yo entro, generalmente entro a la prima que me da pero en limit, no espero el mid, porque
# el SPX maneja muy poca spread entre el ask y el bid" — y para cerrar, lo mismo.
#
# Antes las dos escaleras caminaban HASTA EL MID y la orden se quedaba puesta esperando. Eso fue lo
# que costó los $295 del 02/09: la orden de apertura quedó al mid a las 09:30 y recién llenó a las
# 10:08, en un mercado que ya no era el que la había justificado. En paralelo, el simulador daba esa
# misma entrada por hecha al instante y cobraba +$36 a las 09:43. Mismos strikes, mismo crédito,
# resultado opuesto — la diferencia entera era el momento del fill.
#
# Ahora la escalera tiene UN SOLO peldaño, el ejecutable: se vende al bid y se compran las alas al
# ask (crédito), o se recompra al ask y se venden las alas al bid (débito). La orden entra ya. Se
# cobra ~$12 menos por condor —el 02/09: $165 realizable contra $177.50 al mid— y a cambio no queda
# ninguna orden colgada decidiendo por su cuenta media hora después. En SPX, con el spread que
# maneja, esa certeza vale mucho más que los 12 dólares.
#
# La escalera sigue siendo una lista porque el walk la espera así, y porque un solo peldaño es
# además lo que elimina los reemplazos: sin reemplazos no hay ids nuevos, y sin ids nuevos no hay
# forma de perder una orden vieja viva.

def _credito_realizable(sp, sc, lp, lc) -> float:
    """Crédito que el mercado paga AHORA por el condor: se venden los cortos al bid y se compran las
    alas al ask."""
    return (sp.bid + sc.bid) - (lp.ask + lc.ask)


def _debito_realizable(sp, sc, lp, lc) -> float:
    """Débito que cuesta salir AHORA: se recompran los cortos al ask y se venden las alas al bid."""
    return (sp.ask + sc.ask) - (lp.bid + lc.bid)


def _open_credit_ladder(sp, sc, lp, lc) -> list[float]:
    """Precio de APERTURA: el crédito ejecutable, en la grilla de 5 centavos hacia ABAJO (pedir 4
    centavos menos llena; un número fuera de la grilla lo rechaza Schwab). Lista vacía si el condor
    no paga crédito positivo — ahí no hay operación."""
    credito = _credito_realizable(sp, sc, lp, lc)
    if credito <= 0:
        return []
    return _a_la_grilla([credito], hacia_abajo=True)


def _close_debit_ladder(sp, sc, lp, lc) -> list[float]:
    """Precio de CIERRE: el débito ejecutable, en la grilla hacia ARRIBA. Piso duro de $0.05 — una
    orden de cierre a débito neto ≤ 0 no la acepta Schwab, y salir siempre pesa más que ahorrar
    centavos."""
    debito = _debito_realizable(sp, sc, lp, lc)
    ladder = _a_la_grilla([debito], hacia_abajo=False)
    return [max(round(p, 2), _MIN_CLOSE_DEBIT) for p in ladder] or [_MIN_CLOSE_DEBIT]


def _email(subject: str, body: str) -> None:
    try:
        from options_advisor.alerts import notifier
        notifier.send_email_robot_real(subject, body)
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

    # 1b) Última red: ¿la cuenta tiene patas de SPX que el robot no conoce? (ver la función)
    _huerfanas = _barrer_posiciones_huerfanas(conn, broker, as_of)

    # --- A partir de acá, ABRIR uno nuevo (requiere armado PROPIO + señal + cupo) ---
    # Autorización SEPARADA de los naked (usuario 2026-08-13): el condor real tiene su propio botón de
    # START del día. Sin ese START, gestiona/cierra lo abierto pero NO abre condors nuevos.
    if not repo.is_condor_live_armed(conn, as_of):
        return
    # Pausa PROPIA del condor real (usuario 2026-08-14) además de la compartida con el papel y la maestra.
    # Cualquiera de las tres frena SOLO aperturas nuevas — lo abierto ya se gestionó arriba.
    if repo.is_condor_real_paused(conn) or repo.is_condor_paused(conn) or repo.is_all_paused(conn):
        return
    # ═══ SI HAY ALGO 0DTE QUE EL ROBOT NO PUEDE CUIDAR, NO ABRE NADA MÁS ═══
    # Usuario 2026-09-02, después de perder $420 en un día: "eso debe funcionar al 100%, si no no
    # debe abrir por seguridad". Una pata de SPX que vence hoy y que el robot no tiene registrada es
    # una posición sin stop. Agregarle otra encima es duplicar el riesgo que ya no se está midiendo.
    if _huerfanas:
        logger.error("Condor-real: NO se abre — hay %d pata(s) 0DTE de SPX en la cuenta que el robot "
                     "no tiene registradas. Primero hay que resolver eso.", _huerfanas)
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

    # CHECKLIST DE DESPEGUE (usuario 2026-08-31: "debe verificar que el stop funciona").
    # No se abre nada que después no se pueda cuidar. Ver `puede_cuidar_la_posicion`.
    _ok, _porque = puede_cuidar_la_posicion(conn)
    if not _ok:
        logger.warning("Condor-real: NO se abre — %s. El stop de $%.0f lo ejecuta el robot, así que "
                       "sin visión no habría protección.", _porque, getattr(cfg, "stop_loss_dollars", 0) or 0)
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
            # Edad REAL de la orden, en minutos (auditoria 2026-08-22).
            #
            # Bug que tenia: anclaba en `entry_date` + "T00:00:00", o sea la MEDIANOCHE de hoy, no el
            # momento del envio. Durante el mercado eso da entre 570 y 960 minutos, siempre mayor que
            # _SENDING_GRACE_MINUTES, asi que la ventana de gracia NUNCA se aplicaba: una orden recien
            # colocada que todavia estaba negociandose se declaraba "no confirmada" en el primer tick.
            # `entry_ts` guarda fecha Y hora — es el campo correcto; `entry_date` queda de respaldo
            # para filas viejas que no lo tengan.
            _edad = None
            _sello = row["entry_ts"] if ("entry_ts" in row.keys() and row["entry_ts"]) else None
            if _sello:
                try:
                    _edad = (datetime.now() - datetime.fromisoformat(str(_sello))).total_seconds() / 60.0
                except (ValueError, TypeError):
                    _edad = None
            if _edad is None and row["entry_date"]:
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



# Clave del aviso de posiciones huérfanas: lleva la fecha, así el aviso sale UNA vez por día y no
# se convierte en un mail por minuto mientras la posición siga ahí.
def _clave_huerfanas(as_of: date) -> str:
    return f"condor_real_huerfanas_{as_of.isoformat()}"


def _vence_hoy(occ_symbol: str, as_of: date) -> bool:
    """¿Esta pata vence HOY? Se lee del propio simbolo OCC (root de 6 + YYMMDD en [6:12]), que es el
    formato estable — nunca del texto de la descripcion.

    El barrido de huerfanas solo mira 0DTE porque el condor real SOLO opera 0DTE. El usuario tiene
    ademas spreads de SPX propios con vencimientos mas largos (bull puts y bear calls a 12-15 dias):
    esos no son del robot, el robot no tiene por que gestionarlos, y avisarle todos los dias de algo
    que el abrio a proposito convierte la alerta en ruido — y una alerta que se ignora no sirve para
    nada el dia que importa."""
    if len(occ_symbol) < 12:
        return False
    try:
        return datetime.strptime(occ_symbol[6:12], "%y%m%d").date() == as_of
    except ValueError:
        return False


def _barrer_posiciones_huerfanas(conn, broker, as_of: date) -> int:
    """¿Hay patas de SPX vivas en la cuenta que el robot NO tenga registradas? Avisar.

    El 2026-09-02, con dinero real: Schwab rechazó la apertura del condor por el precio fuera de la
    grilla de 5 centavos, el robot la dio por no llenada y borró la fila. La posición terminó viva
    igual. Durante casi una hora hubo un iron condor abierto en la cuenta que el robot no marcaba,
    no le calculaba P&L y —lo grave— no le aplicaba el stop. Lo cerró el usuario a mano, en pérdida.

    Las verificaciones que ya existían miran ÓRDENES (`_buscar_orden_en_schwab`). Esta mira lo único
    que no se puede discutir: las POSICIONES que hay en la cuenta ahora mismo. Es la última red.

    No adopta sola a propósito: sin saber a qué crédito entró, cualquier stop o P&L que calculara
    sería inventado. Avisa, con las patas exactas, para que el usuario decida."""
    try:
        posiciones = broker.get_all_positions()
    except Exception:  # noqa: BLE001
        logger.debug("Condor-real: no se pudieron leer las posiciones de la cuenta", exc_info=True)
        return 0
    if not posiciones:
        return 0

    conocidas: set[str] = set()
    try:
        for fila in repo.get_open_real_condor_positions(conn) or []:
            for col in ("short_put_symbol", "long_put_symbol", "short_call_symbol", "long_call_symbol"):
                try:
                    sym = fila[col]
                except (IndexError, KeyError):
                    sym = None
                if sym:
                    conocidas.add(str(sym).replace(" ", ""))
    except Exception:  # noqa: BLE001
        logger.debug("Condor-real: no se pudieron listar las patas conocidas", exc_info=True)
        return 0

    huerfanas = []
    for pos in posiciones:
        if (getattr(pos, "asset_type", "") or "").upper() != "OPTION":
            continue
        if not float(getattr(pos, "quantity", 0) or 0):
            continue
        sub = (getattr(pos, "underlying_symbol", "") or "").upper().lstrip("$")
        sym = str(getattr(pos, "symbol", "") or "")
        if sub not in ("SPX", "SPXW") and not sym.upper().startswith(("SPX", "SPXW")):
            continue   # los naked del robot son de acciones y los lleva otro registro
        if not _vence_hoy(sym, as_of):
            continue   # el condor real es 0DTE; lo demas es del usuario (ver `_vence_hoy`)
        if sym.replace(" ", "") in conocidas:
            continue
        huerfanas.append(pos)

    if not huerfanas:
        return 0

    detalle = "\n".join(
        f"  · {getattr(h, 'symbol', '?')}  cantidad {getattr(h, 'quantity', 0):+g}  "
        f"P&L no realizado ${getattr(h, 'unrealized_pnl', 0.0):+,.2f}"
        for h in huerfanas
    )
    logger.error("Condor-real: HAY %d pata(s) de SPX en la cuenta que el robot NO tiene registradas "
                 "— sin stop ni gestión:\n%s", len(huerfanas), detalle)

    clave = _clave_huerfanas(as_of)
    try:
        if repo.get_robot_flag(conn, clave, "0") == "1":
            return len(huerfanas)   # ya se avisó hoy (el aviso es 1 por día; el freno, permanente)
        repo.set_robot_flag(conn, clave, "1")
    except Exception:  # noqa: BLE001
        logger.debug("Condor-real: no se pudo marcar el aviso de huérfanas", exc_info=True)
        return len(huerfanas)

    _email("🔴 Lokshn: hay una posición de SPX en tu cuenta que el robot NO está gestionando",
           "El robot encontró patas de SPX abiertas en tu cuenta que no figuran en su registro.\n\n"
           f"{detalle}\n\n"
           "Eso significa que esa posición NO tiene el stop del robot ni objetivo de ganancia: "
           "nadie la está cuidando.\n\n"
           "Pasó el 2026-09-02: una orden que Schwab había marcado como rechazada terminó "
           "ejecutándose igual. Si esto es eso, cerrala vos a mano desde el broker o avisale al "
           "robot. NO la adopta solo porque no sabe a qué precio entró, y con un crédito inventado "
           "el stop también saldría inventado.\n\n"
           "Mientras esa posición siga ahí, el robot NO abre condors nuevos.")
    return len(huerfanas)


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
                _reconcile_working_open(conn, broker, account_hash, row, cfg)
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


def _minutos_de_la_orden(info) -> float | None:
    """Hace cuántos minutos Schwab recibió esta orden, según el propio broker (`enteredTime`).
    None si no vino el dato o no se puede leer — sin dato no se cancela nada."""
    crudo = info.get("enteredTime") or info.get("enteredTime".lower()) or ""
    if not crudo:
        return None
    try:
        from datetime import timezone
        texto = str(crudo).replace("Z", "+00:00")
        # Schwab manda el offset sin dos puntos ("+0000"). Python 3.11 lo acepta; 3.10 no, y el
        # servidor de Debian corre 3.13 pero los tests pueden correr en otra versión. Normalizamos.
        if len(texto) >= 5 and texto[-5] in "+-" and texto[-3] != ":":
            texto = texto[:-2] + ":" + texto[-2:]
        entrada = datetime.fromisoformat(texto)
        if entrada.tzinfo is None:
            entrada = entrada.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - entrada).total_seconds() / 60.0
    except (ValueError, TypeError):
        return None


def _caducar_apertura_colgada(broker, account_hash, row, info, cfg) -> bool:
    """¿Esta apertura lleva demasiado tiempo puesta sin llenar? Si sí, la CANCELA y devuelve True.

    El 2026-09-02 una orden de apertura quedó colgada 38 minutos —de 09:30 a 10:08— y llenó recién
    ahí, en un mercado que ya no era el que la había justificado. El robot había decidido esa entrada
    con los precios de las 09:30; a las 10:08 el SPX se había movido y nadie volvió a preguntarse si
    la entrada seguía teniendo sentido. Entró igual, y salió por stop loss 14 minutos después.

    Una orden puesta no es gratis: es una decisión vieja esperando ejecutarse. Pasado el plazo se
    cancela. NO se cierra la fila acá a propósito — el próximo tick va a leer CANCELED y va a pasar
    por `_buscar_orden_en_schwab` antes de descartar nada, que es la ruta que ya sabe distinguir
    "no llenó" de "llenó justo mientras salía el cancel"."""
    tope = float(getattr(cfg, "open_working_max_minutes", 0.0) or 0.0)
    if tope <= 0:
        return False
    minutos = _minutos_de_la_orden(info)
    if minutos is None or minutos < tope:
        return False
    try:
        broker.cancel_order(account_hash, row["open_schwab_order_id"])
    except Exception:  # noqa: BLE001
        logger.exception("Condor-real: no se pudo cancelar la apertura vieja id=%s (se reintenta)", row["id"])
        return False
    logger.warning("Condor-real: apertura id=%s llevaba %.1f min puesta sin llenar (tope %.1f) — "
                   "CANCELADA. Los precios que la justificaron ya no son los de ahora; si la "
                   "oportunidad sigue, se rearma con la cadena de este momento.",
                   row["id"], minutos, tope)
    _email("🟡 Lokshn canceló una apertura de Iron Condor que llevaba mucho puesta",
           f"La orden para abrir el condor {row['underlying']} "
           f"(put {row['short_put_strike']:.0f} / call {row['short_call_strike']:.0f}) llevaba "
           f"{minutos:.0f} minutos esperando fill y se canceló.\n\n"
           "Motivo: una orden vieja llena con precios de hace media hora, en un mercado que ya "
           "cambió. Si la oportunidad sigue en pie, el robot la vuelve a armar con precios de ahora.")
    return True


def _reconcile_working_open(conn, broker, account_hash, row, cfg=None) -> None:
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
    # Una apertura que sigue viva pero lleva demasiado puesta se cancela. Solo si NO llenó y NO está
    # ya muerta: si llenó, gana el fill; si está muerta, la resuelve el bloque de abajo.
    if cfg is not None and status not in ("FILLED", "REJECTED", "CANCELED", "EXPIRED"):
        if _caducar_apertura_colgada(broker, account_hash, row, info, cfg):
            return
    if status == "FILLED":
        qty = row["quantity"] or 1
        # El crédito REAL de la ejecución manda sobre el límite guardado al mandar la orden: es el que
        # fija el objetivo de ganancia y el stop loss de esta posición (2026-09-03: se mandó a $1.65 y
        # llenó a $1.75). Si Schwab todavía no publicó las ejecuciones, se cae al límite guardado.
        _real_ps = rcs.extract_condor_net_fill_price(info, rcs.SIDE_OPEN)
        credit_ps = row["entry_credit_ps"] if row["entry_credit_ps"] is not None else (
            (row["entry_net_credit"] or 0.0) / (100.0 * qty))
        if _real_ps is not None:
            if abs(_real_ps - (credit_ps or 0.0)) >= 0.005:
                logger.warning("Condor-real: apertura id=%s llenó a $%.2f, no al límite $%.2f — se "
                               "registra el crédito REAL", row["id"], _real_ps, credit_ps or 0.0)
            credit_ps = _real_ps
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
        # NUNCA se descarta una apertura con UNA sola lectura.
        #
        # El 2026-08-28, con dinero real: el robot dejó la orden puesta a las 09:38:58, leyó
        # "REJECTED" 34 segundos después y tiró la fila como `apertura_no_llenó`. La orden llenó
        # igual, a las 09:41:06 y a $1.95. La posición quedó VIVA dos horas —sin stop de $100, sin
        # objetivo de ganancia, sin nadie mirándola— hasta que el usuario la cerró a mano. Ese día
        # la red venía a los tumbos (2845 errores de conexión entre las 09 y las 11), así que la
        # lectura de estado bien pudo ser basura; y cuando se reemplaza una orden, el id viejo puede
        # quedar muerto mientras el reemplazo sigue vivo.
        #
        # Antes de dar por muerta una apertura le preguntamos a Schwab lo único que importa: ¿hay
        # una orden LLENADA con estas 4 patas exactas? Es la MISMA verificación que ya se usa para
        # las filas en 'sending'; lo que faltaba era usarla también acá.
        adoptada = _buscar_orden_en_schwab(broker, row)
        if adoptada is not None:
            credito_ps, oid_real = adoptada
            qty = row["quantity"] or 1
            total = round(credito_ps * 100.0 * qty, 2)
            repo.mark_real_condor_fill(conn, row["id"], entry_credit_ps=round(credito_ps, 2),
                                       entry_net_credit=total,
                                       open_schwab_order_id=(oid_real or oid),
                                       entry_ts=datetime.now())
            logger.warning("Condor-real: la apertura id=%s figuraba %s pero Schwab confirma que "
                           "LLENÓ (orden %s, crédito $%.2f) — ADOPTADA, queda bajo gestión",
                           row["id"], status, oid_real, total)
            _email("🟢 Lokshn recuperó un Iron Condor REAL que se había dado por rechazado",
                   f"La apertura del condor {row['underlying']} figuraba como {status}, pero en "
                   f"Schwab sí se ejecutó (crédito ${total:,.2f}).\n\n"
                   "El robot la volvió a tomar bajo su gestión: ya tiene stop y objetivo de "
                   "ganancia activos. No hace falta que hagas nada.")
            return

        detalle = str(info.get("statusDescription") or "")[:400]
        repo.close_real_condor_position(conn, row["id"], date.today(),
                                        close_value=None, close_reason="apertura_no_llenó",
                                        realized_pnl=None, close_ts=datetime.now())
        logger.warning("Condor-real: apertura id=%s %s sin llenar — descartada%s",
                       row["id"], status, f" — {detalle}" if detalle else "")
        _email("⚠️ Lokshn NO pudo abrir el Iron Condor REAL de hoy",
               f"El robot quiso abrir un condor {row['underlying']} "
               f"(put {row['short_put_strike']:.0f} / call {row['short_call_strike']:.0f}, "
               f"alas {row['long_put_strike']:.0f}/{row['long_call_strike']:.0f}) y el broker "
               f"no lo dejó.\n\n"
               f"Estado: {status}\n"
               f"Motivo que dio Schwab: {detalle or '(no dio motivo)'}\n\n"
               "El robot verificó con Schwab que NO quedó ninguna posición abierta con esas patas. "
               "Igual, si querés estar seguro, mirá tus posiciones en el broker.")


# Cuánto se tiene que mover el precio objetivo para molestarse en reemplazar la orden puesta. Menos
# que esto es ruido del mid y reemplazar solo agrega churn y riesgo de carrera.
_CAMBIO_MINIMO_PARA_REEMPLAZAR = 0.05


def _gestionar_cierre_puesto(conn, broker, account_hash, row, oid, precio_puesto,
                             entry_total, qty, chain, cfg) -> str:
    """Resuelve la recompra que quedó PUESTA en el broker. Devuelve 'llenó', 'sigue_viva' o 'murió'.

    Antes el motor cancelaba la orden cada tick y ponía otra al mismo precio un minuto después. Eso
    es lo que el usuario marcó el 24/08 —"si modifica el precio sí, el mismo precio no es
    necesario"— y además es lo que abrió la carrera de ese día: la orden llenó justo mientras salía
    el cancel y el robot la dio por no llenada.

    Ahora la orden vive entre ticks. Acá se la sondea y solo se la reemplaza si el precio objetivo
    se movió de verdad. Ante cualquier error de red se la deja quieta: una orden viva en el broker
    es más segura que una cancelada a ciegas."""
    try:
        info = broker.get_order(account_hash, oid)
    except Exception:  # noqa: BLE001
        logger.debug("Condor-real: no se pudo sondear la recompra puesta de id=%s", row["id"], exc_info=True)
        return "sigue_viva"   # sin dato, no se toca

    estado = (info.get("status") or "").upper()

    if estado == "FILLED":
        # Lo que se PAGÓ de verdad por recomprar, no el límite al que quedó puesta la orden. Va directo
        # al P&L realizado de la operación.
        _real_ps = rcs.extract_condor_net_fill_price(info, rcs.SIDE_CLOSE)
        debito_ps = _real_ps if _real_ps is not None else (precio_puesto if precio_puesto is not None else 0.0)
        if _real_ps is not None and precio_puesto is not None and abs(_real_ps - precio_puesto) >= 0.005:
            logger.info("Condor-real: la recompra puesta de id=%s llenó a $%.2f (límite $%.2f) — manda "
                        "el débito REAL", row["id"], _real_ps, precio_puesto)
        debito_total = round(debito_ps * 100.0 * qty, 2)
        realizado = round(entry_total - debito_total - settings_commission(cfg), 2)
        motivo = "manual" if _manual_close_requested(row) else "profit_target"
        repo.close_real_condor_position(conn, row["id"], date.today(), debito_total, motivo,
                                        realizado, close_ts=datetime.now(), close_schwab_order_id=oid)
        repo.set_real_condor_close_working(conn, row["id"], None, None)
        _reset_cierres_fallidos(conn, row["id"])
        logger.warning("Condor-real: id=%s CERRADO — la recompra que estaba puesta LLENÓ a $%.2f — "
                       "P&L $%.2f", row["id"], debito_ps, realizado)
        signo = "🟢" if realizado >= 0 else "🔴"
        _email(f"{signo} Lokshn CERRÓ un Iron Condor REAL — P&L ${realizado:+,.2f}",
               f"Iron Condor {row['underlying']} cerrado (motivo: {motivo}).\n"
               f"Crédito abierto ${entry_total:,.2f} → costo de cierre ${debito_total:,.2f}.\n"
               f"Resultado: ${realizado:+,.2f}.")
        return "llenó"

    if estado in ("CANCELED", "REJECTED", "EXPIRED"):
        _detalle = str(info.get("statusDescription") or "")[:300]
        repo.set_real_condor_close_working(conn, row["id"], None, None)
        logger.warning("Condor-real: la recompra puesta de id=%s murió (%s)%s — se vuelve a intentar",
                       row["id"], estado, f" — {_detalle}" if _detalle else "")
        return "murió"

    # Sigue viva. ¿Cambió el precio objetivo lo suficiente como para reemplazarla?
    if chain is None or precio_puesto is None:
        return "sigue_viva"
    q = _combo_quotes(chain, row["short_put_strike"], row["short_call_strike"],
                      row["long_put_strike"], row["long_call_strike"])
    if q is None:
        return "sigue_viva"
    objetivo = _close_debit_ladder(*q)[-1]   # el mid: donde termina la escalera
    if abs(objetivo - precio_puesto) < _CAMBIO_MINIMO_PARA_REEMPLAZAR:
        return "sigue_viva"   # el mercado no se movió: se la deja trabajar

    legs = _legs_de_la_fila(row)
    if legs is None:
        return "sigue_viva"
    try:
        nuevo_oid = broker.replace_order(
            account_hash, oid,
            so.build_iron_condor_close(legs.short_put_symbol, legs.long_put_symbol,
                                       legs.short_call_symbol, legs.long_call_symbol,
                                       quantity=qty, net_debit_limit=objetivo))
    except Exception:  # noqa: BLE001
        logger.exception("Condor-real: no se pudo reemplazar la recompra de id=%s (queda la vieja)", row["id"])
        return "sigue_viva"
    repo.set_real_condor_close_working(conn, row["id"], nuevo_oid, objetivo)
    logger.info("Condor-real: recompra de id=%s reajustada $%.2f → $%.2f (orden %s)",
                row["id"], precio_puesto, objetivo, nuevo_oid)
    return "sigue_viva"


def _legs_de_la_fila(row):
    """Las 4 patas OCC guardadas al abrir. None si falta alguna — nunca se reconstruyen a mano."""
    patas = [row["short_put_symbol"], row["long_put_symbol"],
             row["short_call_symbol"], row["long_call_symbol"]]
    if not all(patas) or len(set(patas)) != 4:
        return None
    return rcs.CondorLegs(short_put_symbol=patas[0], long_put_symbol=patas[1],
                          short_call_symbol=patas[2], long_call_symbol=patas[3])


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

    # ═══ LAS DOS SALIDAS SE MIDEN AL PRECIO REAL DE SALIDA, NO AL MID ═══
    #
    # EL STOP, desde el 2026-09-02: "deje que el stop loss es de 100 maximo 110, y cerro asi la
    # perdida de hoy" — y la pérdida fue de $125. No era un error de cuentas: el crédito ($95) y el
    # débito ($220) estaban bien anotados. El problema es que la posición se MEDÍA al mid y se SALÍA
    # al precio de verdad. Cuando el mid marcaba −$100, salir ya costaba −$125.
    #
    # EL OBJETIVO DE GANANCIA, desde el 2026-09-07: acá decía que la ganancia se seguía midiendo al
    # mid "porque para cobrar no hay apuro y la orden espera". Era falso: `_close_debit_ladder`
    # devuelve UN solo peldaño, el precio ejecutable, así que la orden sale y llena al instante —
    # nunca espera nada. El 2026-09-04, con dinero real: crédito $175, el objetivo disparó con el mid
    # en +$35 y salir costó $150 → +$25. Usuario: "el objetivo dijo 35 y cobré 25".
    #
    # Las dos miran ahora `condor_exit_value`: lo que cuesta salir YA (recomprar los cortos al ask,
    # vender las alas al bid). El stop dispara un poco antes y la pérdida cae dentro del límite; la
    # ganancia dispara un poco después y cobra lo que el porcentaje promete.
    unrealized_estricto = None
    if not expired:
        try:
            salida_pc = iron_condor.condor_exit_value(
                chain, row["short_put_strike"], row["short_call_strike"],
                row["long_put_strike"], row["long_call_strike"])
        except Exception:  # noqa: BLE001
            salida_pc = None
        if salida_pc is not None:
            unrealized_estricto = round(entry_total - round(salida_pc * qty, 2), 2)

    age_minutes = None
    if row["entry_ts"]:
        try:
            age_minutes = (datetime.now() - datetime.fromisoformat(row["entry_ts"])).total_seconds() / 60.0
        except (ValueError, TypeError):
            age_minutes = None

    # ¿Hay una recompra ya PUESTA en el broker de un tick anterior? Se resuelve primero: puede haber
    # llenado, puede seguir viva, o puede haber muerto. Ver `_gestionar_cierre_puesto`.
    _oid_vivo, _precio_vivo = repo.get_real_condor_close_working(row)
    if _oid_vivo:
        _estado = _gestionar_cierre_puesto(conn, broker, account_hash, row, _oid_vivo, _precio_vivo,
                                           entry_total, qty, chain, cfg)
        if _estado in ("llenó", "sigue_viva"):
            return

    do_close, reason = iron_condor.should_close_condor(unrealized, entry_total, expired, cfg,
                                                      age_minutes=age_minutes,
                                                      unrealized_de_salida=unrealized_estricto)
    if do_close and reason == "stop_loss" and unrealized_estricto is not None:
        logger.warning("Condor-real: STOP de id=%s — al mid la posición marca $%.2f, pero salir de "
                       "verdad cuesta $%.2f (esa es la que manda)", row["id"], unrealized,
                       unrealized_estricto)
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
    # leave_resting_at_mid=True: la recompra QUEDA PUESTA en el broker en vez de cancelarse.
    #
    # Antes iba en False: cada tick ponía una orden, la caminaba ~28 s y la CANCELABA, para volver a
    # poner otra al mismo precio un minuto después (usuario 2026-08-24: "lo sigue enviando más veces
    # en 1.25 en vez de dejarlo working... si modifica el precio sí, el mismo precio no es
    # necesario"). Ese ciclo cancelar/reponer es lo que abrió la carrera del 24/08, cuando la orden
    # llenó justo mientras salía el cancel y el robot no se enteró.
    #
    # Ahora se pone una sola vez y se la deja trabajar. El próximo tick la sondea (arriba) y solo la
    # reemplaza si el precio objetivo se movió de verdad.
    res = rcs.execute_condor_walk(broker, account_hash, rcs.SIDE_CLOSE, legs, qty, ladder,
                                  interval_seconds=10, leave_resting_at_mid=True)
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
        _reset_cierres_fallidos(conn, row["id"])
        repo.set_real_condor_close_working(conn, row["id"], None, None)
    else:
        # No llenó AHORA, pero la orden quedó PUESTA: se guarda para sondearla el próximo tick en
        # vez de mandar otra. Si murió (rechazada/cancelada) no se guarda nada y se reintenta limpio.
        if res.order_id and res.status not in ("CANCELED", "REJECTED", "EXPIRED"):
            repo.set_real_condor_close_working(conn, row["id"], res.order_id, res.final_limit_price)
            logger.info("Condor-real: recompra de id=%s queda PUESTA a $%.2f (orden %s)",
                        row["id"], res.final_limit_price or 0.0, res.order_id)
        repo.mark_real_condor_position(conn, row["id"], datetime.now(), unrealized)
        _detalle = getattr(res, "status_detalle", "") or ""
        logger.warning("Condor-real: la recompra de id=%s no llenó (%s); se reintenta%s",
                       row["id"], res.status, f" — {_detalle}" if _detalle else "")
        _avisar_si_el_cierre_no_entra(conn, row, res, unrealized)


_CIERRES_FALLIDOS_PARA_AVISAR = 3


def _clave_cierres_fallidos(pos_id) -> str:
    return f"condor_real.cierres_fallidos.{pos_id}"


def _reset_cierres_fallidos(conn, pos_id) -> None:
    """El cierre entró: se borra la cuenta de intentos fallidos y el aviso queda rearmado."""
    try:
        repo.set_robot_flag(conn, _clave_cierres_fallidos(pos_id), "0")
    except Exception:  # noqa: BLE001
        logger.debug("Condor-real: no se pudo resetear el contador de cierres fallidos", exc_info=True)


def _avisar_si_el_cierre_no_entra(conn, row, res, unrealized: float) -> None:
    """Manda UN email cuando la recompra falla varias veces seguidas.

    El 2026-08-24 el motor intentó cerrar un condor real 8 veces en 40 minutos; Schwab rechazó todas
    y el usuario NO recibió ningún aviso, porque el email solo salía cuando el cierre entraba. Se
    enteró de casualidad, mirando su broker. Un cierre que no entra es exactamente el momento en que
    hay que avisar: la posición sigue viva y quiere salir.

    Se avisa una sola vez por posición (el contador solo se limpia cuando el cierre entra), para no
    convertir el problema en una lluvia de mails."""
    clave = _clave_cierres_fallidos(row["id"])
    try:
        fallos = int(repo.get_robot_flag(conn, clave, "0") or 0)
    except (TypeError, ValueError):
        fallos = 0
    fallos += 1
    try:
        repo.set_robot_flag(conn, clave, str(fallos))
    except Exception:  # noqa: BLE001
        logger.debug("Condor-real: no se pudo guardar el contador de cierres fallidos", exc_info=True)
        return
    if fallos != _CIERRES_FALLIDOS_PARA_AVISAR:
        return   # != y no >=: así sale UNA vez, no en cada intento posterior

    detalle = getattr(res, "status_detalle", "") or "(el broker no dio motivo)"
    _email(
        "⚠️ Lokshn NO puede cerrar un Iron Condor REAL",
        f"El robot intentó recomprar el condor {row['underlying']} "
        f"(put {row['short_put_strike']:.0f} / call {row['short_call_strike']:.0f}) "
        f"{fallos} veces seguidas y el broker no lo dejó.\n\n"
        f"Último estado: {res.status}\n"
        f"Motivo que dio Schwab: {detalle}\n"
        f"P&L no realizado ahora: ${unrealized:+,.2f}\n\n"
        "REVISÁ TU CUENTA EN SCHWAB. Si la posición ya no está ahí, el robot la cerró y no se "
        "enteró: avisale para que actualice su registro. Si sigue abierta, el condor es 0DTE y "
        "vence hoy — decidí vos si la cerrás a mano.",
    )
    logger.error("Condor-real: id=%s lleva %s cierres rechazados seguidos — avisado por mail",
                 row["id"], fallos)


# Horas de vida que le tienen que quedar al refresh_token para animarse a abrir. El condor es 0DTE:
# se abre y se cierra el mismo día, casi siempre en menos de dos horas. Con 3 horas de margen, una
# posición abierta ahora llega holgada al cierre con el robot todavía viendo.
_HORAS_DE_TOKEN_PARA_ABRIR = 3.0


def puede_cuidar_la_posicion(conn=None) -> tuple[bool, str]:
    """¿Está el robot en condiciones de VIGILAR un condor si lo abre ahora? (motivo si no).

    El stop de $100 NO es una orden puesta en Schwab: lo dispara el robot, mirando el precio cada
    minuto. Si el robot no ve, el stop no existe — y el usuario se queda con hasta $835 de riesgo
    sin protección, creyendo que está cubierto.

    Esta semana pasó tres veces (2026-08-27, 28 y 31): cortes de red y el token vencido dejaron al
    robot ciego con el mercado abierto. Ninguna de esas veces había un condor abierto, pero fue
    suerte. Regla del usuario (2026-08-31): "no puede abrir sin stop loss" — y el stop solo funciona
    si el robot puede ver.

    Solo condiciona ABRIR. Cerrar, marcar y gestionar lo que ya está abierto no pasa por acá: si ya
    hay una posición viva, el robot tiene que seguir intentando cuidarla aunque la red venga mal."""
    from options_advisor.broker import conectividad
    from options_advisor.broker.schwab_auth import read_refresh_token_seconds_left

    fallos, _ultimo_exito, _err = conectividad.estado()
    # OJO: `estado()` devuelve el TIMESTAMP del último éxito, no los segundos transcurridos. Para
    # eso está `segundos_sin_exito()` (bug atrapado al escribir esto: comparar el timestamp contra
    # 300 daba siempre "hace 29 millones de minutos" y el condor no habría abierto nunca más).
    sin_exito = conectividad.segundos_sin_exito()
    if conectividad.esta_ciego():
        return False, "el robot está sin conexión con Schwab"
    if fallos >= 5:
        return False, f"{fallos} fallos de red seguidos — la conexión viene inestable"
    if sin_exito is not None and sin_exito > 300:
        return False, f"hace {sin_exito / 60:.0f} min que ninguna llamada a Schwab funciona"

    try:
        quedan = read_refresh_token_seconds_left()
    except Exception:  # noqa: BLE001
        quedan = None
    if quedan is not None:
        if quedan <= 0:
            return False, "el token de Schwab está vencido"
        if quedan < _HORAS_DE_TOKEN_PARA_ABRIR * 3600:
            return False, (f"al token de Schwab le quedan {quedan / 3600:.1f} h — no alcanza para "
                           "vigilar la posición hasta el cierre")
    return True, ""


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


def _rescatar_orden_viva(broker, account_hash, res):
    """De TODOS los ids que creó el walk, ¿hay alguno que llenó o que sigue vivo? Devuelve
    (id, estado, precio_o_None) o None si están todos muertos de verdad.

    El 2026-09-02, con dinero real: la escalera hizo 1.95 → 1.90 → 1.85 → 1.82. En Schwab cada
    reemplazo es una orden NUEVA. El reemplazo a $1.82 lo rechazaron por la grilla de 5 centavos y
    el walk devolvió REJECTED — pero la orden de $1.85 seguía VIVA y llenó media hora después. El
    robot solo miró el último id, dio la fila por muerta y la borró. Quedó un iron condor abierto en
    la cuenta sin stop, sin objetivo y sin nadie mirándolo hasta que el usuario lo cerró a mano.

    Un id muerto no es prueba de que no haya orden viva. Se preguntan TODOS, del más nuevo al más
    viejo: un fill gana siempre; si no hay fill pero queda una viva, esa pasa a ser la orden de la
    fila y la posición sigue bajo vigilancia. Recién si están todas muertas se descarta."""
    viva = None
    for oid in reversed(res.order_ids or []):
        try:
            info = broker.get_order(account_hash, oid)
        except Exception:  # noqa: BLE001
            logger.debug("Condor-real: no se pudo sondear la orden %s del walk", oid, exc_info=True)
            viva = viva or (oid, "DESCONOCIDA", None)   # sin respuesta NO se da por muerta
            continue
        estado = (str(info.get("status") or "")).upper()
        if estado == "FILLED":
            return oid, "FILLED", info
        if estado not in ("REJECTED", "CANCELED", "EXPIRED", "REPLACED"):
            viva = viva or (oid, estado or "WORKING", info)
    return viva


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

    # ═══ PISO DURO DE CRÉDITO (usuario 2026-09-02) ═══
    #
    # "tampoco puede abrir con esa prima de .95". Ese día el robot abrió un condor cobrando $95
    # contra $905 de riesgo máximo — 1 a 9.5, cuando los que venía armando pagaban entre $165 y $195
    # (1 a 5). `min_credit` no lo frenó porque estaba en 0, y además es una perilla que el
    # aprendizaje puede bajar sola. Este piso es aparte y el aprendizaje NO lo toca.
    #
    # Se mide contra el ÚLTIMO peldaño de la escalera (el mid), que es el crédito más BAJO que
    # aceptaríamos: si ni siquiera ese llega al piso, la operación no vale el riesgo. Mirar el
    # primer peldaño sería engañarse — ese precio casi nunca llena.
    _piso = float(getattr(cfg, "live_min_credit", 0.0) or 0.0)
    if _piso > 0:
        _credito_peor_caso = round(ladder[-1] * 100.0 * quantity, 2)
        if _credito_peor_caso < _piso:
            logger.warning("Condor-real: NO se abre — el crédito que se llegaría a cobrar ($%.2f) no "
                           "alcanza el piso de $%.2f (put %.0f/call %.0f)", _credito_peor_caso, _piso,
                           build.short_put_strike, build.short_call_strike)
            return
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

    # ═══ NINGUNA ORDEN DEL WALK SE PIERDE (usuario 2026-09-02) ═══
    # Si el walk terminó con el último id muerto, se preguntan todos los demás antes de seguir: uno
    # de ellos puede estar vivo (o haber llenado). Ver `_rescatar_orden_viva`.
    if not res.filled and (res.status or "").upper() in ("REJECTED", "CANCELED", "EXPIRED"):
        rescate = _rescatar_orden_viva(broker, account_hash, res)
        if rescate is not None:
            oid_vivo, estado_vivo, _info = rescate
            logger.warning("Condor-real: el walk de id=%s terminó %s, pero la orden %s sigue %s — "
                           "esa pasa a ser la orden de la fila. NO se descarta nada.",
                           pos_id, res.status, oid_vivo, estado_vivo)
            res.order_id = oid_vivo
            if estado_vivo == "FILLED":
                res.filled = True
                res.status = "FILLED"
            else:
                res.status = estado_vivo
        else:
            # Todas muertas: se cancela lo que pudiera quedar colgado y se descarta limpio.
            logger.warning("Condor-real: todas las órdenes del walk de id=%s están muertas (%s)",
                           pos_id, ", ".join(res.order_ids or []) or "sin ids")

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
