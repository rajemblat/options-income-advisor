"""ENVÍO REAL de la orden a Schwab: coloca la orden límite y CAMINA el precio hacia el mid,
reemplazándola cada `interval_seconds`, hasta que LLENA o se agota la ventana; después reconcilia
(lee el fill) y, si quedó viva sin llenar, la CANCELA para no dejar una orden colgada sin supervisión.

Es el último eslabón del trading real (usuario 2026-08-10: "poner en real 100% con tope de 1 operación
diaria"). Se activa SOLO cuando el sistema está encendido (enabled), armado y fuera de dry-run, y siempre
detrás del guardián (que ya recortó contratos y fijó el precio inicial). Con poco capital, la prioridad es
seguridad: NUNCA manda más de lo pedido (el payload viene fijo del plan) y acota el tiempo total.

La red se inyecta vía el `broker` (con place_order/get_order/replace_order/cancel_order) y `sleep`/`clock`
son inyectables, así todo el caminado se testea sin tocar Schwab ni esperar de verdad."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from options_advisor.execution import schwab_orders as so
from options_advisor.execution.live_guard import ACTION_OPEN

logger = logging.getLogger(__name__)

# Estado terminal exitoso.
FILLED = "FILLED"
# Estados en los que la orden murió sin llenar (no tiene sentido seguir esperándola).
_DEAD_STATES = {"CANCELED", "REJECTED", "EXPIRED"}
# Techo duro de tiempo total del caminado (segundos) — nunca colgar el escaneo del robot.
_MAX_WALK_SECONDS = 180


@dataclass
class SendResult:
    ok: bool                       # True si la orden se llegó a COLOCAR (haya llenado o no)
    filled: bool                   # True si LLENÓ
    order_id: str | None = None    # último orderId de Schwab (tras los reemplazos)
    status: str = ""               # estado final visto
    fill_price: float | None = None
    filled_contracts: int = 0
    final_limit_price: float | None = None
    replacements: int = 0
    error: str | None = None
    steps: list[dict] = field(default_factory=list)  # traza precio→estado, para el log/dashboard


def extract_fill_price(order_info: dict) -> float | None:
    """Prima promedio ponderada a la que llenó, a partir de orderActivityCollection[].executionLegs[].
    None si todavía no hay ejecuciones. Pondera por cantidad para fills parciales en varios eventos."""
    total_qty = 0.0
    total_notional = 0.0
    for activity in order_info.get("orderActivityCollection", []) or []:
        for leg in activity.get("executionLegs", []) or []:
            qty = leg.get("quantity") or 0.0
            price = leg.get("price") or 0.0
            total_qty += qty
            total_notional += qty * price
    if total_qty <= 0:
        return None
    return round(total_notional / total_qty, 4)


def execute_live_walk(
    broker,
    account_hash: str,
    opp,
    contracts: int,
    ladder: list[float],
    *,
    interval_seconds: int = 10,
    poll_seconds: float = 2.0,
    max_seconds: int = _MAX_WALK_SECONDS,
    leave_resting_at_mid: bool = True,
    sleep=time.sleep,
    clock=time.monotonic,
) -> SendResult:
    """Coloca la orden y camina el precio por `ladder` (del más favorable al mid). En cada peldaño espera
    `interval_seconds` sondeando el estado cada `poll_seconds`; si no llenó, reemplaza al peldaño siguiente.
    Al agotar la escalera o el tiempo: si `leave_resting_at_mid` (default, usuario 2026-08-10 "dejala puesta
    esperando, como opero a mano") DEJA la orden viva al mid esperando fill; si no, la CANCELA. NUNCA cambia
    contratos ni cruza el mid (la escalera ya termina en el mid)."""
    result = SendResult(ok=False, filled=False)
    if not ladder:
        result.error = "escalera de precios vacía"
        return result
    if contracts < 1:
        result.error = "contratos < 1"
        return result

    # LLENADOS PARCIALES (auditoria 2026-08-22). Schwab NO tiene un estado "PARTIALLY_FILLED": una
    # orden de 4 contratos con 2 llenados sigue reportando status="WORKING" con filledQuantity=2.
    #
    # El bug: `replace_order` CANCELA el remanente y crea una orden NUEVA por la cantidad que se le
    # pase. Como el payload usaba siempre `contracts` completo, un llenado parcial en un peldaño
    # terminaba comprando de mas. Con 4 puts de AAL aprobados: el peldaño 1 llena 2, el reemplazo
    # pide 4 otra vez y llena -> 6 puts cortos. Un 50% mas de lo que el guardian autorizo, con mas
    # colateral del que el tope conto. El assert de live_guard protege la DECISION, no la EJECUCION.
    #
    # Ahora se lleva la cuenta de lo ya llenado y cada reemplazo pide solo el remanente.
    llenados_parciales = 0

    def _pendientes() -> int:
        return max(0, contracts - llenados_parciales)

    def make_payload(price: float, cantidad: int | None = None) -> dict:
        n = contracts if cantidad is None else cantidad
        if opp.action == ACTION_OPEN:
            return so.build_sell_put_to_open(opp.symbol, opp.expiration, opp.strike, n, price)
        return so.build_buy_put_to_close(opp.symbol, opp.expiration, opp.strike, n, price)

    start = clock()
    rung = 0
    price = ladder[0]

    try:
        order_id = broker.place_order(account_hash, make_payload(price))
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de red/rechazo: no colocada
        result.error = f"fallo al COLOCAR la orden: {exc}"
        logger.exception("Live: fallo al colocar la orden real de %s", getattr(opp, "symbol", "?"))
        return result

    result.ok = True
    result.order_id = order_id
    result.final_limit_price = price
    result.steps.append({"price": price, "event": "placed", "order_id": order_id})

    def _poll_once() -> str | None:
        """Devuelve el estado si la orden ya es terminal (FILLED o muerta), None si sigue viva."""
        try:
            info = broker.get_order(account_hash, result.order_id)
        except Exception:  # noqa: BLE001 — un sondeo que falla no corta el caminado
            logger.debug("Live: sondeo de estado falló (se reintenta)", exc_info=True)
            return None
        nonlocal llenados_parciales
        status = (info.get("status") or "").upper()
        result.status = status
        try:
            _ya = int(info.get("filledQuantity") or 0)
        except (TypeError, ValueError):
            _ya = 0
        if status == FILLED:
            result.filled = True
            result.fill_price = extract_fill_price(info)
            result.filled_contracts = llenados_parciales + (_ya or _pendientes())
            result.steps.append({"price": result.final_limit_price, "event": "filled",
                                 "fill_price": result.fill_price})
            return FILLED
        if status in _DEAD_STATES:
            # Una orden que murio pudo haber llenado PARTE. Registrarlo es lo que evita posiciones
            # reales invisibles: `get_open_real_put_positions` exige order_status='FILLED', asi que
            # una orden EXPIRED con 2 contratos llenados y filled_contracts=0 dejaba 2 puts cortos
            # sin profit target, sin stop-loss y sin contar para ningun tope.
            if _ya > llenados_parciales:
                # MAXIMO, no suma: `filledQuantity` es acumulado de la orden, no un incremento.
                # Sumarlo contaba dos veces lo que ya habia visto el sondeo anterior.
                llenados_parciales = _ya
                result.filled_contracts = llenados_parciales
                result.fill_price = extract_fill_price(info) or result.fill_price
                result.steps.append({"price": result.final_limit_price,
                                     "event": f"{status.lower()}_parcial", "llenados": _ya})
            return status
        # Sigue viva. Si ya llenó parte, lo anotamos para que el proximo reemplazo pida SOLO el
        # resto. Se toma el maximo y nunca se baja: los sondeos pueden llegar desordenados y un
        # conteo que retrocede haria pedir de mas, que es justo lo que queremos evitar.
        if _ya > llenados_parciales:
            llenados_parciales = _ya
            result.filled_contracts = llenados_parciales
            result.steps.append({"price": result.final_limit_price, "event": "parcial", "llenados": _ya})
        return None

    while True:
        # Espera este peldaño sondeando cada poll_seconds.
        waited = 0.0
        while waited < interval_seconds:
            sleep(min(poll_seconds, interval_seconds - waited))
            waited += poll_seconds
            term = _poll_once()
            if term == FILLED:
                return result
            if term in _DEAD_STATES:
                # Rechazada/cancelada/expirada de forma inesperada — no seguimos.
                result.error = f"orden {term} antes de llenar"
                result.steps.append({"price": result.final_limit_price, "event": term.lower()})
                return result

        # No llenó en este peldaño. ¿Cortamos por tiempo o porque ya estamos en el mid (último peldaño)?
        if clock() - start >= max_seconds:
            result.steps.append({"price": result.final_limit_price, "event": "timeout"})
            break
        if rung + 1 >= len(ladder):
            result.steps.append({"price": result.final_limit_price, "event": "mid_reached"})
            break

        # Camina un peldaño: reemplaza la orden al precio siguiente (más cerca del mid).
        rung += 1
        price = ladder[rung]
        # Cuanto falta: `replace_order` cancela el remanente y crea una orden NUEVA por la cantidad
        # que le pasemos, asi que pedir `contracts` otra vez duplicaria lo ya llenado.
        #
        # Deliberadamente NO usamos `filledQuantity` para declarar la orden llenada — solo el
        # `status` decide eso. Aca sirve unicamente para dimensionar el reemplazo, donde
        # equivocarse hacia abajo es inofensivo (se manda de menos y el proximo escaneo reintenta),
        # mientras que equivocarse hacia arriba compra contratos que nadie aprobo.
        _falta = _pendientes()
        if _falta <= 0:
            # Segun los sondeos ya se llenó todo. No reemplazamos nada; la reconciliacion final de
            # abajo lee el estado real y decide, que es la unica fuente de verdad.
            result.steps.append({"price": result.final_limit_price, "event": "sin_remanente"})
            break
        try:
            new_id = broker.replace_order(account_hash, result.order_id, make_payload(price, _falta))
        except Exception as exc:  # noqa: BLE001 — si el reemplazo falla, dejamos de caminar y reconciliamos
            result.error = f"fallo al REEMPLAZAR al precio {price}: {exc}"
            logger.exception("Live: fallo al reemplazar la orden real de %s", getattr(opp, "symbol", "?"))
            break
        result.order_id = new_id
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
        # Deja la orden PUESTA al mid, viva, esperando que la acepten (usuario 2026-08-10). Su estado NO es
        # "muerto", así que OCUPA el cupo del día → el robot no genera más órdenes (sin churn) y esta llena
        # cuando el mercado viene a tu precio. Al cierre, la orden DAY expira sola.
        if not result.status or result.status in _DEAD_STATES:
            result.status = "WORKING"
        result.steps.append({"price": result.final_limit_price, "event": "resting"})
        logger.info("Live: orden de %s queda PUESTA al mid %.2f esperando fill (id %s)",
                    getattr(opp, "symbol", "?"), result.final_limit_price or 0.0, result.order_id)
        return result

    try:
        broker.cancel_order(account_hash, result.order_id)
        result.status = "CANCELED"
        result.steps.append({"price": result.final_limit_price, "event": "canceled"})
        logger.info("Live: orden de %s no llenó en la ventana; CANCELADA (sin posición)", getattr(opp, "symbol", "?"))
    except Exception:  # noqa: BLE001
        result.error = (result.error or "") + " | fallo al cancelar la orden colgada"
        logger.exception("Live: no se pudo cancelar la orden colgada de %s", getattr(opp, "symbol", "?"))
    return result
