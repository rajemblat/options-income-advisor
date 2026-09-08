"""Lazo de ejecución REAL (dry-run primero) que "sombrea" la decisión del simulador.

Reutiliza el MISMO cerebro de entrada del simulador (usuario 2026-08-09: "revisemos con un par de
contratos y cuando digas OK pasamos a real"): cuando el robot, con el día ARMADO, decide una entrada
sobre un símbolo de la whitelist real, este módulo arma el plan de orden REAL (guardián + caminar el
precio + payload de Schwab) y lo GUARDA en `live_order_log`. En dry-run no envía nada (`sent=0`).

Es un hook NO intrusivo: si algo falla, se loguea y no toca el flujo del simulador. El envío real (POST)
se enchufa como último paso, con el usuario presente, reemplazando el bloque marcado `# TODO ENVÍO REAL`.
"""

from __future__ import annotations

import json
import logging
import threading
import datetime as _dt   # fecha de apertura en el email (usuario 2026-08-19)
from dataclasses import replace
from datetime import date, datetime

from options_advisor.execution import live_guard
from options_advisor.execution.live_executor import Opportunity, WalkConfig, plan_live_order
from options_advisor.execution.live_guard import AccountSnapshot, DayState
from options_advisor.simulator import rules
from options_advisor.storage import repository as repo

logger = logging.getLogger(__name__)

# Serializa TODAS las operaciones que tocan órdenes REALES (mandar/re-preciar/cerrar/email), para que el
# nuevo job rápido del chat (cada ~15s, hilo propio) y el escaneo del robot (cada 1 min) NUNCA actúen sobre
# la misma orden a la vez — evita doble envío / doble cierre con plata real (usuario 2026-08-11: se pasó el
# procesamiento del chat a un job aparte para que salga en segundos, no al final del escaneo). Es un lock
# de PROCESO (ambos jobs viven en el mismo proceso del scheduler); reentrante por prudencia.
_LIVE_ORDER_LOCK = threading.RLock()


def _serialized(fn):
    """Corre `fn` bajo `_LIVE_ORDER_LOCK`: serializa las operaciones con órdenes reales entre el job rápido
    del chat y el escaneo del robot (nunca dos a la vez sobre la misma orden). Reentrante (RLock)."""
    from functools import wraps

    @wraps(fn)
    def _wrap(*args, **kwargs):
        with _LIVE_ORDER_LOCK:
            return fn(*args, **kwargs)

    return _wrap

# Cash "grande" para el dry-run: en Fase 1 el freno real es el COLATERAL total ($10k), no el cash libre
# (min_account_cash_buffer=0). El ejecutor REAL leerá el cash de la cuenta Schwab en su lugar.
_DRY_RUN_CASH = 1e9

_CLOSE_REASON_ES = {"profit_target": "objetivo de ganancia", "stop_loss": "stop-loss",
                    "dte_close": "cerca del vencimiento", "news_close": "noticia importante",
                    "expired": "vencida", "closed_in_broker": "cerrada en Schwab",
                    "manual_ai": "cierre manual (pedido por chat)"}


def _fmt_open_email(symbol, strike, expiration, contracts, fill_price, ctx, opened_at=None) -> tuple[str, str]:
    """Email de APERTURA real con el mismo detalle que las alertas de apertura (usuario 2026-08-10):
    subyacente, venta/strike/vto/prima, prima neta, beneficio máx, breakeven, cobertura, POP, DTE, anualizado."""
    ctx = ctx or {}
    credit = fill_price * 100.0 * contracts
    breakeven = strike - fill_price
    under = ctx.get("underlying_price")
    cov = ctx.get("chosen_coverage_pct")
    if not isinstance(cov, (int, float)) and isinstance(under, (int, float)) and under > 0:
        cov = (under - strike) / under
    pop = ctx.get("chosen_pop")
    dte = ctx.get("chosen_dte")
    ann = ctx.get("chosen_annualized_return")
    # CUÁNDO se abrió (usuario 2026-08-19: "me envió este correo hoy pero no abrí ningún DLO hoy").
    # El email se reintenta hasta que el SMTP acepta, así que puede llegar horas —o un día— después de
    # la apertura: el de DLO se abrió el 18/08 15:08 y llegó el 19/08 15:59. Sin la fecha adentro, el
    # correo parece una operación de hoy y no hay forma de darse cuenta.
    _abierta = None
    if opened_at:
        try:
            _abierta = _dt.datetime.fromisoformat(str(opened_at)).strftime("%d/%m/%Y %H:%M")
        except (ValueError, TypeError):
            _abierta = None
    L = ["✦ Operación Real EJECUTADA (Lokshn)", "", f"✧ {symbol} — Naked PUT"]
    if _abierta:
        L.append(f"• Abierta: {_abierta}")
    if isinstance(under, (int, float)):
        L.append(f"• Precio del subyacente: ${under:.2f}")
    L += ["──────────",
          f"↓ Venta · -{contracts} Put · Strike ${strike:g} · Vence {expiration} · Prima ${fill_price:.2f}",
          f"＄ Prima neta: ${credit:,.2f} (crédito)",
          f"▲ Beneficio máximo: ${credit:,.2f}", "",
          f"≡ Breakeven: ${breakeven:.2f} (ya incluye la prima)"]
    if isinstance(cov, (int, float)):
        L.append(f"↓ Cobertura: {cov * 100:.1f}% (necesita caer hasta ${breakeven:.2f})")
    if isinstance(pop, (int, float)):
        L.append(f"◎ Probabilidad de beneficio: {pop * 100:.0f}%")
    if isinstance(dte, (int, float)):
        L.append(f"○ DTE: {int(dte)} días")
    if isinstance(ann, (int, float)):
        L.append(f"↻ Rendimiento anualizado (sobre riesgo): {ann * 100:.1f}%")
    L += ["", "Mirá el detalle y puntuála en Real Market."]
    return f"🟢 Lokshn ABRIÓ {symbol} put {strike:g} — crédito ${credit:,.0f}", "\n".join(L)


_RESTING_OPEN_STATES = {"WORKING", "QUEUED", "ACCEPTED", "PENDING_ACTIVATION", "NEW", "PENDING_RECALL",
                        "AWAITING_MANUAL_REVIEW"}


def _fmt_working_email(symbol, strike, expiration, contracts, limit_price, ctx) -> tuple[str, str]:
    """Email cuando la orden real se ENVIÓ y quedó NEGOCIANDO (Working), todavía sin llenar (usuario
    2026-08-11: 'no me llegó la de NU'). El email del FILL llega aparte, cuando llene."""
    ctx = ctx or {}
    under = ctx.get("underlying_price")
    L = ["🟡 Orden REAL enviada — negociando (Lokshn)", "", f"✧ {symbol} — Naked PUT"]
    if isinstance(under, (int, float)):
        L.append(f"• Precio del subyacente: ${under:.2f}")
    L += ["──────────",
          f"↓ Venta · -{contracts} Put · Strike ${strike:g} · Vence {expiration}",
          f"Puesta a límite ${(limit_price or 0):.2f} — esperando que llene (el robot va ajustando el precio al mid).",
          "", "Te aviso de nuevo cuando LLENE. Mientras, la ves como Working en Real Market."]
    return f"🟡 Lokshn PUSO {symbol} put {strike:g} — {contracts} contrato(s), negociando", "\n".join(L)


def _fmt_close_email(symbol, strike, contracts, entry_price, close_price, reason, ctx, days_held) -> tuple[str, str]:
    """Email de CIERRE real: precio de cierre, profit $, % de profit y anualizado REAL (usuario 2026-08-10)."""
    ctx = ctx or {}
    realized = (entry_price - close_price) * 100.0 * contracts
    credit = entry_price * 100.0 * contracts
    pct = (realized / credit * 100.0) if credit else 0.0
    margin = ctx.get("chosen_margin")
    ann_real = None
    if isinstance(margin, (int, float)) and margin > 0 and days_held and days_held > 0:
        ann_real = (realized / margin) * (365.0 / days_held) * 100.0
    signo = "🟢" if realized >= 0 else "🔴"
    L = [f"{signo} Operación Real CERRADA (Lokshn)", "",
         f"✧ {symbol} — Naked PUT · Strike ${strike:g}", "──────────",
         f"Vendido a ${entry_price:.2f} → Recomprado a ${close_price:.2f} · {contracts} contrato(s)",
         f"Motivo: {_CLOSE_REASON_ES.get(reason, reason)}", "",
         f"Resultado: ${realized:+,.2f}  ({pct:+.1f}% de la prima)"]
    if ann_real is not None:
        L.append(f"↻ Anualizado REAL: {ann_real:+.1f}%")
    if days_held is not None:
        L.append(f"○ Días en la operación: {days_held}")
    L += ["", "Mirá el detalle en Real Market → Cerradas."]
    return f"{signo} Lokshn CERRÓ {symbol} put {strike:g} — P&L ${realized:+.2f} ({pct:+.0f}%)", "\n".join(L)


def is_real_active_today(conn, settings, day) -> bool:
    """¿El trading real está ACTIVO hoy? = el día está ARMADO, sin kill switch, y el sistema encendido
    (enabled) o en dry-run. Si es True, el motor debe evaluar la entrada AUNQUE el simulador esté en su
    tope, porque el real lleva su PROPIO conteo aparte (usuario 2026-08-10). Si es False, el hook real es
    un no-op y el motor puede cortar temprano (camino rápido)."""
    lt = settings.live_trading
    if not (lt.enabled or lt.dry_run):
        return False
    if repo.is_live_kill_switch(conn):
        return False
    return repo.is_live_armed(conn, day)


def _limits_with_runtime_flags(settings, conn, day: date):
    """LiveLimits de config + overrides en vivo del dashboard (kill switch y tope diario de órdenes).
    El override del tope diario vale solo para `day` — a la medianoche el robot vuelve al tope de config
    (usuario 2026-08-10, punto 2/5: resetear todos los días desde 0)."""
    lim = live_guard.limits_from_settings(settings.live_trading)
    if repo.is_live_kill_switch(conn):
        lim = replace(lim, kill_switch=True)
    _max_day = repo.get_max_live_orders_per_day(conn, lim.max_orders_per_day, day)
    if _max_day != lim.max_orders_per_day:
        lim = replace(lim, max_orders_per_day=_max_day)
    return lim


def contracts_for_strike(strike: float | None, lt) -> int:
    """Cuántos contratos PEDIR para este strike (usuario 2026-08-17: "cuando el strike es menos de $30
    debe abrir más cantidad, mínimo 4").

    Por qué existe: un put de strike $13 traba ~$88 de colateral por contrato y uno de $285 traba ~$2.500.
    Pidiendo 1 contrato siempre, la operación barata quedaba 28 veces más chica que la cara — cobraba $22
    y no movía la aguja. Con 4 contratos los tamaños quedan comparables.

    Esto es lo que se PIDE, no lo que se manda: el guardián después solo puede RECORTAR (por colateral,
    notional, cash de la cuenta o el techo `max_contracts_per_order`), nunca subir. Decisión del usuario
    2026-08-17: "el guardián manda" — la escala es un objetivo, no un permiso para saltear los frenos.

    Sin `cheap_strike_max`/`cheap_strike_contracts` configurados, devuelve el base de siempre: la regla
    nace apagada y no cambia el comportamiento de nadie que no la configure."""
    base = max(1, int(getattr(lt, "base_contracts_per_order", 1) or 1))
    umbral = float(getattr(lt, "cheap_strike_max", 0.0) or 0.0)
    minimo = int(getattr(lt, "cheap_strike_contracts", 0) or 0)
    if umbral > 0 and minimo > 0 and strike is not None and float(strike) < umbral:
        return max(base, minimo)
    return base


def contracts_for_collateral(margen_por_contrato: float | None, strike: float | None, lt) -> int:
    """Cuántos contratos PEDIR según la PLATA que traba la posición, no según el número del strike.

    Por qué cambió (usuario 2026-09-08). La regla vieja miraba el strike: menos de $30 → 4 contratos,
    todo lo demás → 1. Ese día WFC strike $80 abrió UN contrato trabando $551, mientras AAPL $250
    también abría uno pero trabando $1.415. La misma decisión del cerebro, apostada con montos 2,6
    veces distintos — y NCLH, con $144, diez veces más chica. Usuario: "por el tamaño de mi cuenta
    debería abrir mínimo 2 de ese monto de tiquet".

    No es solo una cuestión de tamaño: el aprendizaje compara ganadoras contra perdedoras, y estaba
    comparando operaciones de $130 con operaciones de $1.415 como si pesaran lo mismo.

    Ahora: contratos = objetivo ÷ lo que traba un contrato, redondeado al entero más cercano. Con el
    objetivo en $1.100 y los números reales de esa semana:
        WFC   $551/contrato → 2 ($1.102)      DIS   $809/contrato → 1
        UAL   $622/contrato → 2 ($1.244)      COIN  $1.023      → 1
        AAPL  $1.415        → 1               NU    $105        → 10, recortado a 4 por el techo

    El piso de `cheap_strike_*` se conserva por si el margen no se puede calcular. Y esto es lo que
    se PIDE: el guardián después solo puede RECORTAR (colateral total, notional, cash, techo por
    orden), nunca subir. "El guardián manda" (usuario 2026-08-17).

    Con `target_collateral_per_position` en 0 se cae a la regla vieja por strike, intacta."""
    objetivo = float(getattr(lt, "target_collateral_per_position", 0.0) or 0.0)
    por_strike = contracts_for_strike(strike, lt)
    if objetivo <= 0 or not margen_por_contrato or margen_por_contrato <= 0:
        return por_strike
    techo = max(1, int(getattr(lt, "max_contracts_per_order", 1) or 1))
    # Redondeo al entero más cercano, explícito: round() de Python redondea el .5 al par y acá eso
    # sería una sorpresa silenciosa sobre el tamaño de una posición real.
    cuantos = max(1, int(objetivo / float(margen_por_contrato) + 0.5))
    cuantos = max(cuantos, por_strike if por_strike > 1 else 1)
    if cuantos > techo:
        # El techo se aplica ACÁ además de en el guardián: pedir 55 contratos de un strike de $5 y
        # confiar en que alguien más lo frene es apoyar toda la seguridad en un solo punto. Pero
        # queda dicho en el log, porque "el objetivo pedía más que el techo" es justamente el dato
        # que hace falta para saber si el techo quedó chico.
        logger.info("Live: el objetivo de $%.0f por posición pedía %d contratos (traba $%.0f cada "
                    "uno); el techo por orden los deja en %d", objetivo, cuantos,
                    float(margen_por_contrato), techo)
    return max(1, min(techo, cuantos))


def contracts_after_diversification(base: int, entradas_abiertas: int) -> int:
    """Cuantos contratos pedir segun cuantas entradas vivas ya hay de ESE simbolo (usuario 2026-08-21:
    "si el lunes vendio -4 put de AAL, que el miercoles no agregue 4 mas, sino 2 o 1 o ninguna").

    La escalera va a la mitad cada vez: 1ra entrada el tamano completo, 2da la mitad, 3ra uno solo, 4ta
    ninguna. Con AAL (4 contratos por la regla de strike barato) queda 4 -> 2 -> 1 -> nada: 7 contratos
    en total en vez de los 9 que se acumularon.

    IMPORTANTE: NO reemplaza la regla de tamano por precio de la empresa (usuario 2026-08-07/08-17,
    `use_price_tier_sizing` en el simulador y `cheap_strike_*` en el real). Esa sigue decidiendo el
    tamano BASE de la primera entrada; esta escalera solo lo achica en las repeticiones. Una empresa
    barata sigue entrando con 4 la primera vez."""
    base = max(0, int(base))
    if entradas_abiertas <= 0:
        return base
    if entradas_abiertas == 1:
        return max(1, base // 2)
    if entradas_abiertas == 2:
        return 1
    return 0


@_serialized
def maybe_log_live_order(conn, symbol, result, snapshot, settings, as_of: date, day_change_pct=None,
                         broker=None) -> None:
    """Hook llamado por el simulador cuando una entrada PASA. Si el símbolo está en la whitelist real y
    el día está armado, arma el plan de orden real y lo registra. En dry-run se queda ahí (no envía). En
    REAL (enabled + sin dry-run) y con `broker`, MANDA la orden y camina el precio. Nunca rompe el caller.

    Va bajo `@_serialized` desde la auditoria del 2026-08-22: era la UNICA funcion que mandaba ordenes
    reales sin tomar el lock, mientras sus hermanas (send_pending_open_emails, reprice_resting_orders,
    close_real_positions, process_approved_ai_orders) si lo tomaban.

    El agujero concreto: el anti-duplicado `has_live_committed_order_for_symbol_today` exige `sent = 1`,
    y esa marca se escribe recien DESPUES del price walk, que dura hasta 3 minutos. Si el chat aprobaba
    vender NU a las 10:00 y entraba al walk, el escaneo del robot podia llegar a NU a las 10:01, no ver
    la fila del chat (todavia con sent=0), y mandar una SEGUNDA orden real del mismo simbolo. Con el
    lock, la segunda espera y ve sent=1."""
    lt = settings.live_trading
    try:
        if symbol not in (lt.allowed_symbols or []):
            return
        if not repo.is_live_armed(conn, as_of):
            return   # sin START del día, ni en dry-run ensayamos
        if repo.has_live_committed_order_for_symbol_today(conn, symbol, as_of):
            return   # ya hay una posición/orden real viva de este símbolo hoy — buscamos otra distinta
        # TOPE DE POSICIONES VIVAS POR SÍMBOLO (usuario 2026-09-07). El chequeo de arriba solo mira
        # HOY, así que no impedía acumular el mismo subyacente día tras día: el 17 y el 18 de agosto
        # entraron dos AAL 13P del mismo vencimiento y quedaron las dos abiertas, doblando la
        # exposición a la misma acción sin que nadie lo decidiera. El usuario las vio juntas en rojo
        # en el dashboard tres semanas después.
        #
        # NO se aplica a las órdenes pedidas por chat: esas las pide el usuario a mano y saltean los
        # topes del robot automático a propósito (2026-08-11). 0 = tope apagado.
        _tope_sym = int(getattr(lt, "max_open_real_per_symbol", 0) or 0)
        if _tope_sym > 0:
            _vivas = repo.count_open_real_puts_for_symbol(conn, symbol)
            if _vivas >= _tope_sym:
                logger.info("Live: %s ya tiene %d posición(es) real(es) abierta(s) (tope %d por "
                            "símbolo) — se busca otra acción", symbol, _vivas, _tope_sym)
                return
        contract = getattr(result, "contract", None)
        if contract is None:
            return

        margin = rules.per_contract_cost(snapshot.price, contract.strike, result.premium, settings.simulator)

        # --- Escalera de DIVERSIFICACION (usuario 2026-08-21) ---
        # El tamano base lo sigue fijando la regla por precio/strike de siempre; aca solo lo achicamos
        # si ya hay posiciones vivas del mismo simbolo, para no apilar 9 contratos de AAL.
        _base_ctr = contracts_for_collateral(margin, contract.strike, lt)
        _abiertas_sym = repo.count_open_real_entries_for_symbol(conn, symbol)
        _ctr_pedidos = contracts_after_diversification(_base_ctr, _abiertas_sym)
        if _ctr_pedidos <= 0:
            # Queda registrado como orden FRENADA para que se vea el motivo en el dashboard, igual que
            # cuando frena por el tope diario. No se manda nada al broker.
            repo.insert_live_order_log(
                conn, log_date=as_of, log_ts=datetime.now(), symbol=symbol,
                action=live_guard.ACTION_OPEN, strike=contract.strike,
                expiration=contract.expiration.isoformat(), approved=False, final_contracts=0,
                start_limit_price=None, collateral=0.0,
                dry_run=not (lt.enabled and not lt.dry_run), sent=False,
                reasons=(f"Diversificacion: ya tenes {_abiertas_sym} entradas abiertas de {symbol}; "
                         f"no se agrega otra hasta cerrar alguna."),
                payload_json=None, ladder_json=None, bid=contract.bid, ask=contract.ask,
            )
            logger.info("Live: %s frenada por diversificacion (%d entradas abiertas)", symbol, _abiertas_sym)
            return
        if _ctr_pedidos < _base_ctr:
            logger.info("Live: %s achicada por diversificacion: %d -> %d contratos (%d entradas abiertas)",
                        symbol, _base_ctr, _ctr_pedidos, _abiertas_sym)

        opp = Opportunity(
            symbol=symbol, action=live_guard.ACTION_OPEN, expiration=contract.expiration,
            strike=contract.strike, requested_contracts=_ctr_pedidos,
            bid=contract.bid, ask=contract.ask, underlying_price=snapshot.price,
            collateral_per_contract=margin,
        )
        day = DayState(
            orders_today=repo.count_live_approved_opens_today(conn, as_of),
            orders_this_week=repo.count_live_approved_opens_this_week(conn, as_of),
            deployed_today=repo.sum_live_collateral_today(conn, as_of),
        )
        limits = _limits_with_runtime_flags(settings, conn, as_of)
        walk = WalkConfig(
            narrow_step=lt.price_walk_step, wide_step=getattr(lt, "price_walk_wide_step", 0.05),
            wide_threshold=getattr(lt, "price_walk_wide_threshold", 0.50),
            interval_seconds=lt.price_walk_interval_seconds, stop_at_mid=lt.price_walk_stop_at_mid,
        )
        account = AccountSnapshot(cash=_DRY_RUN_CASH, equity=_DRY_RUN_CASH)

        plan = plan_live_order(opp, account, limits, day, armed=True, walk=walk)

        collateral = (plan.final_contracts or 0) * margin
        log_id = repo.insert_live_order_log(
            conn, log_date=as_of, log_ts=datetime.now(), symbol=symbol,
            action=live_guard.ACTION_OPEN, strike=contract.strike,
            expiration=contract.expiration.isoformat(), approved=plan.approved,
            final_contracts=plan.final_contracts or 0, start_limit_price=plan.start_limit_price,
            collateral=collateral, dry_run=plan.is_dry_run, sent=False,
            reasons="; ".join(plan.reasons) if plan.reasons else None,
            payload_json=json.dumps(plan.order_payload) if plan.order_payload else None,
            ladder_json=json.dumps(plan.price_ladder) if plan.price_ladder else None,
            bid=opp.bid, ask=opp.ask,
            # Contexto EXACTO de la decisión al abrir ESTA orden (delta, POP, IV, % del día, cobertura…),
            # para que la placa de votación puntúe con el dato real de la orden (usuario 2026-08-10).
            open_context_json=(json.dumps(getattr(result, "context", None)) if getattr(result, "context", None) else None),
        )

        # ENVÍO REAL (usuario 2026-08-10): SOLO si el plan pasó, NO es dry-run, el maestro está encendido,
        # sin kill switch, y tenemos un broker con las primitivas de orden. Todo lo demás ya lo validó el
        # guardián. La fila del log YA cuenta para el tope de 1/día desde que se insertó aprobada, así que
        # un fallo de fill no habilita un reintento el mismo día (seguridad > oportunidad).
        if (plan.approved and not plan.is_dry_run and limits.enabled and not limits.kill_switch
                and broker is not None and hasattr(broker, "place_order")):
            _send_real_order(conn, log_id, broker, opp, plan, walk, lt, open_context=getattr(result, "context", None))
        elif plan.approved and not plan.is_dry_run and limits.enabled:
            logger.warning("Live: modo REAL pero falta el broker con envío — no se mandó (%s).", symbol)

        logger.info("Live: %s %s → %s%s", symbol, "aprobada" if plan.approved else "rechazada",
                    "REAL " if (plan.approved and not plan.is_dry_run) else "dry-run ", plan.description)
    except Exception:
        logger.debug("Live: fallo al registrar el plan dry-run de %s (no afecta al simulador)", symbol, exc_info=True)


def _send_real_order(conn, log_id, broker, opp, plan, walk, lt, open_context=None) -> None:
    """Manda la orden REAL a Schwab y camina el precio (real_sender), después escribe el resultado en la
    fila del log. Aislado: cualquier fallo queda registrado como send_error, nunca tumba el escaneo."""
    from options_advisor.execution import real_sender

    account_hash = None
    try:
        account_hash = broker.resolve_account_hash(getattr(lt, "account_number", "") or None)
    except Exception:
        logger.exception("Live: fallo al resolver la cuenta para la orden real")
    if not account_hash:
        repo.mark_live_order_sent(
            conn, log_id, schwab_order_id=None, order_status="error", fill_price=None,
            filled_contracts=0, final_limit_price=plan.start_limit_price, replacements=0,
            sent_ts=datetime.now(), send_error="sin cuenta Schwab resoluble (revisá account_number/login)",
        )
        return

    logger.warning("Live: ENVIANDO orden REAL de %s (%s contrato/s, arranca en $%.2f)…",
                   opp.symbol, plan.final_contracts, plan.start_limit_price or 0.0)
    res = real_sender.execute_live_walk(
        broker, account_hash, opp, plan.final_contracts, plan.price_ladder,
        interval_seconds=walk.interval_seconds,
    )
    repo.mark_live_order_sent(
        conn, log_id, schwab_order_id=res.order_id, order_status=(res.status or ("error" if res.error else "")),
        fill_price=res.fill_price, filled_contracts=res.filled_contracts,
        final_limit_price=res.final_limit_price, replacements=res.replacements,
        sent_ts=datetime.now(), send_error=res.error,
    )
    if res.filled:
        # El email de APERTURA lo manda send_pending_open_emails (idempotente, en el escaneo) — así nunca
        # se pierde ni se duplica, haya llenado al toque o negociando (usuario 2026-08-11).
        logger.warning("Live: orden REAL de %s LLENÓ %s contrato/s a $%.2f (id %s).",
                       opp.symbol, res.filled_contracts, res.fill_price or 0.0, res.order_id)
    elif (res.status or "").upper() in _RESTING_OPEN_STATES and res.order_id and not res.error:
        # La orden se ENVIÓ y quedó negociando (Working): avisamos por email aunque todavía no haya
        # llenado (usuario 2026-08-11). El email del fill llega después, cuando llene (vía reprice).
        logger.warning("Live: orden REAL de %s puesta y NEGOCIANDO (estado %s, id %s).",
                       opp.symbol, res.status, res.order_id)
        try:
            from options_advisor.alerts import notifier
            _subj, _body = _fmt_working_email(opp.symbol, opp.strike, opp.expiration, plan.final_contracts,
                                              res.final_limit_price or plan.start_limit_price, open_context)
            notifier.send_email_robot_real(_subj, _body)
        except Exception:
            logger.debug("Live: no se pudo mandar el email de 'Working'", exc_info=True)
    else:
        logger.warning("Live: orden REAL de %s NO llenó (estado %s). %s",
                       opp.symbol, res.status, res.error or "cancelada al cerrar la ventana")


@_serialized
def send_pending_open_emails(conn, settings) -> None:
    """Manda el email de APERTURA de cada orden real que LLENÓ y todavía no lo tiene mandado (usuario
    2026-08-11: 'no me llegó el email de NU'). Idempotente y desacoplado del momento exacto del fill:
    garantiza EXACTAMENTE 1 email por apertura, haya llenado al instante o negociando. Vale para las
    automáticas Y las del chat. Corre en cada escaneo. Nunca rompe: aislado por fila."""
    lt = settings.live_trading
    if not lt.enabled or lt.dry_run:
        return
    try:
        pendientes = repo.get_open_fills_needing_email(conn)
    except Exception:
        logger.debug("Live-email: fallo al leer aperturas sin email", exc_info=True)
        return
    if not pendientes:
        return
    from options_advisor.alerts import notifier
    for r in pendientes:
        try:
            _octx = None
            if "open_context_json" in r.keys() and r["open_context_json"]:
                try:
                    _octx = json.loads(r["open_context_json"])
                except Exception:
                    _octx = None
            _contracts = r["filled_contracts"] or r["final_contracts"] or 1
            _abierta = r["log_ts"] if "log_ts" in r.keys() else None
            _subj, _body = _fmt_open_email(r["symbol"], r["strike"], r["expiration"], _contracts,
                                           r["fill_price"] or 0.0, _octx, opened_at=_abierta)
            if notifier.send_email_robot_real(_subj, _body):
                repo.mark_open_email_sent(conn, r["id"])   # solo marcamos si SE MANDÓ (si SMTP falla, reintenta)
        except Exception:
            logger.debug("Live-email: fallo al mandar el email de apertura de %s", r["symbol"], exc_info=True)


def _walk_config(lt) -> WalkConfig:
    return WalkConfig(
        narrow_step=lt.price_walk_step, wide_step=getattr(lt, "price_walk_wide_step", 0.05),
        wide_threshold=getattr(lt, "price_walk_wide_threshold", 0.50),
        interval_seconds=lt.price_walk_interval_seconds, stop_at_mid=lt.price_walk_stop_at_mid,
    )


@_serialized
def reprice_resting_orders(conn, broker, settings, as_of: date) -> None:
    """Sigue NEGOCIANDO las órdenes de apertura reales que quedaron puestas al mid y no llenaron: cada
    escaneo verifica si ya llenaron (marca el fill + email) y, si siguen vivas, las RE-PRECIA al mid ACTUAL
    (el mercado se movió) sin cruzarlo (usuario 2026-08-10: 'está a un centavo del mark y no negocia'). Corre
    solo con real encendido y mercado abierto. Aislado por orden."""
    lt = settings.live_trading
    if not lt.enabled or lt.dry_run or repo.is_live_kill_switch(conn):
        return
    if broker is None or not hasattr(broker, "place_order"):
        return
    from options_advisor.scheduler.market_calendar import market_session
    if market_session() != "abierto":
        return
    resting = repo.get_resting_real_open_orders(conn)
    if not resting:
        return
    account_hash = None
    try:
        account_hash = broker.resolve_account_hash(getattr(lt, "account_number", "") or None)
    except Exception:
        logger.exception("Live-reprice: no se pudo resolver la cuenta")
    if not account_hash:
        return
    for r in resting:
        try:
            _reprice_one_resting(conn, broker, account_hash, r, as_of)
        except Exception:
            logger.exception("Live-reprice: fallo con la orden en espera de %s (se continúa)", r["symbol"])


def _reprice_one_resting(conn, broker, account_hash, r, as_of: date) -> None:
    from datetime import date as _date

    from options_advisor.execution import price_walker as pw
    from options_advisor.execution import real_sender
    from options_advisor.execution import schwab_orders as so

    oid = r["schwab_order_id"]
    contracts = r["filled_contracts"] or r["final_contracts"] or 1

    # 1) ¿Ya llenó (o murió) desde el último escaneo?
    try:
        info = broker.get_order(account_hash, oid)
    except Exception:
        logger.debug("Live-reprice: no se pudo leer el estado de %s", r["symbol"], exc_info=True)
        return
    status = (info.get("status") or "").upper()
    if status == "FILLED":
        fp = real_sender.extract_fill_price(info)
        repo.mark_live_order_sent(
            conn, r["id"], schwab_order_id=oid, order_status="FILLED", fill_price=fp,
            filled_contracts=int(info.get("filledQuantity") or contracts), final_limit_price=r["final_limit_price"],
            replacements=r["replacements"] or 0, sent_ts=datetime.now())
        # El email de apertura lo manda send_pending_open_emails (idempotente) en este mismo escaneo.
        logger.warning("Live-reprice: la orden en espera de %s LLENÓ a $%.2f", r["symbol"], fp or 0.0)
        return
    if status in ("REJECTED", "CANCELED", "EXPIRED"):
        # Una orden que murio pudo haber llenado PARTE (auditoria 2026-08-22). Registrar 0 dejaba
        # esos contratos REALES invisibles: `get_open_real_put_positions` exige order_status='FILLED',
        # asi que una orden EXPIRED con 2 de 4 llenados dejaba 2 puts cortos vivos sin objetivo de
        # ganancia, sin stop y sin contar para ningun tope. El dato estaba disponible ahi mismo, en
        # `info`, y se descartaba.
        try:
            _parcial = int(info.get("filledQuantity") or 0)
        except (TypeError, ValueError):
            _parcial = 0
        if _parcial > 0:
            _fp = real_sender.extract_fill_price(info)
            repo.mark_live_order_sent(
                conn, r["id"], schwab_order_id=oid, order_status="FILLED", fill_price=_fp,
                filled_contracts=_parcial, final_limit_price=r["final_limit_price"],
                replacements=r["replacements"] or 0, sent_ts=datetime.now(),
                send_error=f"orden {status} con llenado PARCIAL de {_parcial} contrato(s)")
            logger.warning("Live-reprice: %s quedo %s pero llenó %d contrato(s) — se registran como "
                           "posicion abierta para que el robot los gestione", r["symbol"], status, _parcial)
            return
        repo.mark_live_order_sent(
            conn, r["id"], schwab_order_id=oid, order_status=status, fill_price=None, filled_contracts=0,
            final_limit_price=r["final_limit_price"], replacements=r["replacements"] or 0,
            sent_ts=datetime.now(), send_error=f"orden {status} mientras esperaba")
        return

    # 2) Sigue viva: re-preciar al MID actual (si cambió ≥ 1 centavo).
    exp = _date.fromisoformat(r["expiration"])
    try:
        chain = broker.get_option_chain(r["symbol"], expiration_range_days=(0, max((exp - as_of).days + 5, 7)))
    except Exception:
        return
    ct = None
    for c in chain.contracts:
        if c.option_type == "put" and abs(c.strike - r["strike"]) < 0.01 and c.expiration == exp:
            ct = c
            break
    if ct is None or ct.bid <= 0 or ct.ask <= 0 or ct.ask < ct.bid:
        return
    # Piso DURO de precio del usuario (usuario 2026-08-11: la orden llenó a 2.94 pidiendo mínimo 3.00 porque
    # el re-precio seguía al mid HACIA ABAJO sin piso). Ahora el re-precio nunca baja de `max(mid, piso)`:
    # si el mid queda por debajo del piso, la orden se queda en el piso y NO llena por debajo de lo pedido.
    _floor = None
    try:
        if "price_floor" in r.keys() and r["price_floor"] is not None:
            _floor = float(r["price_floor"])
    except (ValueError, TypeError):
        _floor = None
    new_price = pw.sell_stop_price(ct.bid, ct.ask, stop_at_mid=True, price_floor=_floor)
    cur = r["final_limit_price"]
    if cur is not None and round(new_price, 2) == round(cur, 2):
        return  # ya está EXACTAMENTE en el precio objetivo (en centavos), nada que cambiar
    # Solo el REMANENTE: `replace_order` cancela lo que queda y crea una orden nueva por la cantidad
    # indicada, asi que reenviar `contracts` sobre una orden con llenado parcial compra de mas.
    # `contracts` sale de `filled_contracts or final_contracts`, y filled_contracts vale 0 mientras
    # la orden espera, asi que antes siempre se reenviaba la cantidad completa.
    try:
        _ya = int(info.get("filledQuantity") or 0)
    except (TypeError, ValueError):
        _ya = 0
    _falta = max(0, contracts - _ya)
    if _falta <= 0:
        return   # no queda nada por pedir; el proximo sondeo vera el estado final
    payload = so.build_sell_put_to_open(r["symbol"], exp, r["strike"], _falta, new_price)
    try:
        new_oid = broker.replace_order(account_hash, oid, payload)
    except Exception:
        logger.exception("Live-reprice: no se pudo re-preciar %s", r["symbol"])
        return
    repo.update_resting_order_reprice(conn, r["id"], new_schwab_order_id=new_oid, new_limit_price=new_price,
                                      bid=ct.bid, ask=ct.ask)
    logger.info("Live-reprice: %s put %.2f re-preciada de $%.2f a $%.2f%s",
                r["symbol"], r["strike"], cur or 0.0, new_price,
                f" (piso ${_floor:.2f})" if _floor else " (mid actual)")


@_serialized
def close_real_positions(conn, broker, settings, as_of: date) -> None:
    """Cierra las posiciones REALES abiertas por el robot cuando se cumplen las MISMAS reglas del simulador
    (profit target escalonado / stop-loss / DTE mínimo / noticia), recomprando para cerrar y caminando el
    precio hacia el mid (usuario 2026-08-10). Corre cuando el real está ENCENDIDO (enabled, sin dry-run, sin
    kill switch); gestionar lo abierto NO requiere el START del día. Reconciliado contra Schwab: si una
    posición ya no está en la cuenta (cerrada/asignada afuera), la marca cerrada y no intenta recomprarla.
    Aislado por posición: un fallo nunca tumba el resto del escaneo."""
    lt = settings.live_trading
    if not lt.enabled or lt.dry_run or repo.is_live_kill_switch(conn):
        return
    if broker is None or not hasattr(broker, "place_order"):
        return
    from options_advisor.scheduler.market_calendar import market_session
    if market_session() != "abierto":
        return  # solo cerramos con el mercado abierto (precios frescos, ejecución posible)
    positions = repo.get_open_real_put_positions(conn)
    if not positions:
        return

    account_hash = None
    try:
        account_hash = broker.resolve_account_hash(getattr(lt, "account_number", "") or None)
    except Exception:
        logger.exception("Live-close: no se pudo resolver la cuenta")
    if not account_hash:
        return

    # Reconciliación: qué puts cortos siguen REALMENTE abiertos en la cuenta (evita recomprar algo ya
    # cerrado/asignado, y mantiene el registro en sincronía con Schwab).
    open_at_broker = _puts_cortos_en_el_broker(broker)

    walk = _walk_config(lt)
    for pos in positions:
        try:
            _maybe_close_one_real(conn, broker, account_hash, pos, settings.simulator, as_of, walk, open_at_broker)
        except Exception:
            logger.exception("Live-close: fallo evaluando el cierre de %s (se continúa)", pos["symbol"])


def _puts_cortos_en_el_broker(broker):
    """Set de (simbolo, strike, vencimiento) de los puts CORTOS vivos en la cuenta, o None si no se
    pudo leer con confianza. `None` significa "no se" y APAGA la reconciliacion.

    Por que existe (usuario 2026-08-21: "esas no las cerre yo"). Ese dia la API de Schwab fallo:

        12:26:53 ERROR Fallo al leer posiciones de la cuenta 74257810
        12:26:56 ERROR Fallo al listar cuentas de Schwab; sin posiciones reales esta corrida

    `get_all_positions()` es TOLERANTE: ante un fallo loguea y devuelve lo que pudo, que ese dia fue
    una lista vacia. El codigo viejo la trataba como verdad y concluyo que ninguna de las 5
    posiciones seguia en el broker. Las marco `closed_in_broker` a las 12:28 con AAL, NVDA y AMZN
    todavia abiertas. El robot dejo de vigilarlas: sin cierre por ganancia y sin contar para los topes.

    La solucion NO es desconfiar de toda lista vacia --- eso dejaria la reconciliacion muerta para
    siempre el dia que cierres todo a mano, y el robot quedaria intentando recomprar posiciones
    fantasma. La solucion es preguntar bien: `get_all_positions_strict()` LANZA si alguna cuenta no
    respondio, asi que su lista vacia si es de fiar. Solo cuando el broker no ofrece esa garantia
    (mocks y tests) caemos a la version tolerante."""
    estricto = getattr(broker, "get_all_positions_strict", None)
    try:
        posiciones = list(estricto() if callable(estricto) else broker.get_all_positions())
    except Exception as exc:
        logger.warning("Reconciliacion: lectura de posiciones incompleta (%s); NO se cierra nada", exc)
        return None
    return {
        (p.underlying_symbol, round(float(p.strike), 2), p.expiration)
        for p in posiciones
        if getattr(p, "option_type", None) == "put" and (p.quantity or 0) < 0 and p.strike and p.expiration
    }


# Piso de precio para recomprar un put que ya no tiene bid (vale casi nada). Sin esto el robot no
# podía cerrar justo las posiciones más ganadoras (usuario 2026-08-14).
_MIN_TICK = 0.01
# A partir de este número de intentos fallidos seguidos sobre la MISMA posición, avisa por mail.
_CLOSE_FAIL_ALERT_AFTER = 3


def _market_value_now(broker, symbol, strike, expiration) -> float | None:
    """Valor de mercado del put AHORA (mid de la cadena, o intrínseco si no aparece). Se usa para
    ESTIMAR el precio de salida de una posición que se cerró por fuera del robot y cuyo fill exacto no
    aparece en Schwab — así su ganancia entra igual en los totales, marcada como estimación, en vez de
    perderse como un P&L NULL (usuario 2026-08-14: "que todas las ganancias se ingresen")."""
    try:
        from datetime import date as _d
        dte = max(0, (expiration - _d.today()).days)
        chain = broker.get_option_chain(symbol, expiration_range_days=(0, max(dte + 5, 7)))
        for c in chain.contracts:
            if c.option_type == "put" and abs(c.strike - strike) < 0.01 and c.expiration == expiration:
                if c.mid_price and c.mid_price > 0:
                    return round(float(c.mid_price), 2)
        spot = getattr(chain, "underlying_price", None)
        if spot:
            return round(max(float(strike) - float(spot), 0.0), 2)
    except Exception:
        logger.debug("Live-close: no se pudo estimar el valor de %s put %s", symbol, strike, exc_info=True)
    return None


def _find_close_fill_price(broker, symbol, strike, expiration) -> float | None:
    """Busca el precio REAL al que se cerró (recompró) este put en las órdenes llenadas recientes de
    Schwab, para poder calcular el P&L realizado cuando la posición se cerró por fuera del robot (usuario
    2026-08-11: 'no aparece la ganancia'). None si no lo encuentra (asignación, o cierre viejo)."""
    try:
        from datetime import timedelta, timezone
        from options_advisor.broker.models import parse_occ_option_symbol
        # 4 días era corto: una posición abierta el viernes y cerrada a mano el lunes siguiente ya
        # quedaba fuera de la ventana y su P&L se perdía. 10 días cubre un fin de semana largo.
        orders = broker.get_recent_filled_orders(datetime.now(timezone.utc) - timedelta(days=10))
    except Exception:
        logger.debug("Live-close: no se pudieron leer las órdenes llenadas recientes", exc_info=True)
        return None
    best = None
    try:
        for o in orders:
            for leg in getattr(o, "legs", []) or []:
                parsed = parse_occ_option_symbol(leg.occ_symbol)
                if parsed is None:
                    continue
                root, exp, otype, k = parsed
                if (root.strip().upper() == str(symbol).strip().upper() and otype == "put"
                        and exp == expiration and abs(float(k) - float(strike)) < 0.01
                        and str(leg.instruction).upper() in ("BUY_TO_CLOSE", "BUY")):
                    best = float(leg.price)   # el fill de la recompra que cerró el put corto
    except Exception:
        return None
    return best


def _maybe_close_one_real(conn, broker, account_hash, pos, sim, as_of: date, walk, open_at_broker,
                          force_reason: str | None = None) -> None:
    """Evalúa (o FUERZA) el cierre de UNA posición real. Si `force_reason` viene seteado (cierre manual
    pedido por el asesor, usuario 2026-08-11), se salta SOLO la regla del simulador — igual respeta la
    reconciliación con Schwab, el vencimiento y los huecos de datos, y recompra por el mismo camino."""
    from datetime import date as _date

    from options_advisor.execution import price_walker as pw
    from options_advisor.execution import real_sender
    from options_advisor.simulator import positions as sim_positions

    symbol = pos["symbol"]
    strike = pos["strike"]
    entry_premium = pos["fill_price"]
    contracts = pos["filled_contracts"] or pos["final_contracts"] or 1
    if entry_premium is None or strike is None or not pos["expiration"]:
        return
    expiration = _date.fromisoformat(pos["expiration"])
    dte = (expiration - as_of).days

    # Ya no está en la cuenta (cerrada/asignada afuera): reconciliar sin recomprar. Antes se guardaba el
    # P&L en None y el reporte mostraba $0.00 (usuario 2026-08-11: "no aparece la ganancia"). Ahora
    # buscamos el precio REAL de cierre en las órdenes llenadas de Schwab y calculamos el P&L.
    if open_at_broker is not None and (symbol, round(float(strike), 2), expiration) not in open_at_broker:
        _close_px = _find_close_fill_price(broker, symbol, strike, expiration)
        _estimado = False
        if _close_px is None:
            # No apareció el fill exacto en Schwab (cierre a mano viejo, asignación, o la orden no quedó
            # en el rango consultado). Antes se guardaba P&L NULL y esa ganancia DESAPARECÍA de todos los
            # totales — pasó con NU y NVDA (usuario 2026-08-14: "que todas las ganancias se ingresen").
            # Ahora se estima con el valor de mercado del put en el momento de detectar el cierre, que es
            # lo más cercano al precio de salida real, y queda MARCADO como estimación.
            _close_px = _market_value_now(broker, symbol, strike, expiration)
            _estimado = _close_px is not None
        _realized = (round((entry_premium - _close_px) * 100.0 * contracts, 2)
                     if _close_px is not None else None)
        repo.mark_real_position_closed(conn, pos["id"], close_ts=datetime.now(), close_fill_price=_close_px,
                                       close_reason="closed_in_broker", realized_pnl=_realized,
                                       close_schwab_order_id=None, pnl_is_estimate=_estimado)
        logger.warning("Live-close: %s put %.2f ya no está en Schwab — cerrada (reconciliación, P&L %s%s)",
                       symbol, strike, f"${_realized:+.2f}" if _realized is not None else "s/d",
                       " ESTIMADO" if _estimado else "")
        return

    # Vencida: no se recompra; marcar cerrada (el P&L exacto se reconcilia en Schwab).
    if dte < 0:
        repo.mark_real_position_closed(conn, pos["id"], close_ts=datetime.now(), close_fill_price=None,
                                       close_reason="expired", realized_pnl=None, close_schwab_order_id=None)
        return

    try:
        chain = broker.get_option_chain(symbol, expiration_range_days=(0, max(dte + 5, 7)))
        quote = broker.get_quote(symbol)
    except Exception as exc:
        # Antes esto era logger.debug y el robot NO registra DEBUG: se abandonaba el cierre sin dejar
        # rastro (usuario 2026-08-14). Ahora queda visible y cuenta como intento.
        logger.warning("Live-close: %s — sin cadena/quote (%s); NO se pudo evaluar el cierre, se reintenta",
                       symbol, exc)
        repo.bump_real_close_attempt(conn, pos["id"], f"sin cadena/quote: {exc}")
        return

    ct = None
    for c in chain.contracts:
        if c.option_type == "put" and abs(c.strike - strike) < 0.01 and c.expiration == expiration:
            ct = c
            break
    if ct is None:
        # Hueco de datos: no cerramos a ciegas, pero AVISAMOS. Esta rama no logueaba absolutamente nada.
        logger.warning("Live-close: %s put %.2f vto %s no aparece en la cadena; no se cierra a ciegas",
                       symbol, strike, expiration)
        repo.bump_real_close_attempt(conn, pos["id"], "el contrato no aparece en la cadena")
        return

    current_value = ct.mid_price if ct.mid_price and ct.mid_price > 0 else max(strike - quote.last_price, 0.0)
    try:
        age_days = max(0, (as_of - _date.fromisoformat(pos["log_date"])).days)
    except Exception:
        age_days = 0

    if force_reason is not None:
        do_close, reason = True, force_reason   # cierre manual: no pasa por la regla, pero sí por todo lo demás
    else:
        do_close, reason = sim_positions.evaluate_put_close(
            entry_premium, current_value, strike, quote.last_price, dte, age_days, sim)
        if not do_close:
            return

    # Bid en CERO no puede frenar el cierre (usuario 2026-08-14). Un put que ya casi no vale suele
    # quedar con bid 0.00 — que es exactamente cuando MÁS conviene recomprarlo: pagás centavos y te
    # quedás con casi toda la prima. Antes esta rama abandonaba el cierre en silencio (logger.debug, y
    # el robot no registra DEBUG), y es la explicación más probable de por qué NU se quedó abierta al
    # 75% de ganancia sin que el robot volviera a intentar.
    bid, ask = ct.bid, ct.ask
    if ask <= 0 or ask < max(bid, 0.0):
        logger.warning("Live-close: %s put %.2f sin ask válido (bid %.2f / ask %.2f); no se puede recomprar",
                       symbol, strike, bid, ask)
        repo.bump_real_close_attempt(conn, pos["id"], f"sin ask válido (bid {bid} / ask {ask})")
        return
    if bid <= 0:
        bid = _MIN_TICK   # piso: recomprar al tick mínimo en vez de no cerrar nunca
        logger.info("Live-close: %s put %.2f tiene bid 0.00 — se usa el piso de $%.2f para poder salir",
                    symbol, strike, _MIN_TICK)

    step = walk.step_for(bid, ask)
    ladder = pw.build_price_ladder(pw.SIDE_BUY, bid, ask, step=step, stop_at_mid=walk.stop_at_mid)
    opp = Opportunity(symbol=symbol, action=live_guard.ACTION_CLOSE, expiration=expiration, strike=strike,
                      requested_contracts=contracts, bid=bid, ask=ask, underlying_price=quote.last_price)
    logger.warning("Live-close: CERRANDO %s put %.2f x%d — motivo %s (recompra arranca %.2f → mid)",
                   symbol, strike, contracts, reason, ladder[0] if ladder else 0.0)

    # Para el cierre NO dejamos la orden colgada: si no llena en la ventana, se cancela y se reintenta en el
    # próximo escaneo (así nunca queda una recompra vieja a un precio pasado). El BUY_TO_CLOSE, además, Schwab
    # solo lo acepta si la posición existe (protección extra contra recomprar algo ya cerrado).
    res = real_sender.execute_live_walk(broker, account_hash, opp, contracts, ladder,
                                        interval_seconds=walk.interval_seconds, leave_resting_at_mid=False)
    if res.filled:
        close_px = res.fill_price if res.fill_price is not None else current_value
        realized = round((entry_premium - close_px) * 100.0 * contracts, 2)
        repo.mark_real_position_closed(conn, pos["id"], close_ts=datetime.now(), close_fill_price=close_px,
                                       close_reason=reason, realized_pnl=realized, close_schwab_order_id=res.order_id)
        logger.warning("Live-close: %s CERRADA a $%.2f — motivo %s — P&L $%.2f", symbol, close_px, reason, realized)
        try:
            from options_advisor.alerts import notifier
            _octx = None
            if "open_context_json" in pos.keys() and pos["open_context_json"]:
                try:
                    _octx = json.loads(pos["open_context_json"])
                except Exception:
                    _octx = None
            _days_held = None
            try:
                _days_held = max(0, (as_of - _date.fromisoformat(pos["log_date"])).days)
            except Exception:
                _days_held = None
            _subj, _body = _fmt_close_email(symbol, strike, contracts, entry_premium, close_px, reason, _octx, _days_held)
            notifier.send_email_robot_real(_subj, _body)
        except Exception:
            logger.debug("Live-close: no se pudo mandar el email de cierre", exc_info=True)
        repo.reset_real_close_attempts(conn, pos["id"])
    else:
        # No llenó. Antes esto era una línea de INFO y nada más: el robot podía fallar el cierre una vez
        # tras otra sin que nadie se enterara (NU al 75%, NVDA al 45%). Ahora se cuenta el intento, se
        # guarda el motivo REAL de Schwab, y a partir del 3er fallo llega un mail.
        _motivo = res.error or (f"estado {res.status}" if res.status else "el broker no devolvió estado")
        _n = repo.bump_real_close_attempt(conn, pos["id"], _motivo)
        logger.warning("Live-close: la recompra de %s put %.2f NO llenó (intento %d) — %s",
                       symbol, strike, _n, _motivo)
        if _n >= _CLOSE_FAIL_ALERT_AFTER and repo.close_fail_email_pending(conn, pos["id"]):
            try:
                from options_advisor.alerts import notifier
                _pnl_now = round((entry_premium - current_value) * 100.0 * contracts, 2)
                notifier.send_email_robot_real(
                    f"⚠️ Lokshn no puede cerrar {symbol} put ${strike:,.2f} ({_n} intentos)",
                    f"El robot quiere cerrar esta posición por {reason} pero la recompra no llena.\n\n"
                    f"Posición: {contracts} put(s) {symbol} ${strike:,.2f} vto {expiration}\n"
                    f"Entrada ${entry_premium:.2f} · valor ahora ${current_value:.2f} · "
                    f"ganancia sin realizar ${_pnl_now:+,.2f}\n"
                    f"Último motivo del broker: {_motivo}\n\n"
                    f"Van {_n} intentos fallidos. Revisalo en Schwab: puede que haya que cerrarla a mano.")
                repo.mark_close_fail_email_sent(conn, pos["id"])
            except Exception:
                logger.debug("Live-close: no se pudo mandar el aviso de cierre fallido", exc_info=True)


def _build_ai_open_context(conn, broker, symbol, contract, underlying, strike, premium, margin, dte, rationale) -> dict:
    """Contexto RICO para una orden abierta por el chat (usuario 2026-08-11: 'órdenes del robot, falta info'):
    delta, POP, IV, cobertura, anualizado, OI, volumen (de la opción) + IV rank, HV, RSI, % del día (del
    último snapshot técnico). Así la placa 'Puntuar esta orden' sale completa, igual que las automáticas."""
    _delta = getattr(getattr(contract, "greeks", None), "delta", None)
    _iv = getattr(contract, "implied_volatility", None)
    _cov = ((underlying - strike) / underlying) if (underlying and underlying > 0) else None
    _ann = ((premium * 100.0 / margin) * (365.0 / dte)) if (margin and margin > 0 and dte and dte > 0) else None
    ctx = {
        "source": "ai_advisor", "rationale": rationale, "underlying_price": underlying,
        "chosen_credit": premium, "chosen_dte": dte, "chosen_margin": margin,
        "chosen_delta": _delta,
        "chosen_pop": round(1 - abs(_delta), 4) if isinstance(_delta, (int, float)) else None,
        "chosen_iv": _iv, "chosen_coverage_pct": _cov, "chosen_annualized_return": _ann,
        "chosen_open_interest": getattr(contract, "open_interest", None),
        "chosen_volume": getattr(contract, "volume", None),
    }
    try:
        snap = repo.get_latest_indicator_snapshot(conn, symbol)
        if snap is not None:
            for k in ("iv_rank", "hv_20d", "rsi_14"):
                try:
                    ctx[k] = snap[k]
                except (KeyError, IndexError):
                    pass
    except Exception:
        pass
    try:
        q = broker.get_quote(symbol)
        ctx["day_change_pct"] = round(float(q.net_change_pct), 2)
    except Exception:
        pass
    return ctx


@_serialized
def process_approved_ai_orders(conn, broker, settings, as_of: date) -> None:
    """Manda las sugerencias del ASESOR AI que el usuario APROBÓ a mano (usuario 2026-08-10), por el
    MISMO guardián real que todo lo demás: START del día, kill switch, cupo diario y colateral. La IA
    solo propuso; nada se abre sin la aprobación del usuario Y sin que el guardián lo apruebe. Aislado:
    cualquier fallo queda anotado en la sugerencia y nunca tumba el escaneo. Corre en cada escaneo.
    """
    lt = settings.live_trading
    try:
        approved = repo.get_approved_ai_suggestions(conn)
    except Exception:
        logger.debug("Asesor: fallo al leer sugerencias aprobadas", exc_info=True)
        return
    for s in approved:
        sug_id = s["id"]
        symbol = (s["symbol"] or "").strip().upper()
        action = (s["action"] if "action" in s.keys() else "open") or "open"
        try:
            # Las sugerencias del SIMULADOR se cierran al instante en la página (paper), no acá.
            if "target" in s.keys() and s["target"] == "simulador":
                continue
            if repo.is_live_kill_switch(conn):
                continue

            # --- CIERRE MANUAL pedido por chat (usuario 2026-08-11): recompra forzada, salta solo la regla ---
            if action == "close":
                _process_approved_close(conn, broker, settings, as_of, s)
                continue

            # --- APERTURA ---
            if lt.allowed_symbols and symbol not in lt.allowed_symbols:
                repo.resolve_ai_suggestion(conn, sug_id, status="rejected",
                                           result_note="símbolo fuera de la lista permitida")
                continue
            if not repo.is_live_armed(conn, as_of):
                continue  # sin START del día: la sugerencia queda esperando aprobada, no se descarta
            # Órdenes del CHAT = pedidas y aprobadas por el usuario a mano → NO se limitan por los topes
            # del robot automático (usuario 2026-08-11: "que el chat haga lo que le pido, que no mire el
            # límite"). No aplicamos el bloqueo de 'ya hay una del símbolo hoy', ni el tope de contratos por
            # orden, ni el cupo diario. Se respeta el kill switch y el buying power real de Schwab. Tope duro
            # de 100 contratos solo como red anti dedo-gordo.
            strike = float(s["strike"])
            exp = date.fromisoformat(s["expiration"])
            contracts_req = max(1, min(int(s["contracts"] or 1), 100))
            # Piso DURO de precio pedido por el usuario ('no bajes de 3.00', usuario 2026-08-11): ni la
            # colocación ni el re-precio bajan de acá. Se persiste en la fila para que el re-precio lo respete.
            _floor = None
            try:
                if "min_price" in s.keys() and s["min_price"] is not None and float(s["min_price"]) > 0:
                    _floor = round(float(s["min_price"]), 2)
            except (ValueError, TypeError):
                _floor = None

            chain = broker.get_option_chain(symbol)
            contract = next(
                (c for c in chain.contracts
                 if c.option_type == "put" and abs(c.strike - strike) < 1e-6 and c.expiration == exp),
                None,
            )
            if contract is None:
                repo.resolve_ai_suggestion(conn, sug_id, status="error",
                                           result_note="no encontré ese contrato en la cadena (strike/vto)")
                continue

            underlying = chain.underlying_price
            premium = contract.mid_price
            margin = rules.per_contract_cost(underlying, strike, premium, settings.simulator)
            opp = Opportunity(
                symbol=symbol, action=live_guard.ACTION_OPEN, expiration=exp, strike=strike,
                requested_contracts=contracts_req, bid=contract.bid, ask=contract.ask,
                underlying_price=underlying, collateral_per_contract=margin,
            )
            day = DayState(
                orders_today=repo.count_live_approved_opens_today(conn, as_of),
                orders_this_week=repo.count_live_approved_opens_this_week(conn, as_of),
                deployed_today=repo.sum_live_collateral_today(conn, as_of),
            )
            limits = _limits_with_runtime_flags(settings, conn, as_of)
            # Relajar los topes autónomos para esta orden pedida por el usuario en el chat: honra los
            # contratos pedidos, sin cupo diario/semanal ni tope de colateral. Mantiene enabled/kill switch.
            limits = replace(limits, max_contracts_per_order=contracts_req, max_orders_per_day=0,
                             max_orders_per_week=0, max_total_deployed=1e12)
            walk = WalkConfig(
                narrow_step=lt.price_walk_step, wide_step=getattr(lt, "price_walk_wide_step", 0.05),
                wide_threshold=getattr(lt, "price_walk_wide_threshold", 0.50),
                interval_seconds=lt.price_walk_interval_seconds, stop_at_mid=lt.price_walk_stop_at_mid,
                price_floor=_floor,
            )
            account = AccountSnapshot(cash=_DRY_RUN_CASH, equity=_DRY_RUN_CASH)
            plan = plan_live_order(opp, account, limits, day, armed=True, walk=walk)

            collateral = (plan.final_contracts or 0) * margin
            ctx = _build_ai_open_context(conn, broker, symbol, contract, underlying, strike, premium, margin,
                                         (exp - as_of).days, s["rationale"])
            log_id = repo.insert_live_order_log(
                conn, log_date=as_of, log_ts=datetime.now(), symbol=symbol,
                action=live_guard.ACTION_OPEN, strike=strike, expiration=exp.isoformat(),
                approved=plan.approved, final_contracts=plan.final_contracts or 0,
                start_limit_price=plan.start_limit_price, collateral=collateral,
                dry_run=plan.is_dry_run, sent=False,
                reasons="; ".join(plan.reasons) if plan.reasons else None,
                payload_json=json.dumps(plan.order_payload) if plan.order_payload else None,
                ladder_json=json.dumps(plan.price_ladder) if plan.price_ladder else None,
                bid=opp.bid, ask=opp.ask, open_context_json=json.dumps(ctx), price_floor=_floor,
            )

            if not plan.approved:
                repo.resolve_ai_suggestion(
                    conn, sug_id, status="rejected", live_order_log_id=log_id,
                    result_note="; ".join(plan.reasons) if plan.reasons else "el guardián no la aprobó (cupo/colateral)",
                )
                continue
            if (plan.is_dry_run or not limits.enabled or limits.kill_switch
                    or broker is None or not hasattr(broker, "place_order")):
                repo.resolve_ai_suggestion(
                    conn, sug_id, status="rejected", live_order_log_id=log_id,
                    result_note="modo no-real (dry-run/kill/sin envío): no se mandó",
                )
                continue

            _send_real_order(conn, log_id, broker, opp, plan, walk, lt, open_context=ctx)
            repo.resolve_ai_suggestion(
                conn, sug_id, status="sent", live_order_log_id=log_id,
                result_note="enviada al mercado — mirá el fill en Real Market",
            )
        except Exception as e:
            logger.exception("Asesor: fallo procesando la sugerencia aprobada %s", sug_id)
            try:
                repo.resolve_ai_suggestion(conn, sug_id, status="error", result_note=f"error: {e}")
            except Exception:
                pass


def _process_approved_close(conn, broker, settings, as_of: date, s) -> None:
    """Cierre manual aprobado por el usuario en el chat (usuario 2026-08-11): recompra la posición
    indicada aunque NO cumpla la regla de cierre, por el MISMO camino real (BUY_TO_CLOSE + caminar el
    precio + email + P&L). Solo en modo real y con el mercado abierto; si no llena, reintenta el próximo
    escaneo. Si la posición ya no está abierta, marca la sugerencia como resuelta."""
    lt = settings.live_trading
    sug_id = s["id"]
    symbol = (s["symbol"] or "").strip().upper()
    strike = float(s["strike"])
    try:
        exp = date.fromisoformat(s["expiration"])
    except (ValueError, TypeError):
        repo.resolve_ai_suggestion(conn, sug_id, status="error", result_note="vencimiento inválido en la sugerencia")
        return

    if not lt.enabled or lt.dry_run:
        repo.resolve_ai_suggestion(conn, sug_id, status="rejected", result_note="modo no-real: no se cerró")
        return
    if broker is None or not hasattr(broker, "place_order"):
        repo.resolve_ai_suggestion(conn, sug_id, status="rejected", result_note="sin broker con envío real")
        return

    from options_advisor.scheduler.market_calendar import market_session
    if market_session() != "abierto":
        return  # mercado cerrado: la orden de cierre espera aprobada hasta la apertura

    # ¿Sigue abierta esa posición del robot? Si la sugerencia trae un ID de posición (usuario 2026-08-11:
    # "poné un ID único para saber exacto cuál cerrar"), matcheamos por ID exacto — así no hay confusión
    # entre dos C con distinto strike/vto. Si no, caemos al match por símbolo+strike+vto.
    positions = repo.get_open_real_put_positions(conn)
    _pid = s["position_id"] if "position_id" in s.keys() else None
    if _pid:
        pos = next((p for p in positions if p["id"] == int(_pid)), None)
    else:
        pos = next(
            (p for p in positions if (p["symbol"] or "").strip().upper() == symbol
             and abs(float(p["strike"]) - strike) < 1e-6 and p["expiration"] == exp.isoformat()),
            None,
        )
    if pos is None:
        repo.resolve_ai_suggestion(conn, sug_id, status="sent",
                                   result_note="la posición ya no estaba abierta (nada que cerrar)")
        return

    account_hash = None
    try:
        account_hash = broker.resolve_account_hash(getattr(lt, "account_number", "") or None)
    except Exception:
        logger.exception("Asesor-cierre: no se pudo resolver la cuenta")
    if not account_hash:
        return  # sin cuenta resoluble: reintentar (no descartar la orden del usuario)

    open_at_broker = _puts_cortos_en_el_broker(broker)

    pos_id = pos["id"]
    walk = _walk_config(lt)
    _maybe_close_one_real(conn, broker, account_hash, pos, settings.simulator, as_of, walk,
                          open_at_broker, force_reason="manual_ai")

    # ¿Quedó cerrada? (si no llenó, sigue abierta y se reintenta el próximo escaneo — dejamos 'approved').
    still_open = any(p["id"] == pos_id for p in repo.get_open_real_put_positions(conn))
    if not still_open:
        repo.resolve_ai_suggestion(conn, sug_id, status="sent",
                                   result_note="cerrada — recompra ejecutada (mirá el P&L en Real Market)")
