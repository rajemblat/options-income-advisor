"""Armado de órdenes de opciones para el Trader API de Schwab (+ descripción legible para dry-run).

Este módulo SOLO construye el JSON de la orden y lo describe en texto — NO la envía. El envío real
(POST /accounts/{hash}/orders) se agrega recién en el ejecutor, detrás del guardián y con el usuario
presente (usuario 2026-08-09, trading real con poco capital: "buena seguridad, que sea perfecto").

Todo acá es PURO y testeable: el símbolo OCC, el payload y la descripción se validan sin tocar la red.
"""

from __future__ import annotations

from datetime import date

# Instrucciones de opciones de Schwab.
SELL_TO_OPEN = "SELL_TO_OPEN"
BUY_TO_CLOSE = "BUY_TO_CLOSE"
BUY_TO_OPEN = "BUY_TO_OPEN"      # comprar las ALAS del iron condor al abrir
SELL_TO_CLOSE = "SELL_TO_CLOSE"  # vender las alas al cerrar el condor


def occ_option_symbol(root: str, expiration: date, option_type: str, strike: float) -> str:
    """Símbolo OCC de 21 caracteres que Schwab espera para una opción:
    `ROOT(6, izq, con espacios) + YYMMDD + C/P + strike×1000 (8 dígitos con ceros)`.

    Ej.: AAL put $11 vto 2026-09-18 → 'AAL   260918P00011000'.
    Un símbolo mal armado = orden rechazada o sobre el instrumento equivocado, por eso está testeado."""
    r = root.strip().upper()
    if not r or len(r) > 6:
        raise ValueError(f"root inválido para OCC: {root!r}")
    ot = option_type.strip().upper()
    if ot in ("P", "PUT"):
        ot = "P"
    elif ot in ("C", "CALL"):
        ot = "C"
    else:
        raise ValueError(f"option_type inválido: {option_type!r}")
    if strike <= 0:
        raise ValueError(f"strike inválido: {strike!r}")
    root_field = f"{r:<6}"                       # 6 chars, relleno con espacios a la derecha
    date_field = expiration.strftime("%y%m%d")   # YYMMDD
    strike_field = f"{round(strike * 1000):08d}"  # strike×1000, 8 dígitos
    sym = f"{root_field}{date_field}{ot}{strike_field}"
    assert len(sym) == 21, f"OCC debe ser 21 chars, salió {len(sym)}: {sym!r}"
    return sym


def build_option_order(
    occ_symbol: str,
    instruction: str,
    quantity: int,
    limit_price: float,
    *,
    duration: str = "DAY",
    session: str = "NORMAL",
) -> dict:
    """Payload de una orden LÍMITE de una sola pata de opción (SINGLE) para Schwab. Siempre límite —
    nunca market (usuario 2026-08-09). `quantity` en contratos, `limit_price` en $ de prima."""
    if quantity < 1:
        raise ValueError("quantity debe ser ≥ 1")
    if limit_price <= 0:
        raise ValueError("limit_price debe ser > 0")
    if instruction not in (SELL_TO_OPEN, BUY_TO_CLOSE):
        raise ValueError(f"instruction no soportada: {instruction!r}")
    return {
        "orderType": "LIMIT",
        "session": session,
        "price": f"{limit_price:.2f}",
        "duration": duration,
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": instruction,
                "quantity": quantity,
                "instrument": {"symbol": occ_symbol, "assetType": "OPTION"},
            }
        ],
    }


def build_sell_put_to_open(root: str, expiration: date, strike: float, quantity: int, limit_price: float) -> dict:
    """Orden para ABRIR una venta de put (cash-secured/naked): SELL_TO_OPEN, límite."""
    occ = occ_option_symbol(root, expiration, "P", strike)
    return build_option_order(occ, SELL_TO_OPEN, quantity, limit_price)


def build_buy_put_to_close(root: str, expiration: date, strike: float, quantity: int, limit_price: float) -> dict:
    """Orden para CERRAR (recomprar) un put vendido: BUY_TO_CLOSE, límite."""
    occ = occ_option_symbol(root, expiration, "P", strike)
    return build_option_order(occ, BUY_TO_CLOSE, quantity, limit_price)


def _condor_leg(instruction: str, occ_symbol: str, quantity: int) -> dict:
    return {"instruction": instruction, "quantity": quantity, "instrument": {"symbol": occ_symbol, "assetType": "OPTION"}}


# ═══ PRECIO NETO DE UN SPREAD DE ÍNDICE: MÚLTIPLOS DE 5 CENTAVOS ═══
#
# El 2026-09-02, con dinero real: el robot mandó un iron condor de SPX a $1.82 de crédito neto y
# Schwab lo RECHAZÓ — "Spread orders for SPX options must be priced in 5-cent increments." La fila
# se descartó, pero la posición terminó viva en la cuenta sin que el robot la vigilara: sin stop,
# sin objetivo. La cerró el usuario a mano, en pérdida.
#
# El $1.82 no salió de la nada: la escalera camina de 5 en 5 pero termina clavada en el MID exacto,
# y el mid casi nunca cae en la grilla. Por eso el redondeo va acá, en el armado del payload: es el
# único lugar por el que pasan TODAS las órdenes de condor, incluidos los reemplazos.
#
# Dirección del redondeo — siempre en contra nuestra, nunca a favor:
#   · CRÉDITO al abrir  → hacia ABAJO (pedimos un poco menos, la orden es válida y llena).
#   · DÉBITO al cerrar  → hacia ARRIBA (ofrecemos un poco más; salir siempre pesa más que ahorrar
#     4 centavos, sobre todo cuando el que cierra es el stop loss).
CONDOR_PRICE_TICK = 0.05


def redondear_a_tick(precio: float, *, hacia_abajo: bool, tick: float = CONDOR_PRICE_TICK) -> float:
    """Ajusta `precio` a la grilla de `tick` (default 5 centavos, la regla de SPX). `hacia_abajo`
    trunca (para créditos), si no redondea hacia arriba (para débitos). Trabaja en centavos enteros
    para no arrastrar el error binario de los flotantes (0.05 no es exacto en binario)."""
    if tick <= 0:
        return round(precio, 2)
    paso = int(round(tick * 100))
    centavos = int(round(precio * 100))
    ajustado = (centavos // paso) * paso if hacia_abajo else -((-centavos) // paso) * paso
    return round(ajustado / 100.0, 2)


def build_iron_condor_open(
    short_put_symbol: str, long_put_symbol: str, short_call_symbol: str, long_call_symbol: str,
    quantity: int, net_credit_limit: float, *, duration: str = "DAY", session: str = "NORMAL",
) -> dict:
    """Orden COMBINADA de 4 patas para ABRIR un iron condor en Schwab, a CRÉDITO NETO límite (usuario
    2026-08-13: pasar el condor a real con el mismo cerebro del paper). Recibe los símbolos OCC EXACTOS de
    la cadena (no se reconstruyen: crítico para índices como $SPX/SPXW en 0DTE). Composición fija y testeada:
      · SELL_TO_OPEN el put corto   (cobra prima)
      · BUY_TO_OPEN  el put largo   (ala de abajo, protección)
      · SELL_TO_OPEN el call corto  (cobra prima)
      · BUY_TO_OPEN  el call largo  (ala de arriba, protección)
    `net_credit_limit` = crédito neto mínimo aceptado (>0), en $ de prima por contrato. Riesgo acotado por
    las alas; nunca es una venta desnuda."""
    if quantity < 1:
        raise ValueError("quantity debe ser ≥ 1")
    # Grilla de 5 centavos: hacia ABAJO. Se ajusta ANTES de validar, para que un crédito de $0.03
    # (que redondea a 0) muera acá y no salga a Schwab.
    net_credit_limit = redondear_a_tick(net_credit_limit, hacia_abajo=True)
    if net_credit_limit <= 0:
        raise ValueError("el crédito neto (net_credit_limit) debe ser > 0 — un iron condor SIEMPRE se abre a crédito")
    syms = [short_put_symbol, long_put_symbol, short_call_symbol, long_call_symbol]
    if any(not s for s in syms) or len(set(syms)) != 4:
        raise ValueError("hacen falta 4 símbolos OCC distintos (put corto/largo, call corto/largo)")
    return {
        "orderType": "NET_CREDIT",
        "session": session,
        "price": f"{net_credit_limit:.2f}",
        "duration": duration,
        "orderStrategyType": "SINGLE",
        "complexOrderStrategyType": "IRON_CONDOR",
        "orderLegCollection": [
            _condor_leg(SELL_TO_OPEN, short_put_symbol, quantity),
            _condor_leg(BUY_TO_OPEN, long_put_symbol, quantity),
            _condor_leg(SELL_TO_OPEN, short_call_symbol, quantity),
            _condor_leg(BUY_TO_OPEN, long_call_symbol, quantity),
        ],
    }


def build_iron_condor_close(
    short_put_symbol: str, long_put_symbol: str, short_call_symbol: str, long_call_symbol: str,
    quantity: int, net_debit_limit: float, *, duration: str = "DAY", session: str = "NORMAL",
) -> dict:
    """Orden COMBINADA de 4 patas para CERRAR un iron condor, a DÉBITO NETO límite. Revierte cada pata:
      · BUY_TO_CLOSE  el put corto   (recompra)
      · SELL_TO_CLOSE el put largo   (vende el ala)
      · BUY_TO_CLOSE  el call corto  (recompra)
      · SELL_TO_CLOSE el call largo  (vende el ala)
    `net_debit_limit` = débito neto máximo a pagar para cerrar (≥0). Ej.: cerrar al 50% de un crédito de
    $2.00 = pagar ~$1.00 de débito."""
    if quantity < 1:
        raise ValueError("quantity debe ser ≥ 1")
    # Grilla de 5 centavos: hacia ARRIBA. Pagar hasta 4 centavos de más es baratísimo comparado con
    # que Schwab rechace la recompra y la posición quede abierta.
    net_debit_limit = redondear_a_tick(net_debit_limit, hacia_abajo=False)
    if net_debit_limit < 0:
        raise ValueError("el débito neto (net_debit_limit) no puede ser negativo")
    syms = [short_put_symbol, long_put_symbol, short_call_symbol, long_call_symbol]
    if any(not s for s in syms) or len(set(syms)) != 4:
        raise ValueError("hacen falta 4 símbolos OCC distintos (put corto/largo, call corto/largo)")
    return {
        "orderType": "NET_DEBIT",
        "session": session,
        "price": f"{net_debit_limit:.2f}",
        "duration": duration,
        "orderStrategyType": "SINGLE",
        "complexOrderStrategyType": "IRON_CONDOR",
        "orderLegCollection": [
            _condor_leg(BUY_TO_CLOSE, short_put_symbol, quantity),
            _condor_leg(SELL_TO_CLOSE, long_put_symbol, quantity),
            _condor_leg(BUY_TO_CLOSE, short_call_symbol, quantity),
            _condor_leg(SELL_TO_CLOSE, long_call_symbol, quantity),
        ],
    }


def describe_order(order: dict) -> str:
    """Texto legible de lo que la orden HARÍA — para el log de dry-run y el dashboard, así el usuario
    revisa que sea exactamente lo que espera antes de arriesgar nada."""
    leg = order["orderLegCollection"][0]
    instr = leg["instruction"]
    verbo = "VENDER para abrir" if instr == SELL_TO_OPEN else "RECOMPRAR para cerrar"
    return (f"[DRY-RUN] {verbo} {leg['quantity']} contrato(s) de {leg['instrument']['symbol']} "
            f"a límite ${order['price']} ({order['duration']}). NO enviada.")
