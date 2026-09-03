"""ENVÍO REAL de la orden COMBINADA de Iron Condor a Schwab (4 patas, NET_CREDIT al abrir / NET_DEBIT al
cerrar) y "caminar" el precio NETO hacia el mid, reemplazando la orden cada `interval_seconds` hasta que
LLENA o se agota la ventana; después reconcilia y, si quedó viva sin llenar, la deja puesta (apertura) o la
cancela (cierre).

Es el análogo del `real_sender` de puts, pero para el condor de 4 patas (usuario 2026-08-13: "pasar el
condor a real con el mismo cerebro del papel, mismo stop loss y mismo profit %"). Se activa SOLO detrás del
guardián del condor real (live_condor_engine), con el sistema encendido/armado y el condor en modo real.

Seguridad: la orden se arma con `build_iron_condor_open/close`, que fija la composición EXACTA de las 4
patas (dirección de cada una testeada) — acá solo caminamos el PRECIO neto, nunca tocamos las patas ni la
cantidad. La red se inyecta vía `broker`; `sleep`/`clock` son inyectables para testear sin tocar Schwab.

Precio de fill: se registra el precio REAL al que Schwab ejecutó las 4 patas, leído de
`orderActivityCollection[].executionLegs[]`. El límite que mandamos queda solo como respaldo.

Antes se anotaba el último límite, con el argumento de que un fill nunca es peor que el límite y por lo
tanto era "conservador". El 2026-09-03, con dinero real: el robot mandó el condor a $1.65 de crédito y
Schwab llenó a $1.75 — quedó anotado $165 en vez de $175. No es solo P&L subestimado: el objetivo de
ganancia y el STOP LOSS del condor se calculan SOBRE EL CRÉDITO de entrada, así que un crédito equivocado
corre los dos umbrales de la posición. El precio al que se ejecutó no se estima: se pregunta.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from options_advisor.execution import schwab_orders as so

logger = logging.getLogger(__name__)

FILLED = "FILLED"
_DEAD_STATES = {"CANCELED", "REJECTED", "EXPIRED"}
_MAX_WALK_SECONDS = 180

SIDE_OPEN = "open"     # abrir el condor = NET_CREDIT
SIDE_CLOSE = "close"   # cerrar el condor = NET_DEBIT


@dataclass
class CondorSendResult:
    ok: bool                        # True si la orden combinada se llegó a COLOCAR
    filled: bool                    # True si LLENÓ
    order_id: str | None = None
    status: str = ""
    # Texto con que el broker explica un estado muerto ("REJECTED: This order may result in an
    # oversold/overbought position..."). Antes se tiraba a la basura y el log solo decía "REJECTED",
    # que no alcanzaba para entender nada (auditoría 2026-08-24).
    status_detalle: str = ""
    fill_price: float | None = None   # precio NETO (por acción) al que REALMENTE se ejecutó
    # De dónde salió `fill_price`: "schwab" (ejecuciones reales) o "limite" (respaldo, Schwab todavía
    # no publicó los fills). Se loguea y se guarda para poder auditar un crédito raro después.
    fill_price_origen: str = ""
    final_limit_price: float | None = None
    replacements: int = 0
    error: str | None = None
    steps: list[dict] = field(default_factory=list)
    # TODOS los ids de orden que este walk llegó a crear, en orden (el colocado + cada reemplazo).
    #
    # Existe por el 2026-09-02, con dinero real. La escalera camina 1.95 → 1.90 → 1.85 → 1.82, y en
    # Schwab CADA reemplazo es una orden NUEVA con id propio. El reemplazo a $1.82 lo rechazaron
    # (fuera de la grilla de 5 centavos), el walk devolvió REJECTED con el id nuevo... y la orden
    # anterior, la de $1.85, SIGUIÓ VIVA. Llenó media hora después. El robot solo miraba el último
    # id: dio la fila por muerta, la borró, y quedó un iron condor abierto en la cuenta que nadie
    # vigilaba — sin stop. Lo cerró el usuario a mano, $295 de pérdida.
    #
    # Un id muerto NO significa que no haya orden viva. Por eso se guardan todos.
    order_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CondorLegs:
    """Los 4 símbolos OCC EXACTOS de la cadena en vivo (no se reconstruyen)."""
    short_put_symbol: str
    long_put_symbol: str
    short_call_symbol: str
    long_call_symbol: str


def extract_condor_net_fill_price(order_info: dict, side: str) -> float | None:
    """Precio NETO por acción al que Schwab REALMENTE ejecutó el condor, a partir de las ejecuciones
    (`orderActivityCollection[].executionLegs[]`) cruzadas con las patas de la orden
    (`orderLegCollection`, que trae la instrucción de cada `legId`).

    Neto = Σ(patas vendidas) − Σ(patas compradas), la misma cuenta que hace la adopción de condors
    huérfanos en `live_condor_engine`. Se devuelve SIEMPRE positivo en el sentido de la operación:
    crédito al abrir, débito al cerrar.

    Devuelve None —y el que llama se queda con el límite— si el dato no es confiable:
      · no hay ejecuciones publicadas todavía (Schwab tarda unos segundos en llenar este bloque);
      · no llegan las 4 patas ejecutadas (un neto de 3 patas no significa nada);
      · las patas llenaron cantidades distintas (condor a medio armar: el neto no es interpretable);
      · el signo sale al revés (crédito ≤ 0 al abrir, débito < 0 al cerrar) → algo se leyó mal.
    NUNCA inventa: ante la duda, None. Un crédito inventado mueve el stop loss."""
    if side not in (SIDE_OPEN, SIDE_CLOSE):
        return None

    # legId → instrucción de esa pata (SELL_* cobra, BUY_* paga).
    patas: dict[object, str] = {}
    for leg in order_info.get("orderLegCollection") or []:
        leg_id = leg.get("legId")
        instr = str(leg.get("instruction") or "").upper()
        if leg_id is None or not instr:
            continue
        patas[leg_id] = instr
    if len(patas) != 4:
        return None

    ejecuciones: dict[object, dict] = {}
    for activity in order_info.get("orderActivityCollection") or []:
        for exec_leg in activity.get("executionLegs") or []:
            leg_id = exec_leg.get("legId")
            if leg_id not in patas:
                continue
            try:
                qty = float(exec_leg.get("quantity") or 0.0)
                price = float(exec_leg.get("price") or 0.0)
            except (TypeError, ValueError):
                return None
            if qty <= 0:
                continue
            acc = ejecuciones.setdefault(leg_id, {"qty": 0.0, "notional": 0.0})
            acc["qty"] += qty
            acc["notional"] += qty * price

    if len(ejecuciones) != 4:
        return None
    cantidades = [d["qty"] for d in ejecuciones.values()]
    if min(cantidades) <= 0 or abs(max(cantidades) - min(cantidades)) > 1e-6:
        # Condor a medio armar (o fills parciales desparejos entre patas): no hay "precio neto".
        return None

    neto = 0.0
    for leg_id, acc in ejecuciones.items():
        promedio = acc["notional"] / acc["qty"]
        instr = patas[leg_id]
        if instr.startswith("SELL"):
            neto += promedio
        elif instr.startswith("BUY"):
            neto -= promedio
        else:
            return None

    if side == SIDE_CLOSE:
        neto = -neto            # cerrar es un DÉBITO: se devuelve positivo, como el límite
        if neto < 0:
            return None
    elif neto <= 0:
        return None             # un iron condor SIEMPRE se abre a crédito
    return round(neto, 4)


def _make_condor_payload(side: str, legs: CondorLegs, quantity: int, net_price: float) -> dict:
    if side == SIDE_OPEN:
        return so.build_iron_condor_open(
            legs.short_put_symbol, legs.long_put_symbol, legs.short_call_symbol, legs.long_call_symbol,
            quantity=quantity, net_credit_limit=net_price,
        )
    return so.build_iron_condor_close(
        legs.short_put_symbol, legs.long_put_symbol, legs.short_call_symbol, legs.long_call_symbol,
        quantity=quantity, net_debit_limit=net_price,
    )


def execute_condor_walk(
    broker,
    account_hash: str,
    side: str,
    legs: CondorLegs,
    quantity: int,
    ladder: list[float],
    *,
    interval_seconds: int = 10,
    poll_seconds: float = 2.0,
    max_seconds: int = _MAX_WALK_SECONDS,
    leave_resting_at_mid: bool = True,
    sleep=time.sleep,
    clock=time.monotonic,
) -> CondorSendResult:
    """Coloca la orden combinada del condor y camina el precio NETO por `ladder` (del más favorable al
    mid). En cada peldaño espera `interval_seconds` sondeando cada `poll_seconds`; si no llenó, reemplaza
    al peldaño siguiente. Al agotar la escalera/el tiempo: si `leave_resting_at_mid` (apertura) DEJA la
    orden viva al mid; si no (cierre) la CANCELA para reintentar limpio. NUNCA cambia patas ni cantidad."""
    result = CondorSendResult(ok=False, filled=False)
    if side not in (SIDE_OPEN, SIDE_CLOSE):
        result.error = f"side inválido: {side!r}"
        return result
    if not ladder:
        result.error = "escalera de precios vacía"
        return result
    if quantity < 1:
        result.error = "cantidad < 1"
        return result

    start = clock()
    rung = 0
    price = ladder[0]

    try:
        order_id = broker.place_order(account_hash, _make_condor_payload(side, legs, quantity, price))
    except Exception as exc:  # noqa: BLE001
        result.error = f"fallo al COLOCAR el condor: {exc}"
        logger.exception("Condor-real: fallo al colocar la orden combinada")
        return result

    result.ok = True
    result.order_id = order_id
    result.order_ids.append(str(order_id))
    result.final_limit_price = price
    result.steps.append({"price": price, "event": "placed", "order_id": order_id})

    def _poll_once() -> str | None:
        try:
            info = broker.get_order(account_hash, result.order_id)
        except Exception:  # noqa: BLE001
            logger.debug("Condor-real: sondeo de estado falló (se reintenta)", exc_info=True)
            return None
        status = (info.get("status") or "").upper()
        result.status = status
        _detalle = info.get("statusDescription") or info.get("statusDescriptions") or ""
        if _detalle:
            result.status_detalle = str(_detalle)[:400]
        if status == FILLED:
            result.filled = True
            _real = extract_condor_net_fill_price(info, side)
            if _real is None:
                # Schwab dice FILLED pero todavía no publicó las ejecuciones: se anota el límite y se
                # deja constancia de que es un respaldo, no el precio de verdad.
                result.fill_price = result.final_limit_price
                result.fill_price_origen = "limite"
                logger.warning("Condor-real: LLENÓ (orden %s) pero Schwab no publicó las ejecuciones — "
                               "se registra el LÍMITE $%.2f como precio de fill",
                               result.order_id, result.final_limit_price or 0.0)
            else:
                result.fill_price = _real
                result.fill_price_origen = "schwab"
                _lim = result.final_limit_price
                if _lim is not None and abs(_real - _lim) >= 0.005:
                    logger.info("Condor-real: fill REAL $%.2f distinto del límite $%.2f (%s) — manda el "
                                "real: el objetivo y el stop se calculan sobre él", _real, _lim, side)
            result.steps.append({"price": result.final_limit_price, "event": "filled",
                                 "fill_price": result.fill_price, "origen": result.fill_price_origen})
            return FILLED
        if status in _DEAD_STATES:
            return status
        return None

    while True:
        waited = 0.0
        while waited < interval_seconds:
            sleep(min(poll_seconds, interval_seconds - waited))
            waited += poll_seconds
            term = _poll_once()
            if term == FILLED:
                return result
            if term in _DEAD_STATES:
                result.error = f"orden {term} antes de llenar"
                result.steps.append({"price": result.final_limit_price, "event": term.lower()})
                return result

        if clock() - start >= max_seconds:
            result.steps.append({"price": result.final_limit_price, "event": "timeout"})
            break
        if rung + 1 >= len(ladder):
            result.steps.append({"price": result.final_limit_price, "event": "mid_reached"})
            break

        rung += 1
        price = ladder[rung]
        try:
            new_id = broker.replace_order(account_hash, result.order_id, _make_condor_payload(side, legs, quantity, price))
        except Exception as exc:  # noqa: BLE001
            result.error = f"fallo al REEMPLAZAR el condor al precio {price}: {exc}"
            logger.exception("Condor-real: fallo al reemplazar la orden combinada")
            break
        result.order_id = new_id
        if str(new_id) not in result.order_ids:
            result.order_ids.append(str(new_id))
        result.final_limit_price = price
        result.replacements += 1
        result.steps.append({"price": price, "event": "replaced", "order_id": new_id})

    # Reconciliación final: por si llenó justo en el último sondeo.
    term = _poll_once()
    if term == FILLED:
        return result
    if term in _DEAD_STATES:
        return result

    if leave_resting_at_mid:
        if not result.status or result.status in _DEAD_STATES:
            result.status = "WORKING"
        result.steps.append({"price": result.final_limit_price, "event": "resting"})
        logger.info("Condor-real: orden combinada queda PUESTA al mid %.2f esperando fill (id %s)",
                    result.final_limit_price or 0.0, result.order_id)
        return result

    cancelado = False
    try:
        broker.cancel_order(account_hash, result.order_id)
        cancelado = True
    except Exception:  # noqa: BLE001
        result.error = (result.error or "") + " | fallo al cancelar la orden de cierre colgada"
        logger.exception("Condor-real: no se pudo cancelar la orden de cierre colgada")

    # CARRERA CANCEL/FILL — bug real del 2026-08-24, con dinero de verdad.
    #
    # Entre el último sondeo y el `cancel_order` hay una ventana de milisegundos en la que la orden
    # puede LLENAR. Ese día llenó a $1.25 justo mientras salía el cancel; el walk devolvió CANCELED,
    # el motor dejó la posición como abierta, y pasó 40 MINUTOS mandando recompras que Schwab
    # rechazaba una tras otra ("oversold/overbought position") porque la posición ya no existía.
    # El usuario lo vio en su broker antes que el robot.
    #
    # Cancelar no es una respuesta: hay que volver a PREGUNTAR. Si llenó, gana el fill.
    term = _poll_once()
    if term == FILLED:
        logger.warning("Condor-real: la orden de cierre LLENÓ mientras salía el cancel (orden %s, "
                       "límite $%.2f) — se registra el FILL, no el cancel", result.order_id,
                       result.final_limit_price or 0.0)
        return result

    if cancelado:
        result.status = "CANCELED"
        result.steps.append({"price": result.final_limit_price, "event": "canceled"})
        logger.info("Condor-real: cierre no llenó en la ventana; CANCELADO (se reintenta el próximo tick)")
    return result
