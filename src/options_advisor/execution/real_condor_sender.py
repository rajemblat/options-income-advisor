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

Nota sobre el precio de fill: para una orden límite combinada, el fill nunca es PEOR que el límite (un
crédito llena por ≥ el límite; un débito por ≤ el límite). Registramos como precio de entrada/salida el
ÚLTIMO límite al que quedó la orden cuando llenó — conservador y suficiente para el P&L (usuario: poco
capital, prioridad seguridad). El valor exacto se reconcilia contra Schwab en el marcado si hiciera falta.
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
    fill_price: float | None = None   # precio NETO (por acción) al que quedó cuando llenó
    final_limit_price: float | None = None
    replacements: int = 0
    error: str | None = None
    steps: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class CondorLegs:
    """Los 4 símbolos OCC EXACTOS de la cadena en vivo (no se reconstruyen)."""
    short_put_symbol: str
    long_put_symbol: str
    short_call_symbol: str
    long_call_symbol: str


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
        if status == FILLED:
            result.filled = True
            result.fill_price = result.final_limit_price   # límite al que quedó = fill conservador
            result.steps.append({"price": result.final_limit_price, "event": "filled"})
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

    try:
        broker.cancel_order(account_hash, result.order_id)
        result.status = "CANCELED"
        result.steps.append({"price": result.final_limit_price, "event": "canceled"})
        logger.info("Condor-real: cierre no llenó en la ventana; CANCELADO (se reintenta el próximo tick)")
    except Exception:  # noqa: BLE001
        result.error = (result.error or "") + " | fallo al cancelar la orden de cierre colgada"
        logger.exception("Condor-real: no se pudo cancelar la orden de cierre colgada")
    return result
