"""Negociación del precio con órdenes límite — "caminar el precio" para capturar el mejor fill posible.

Replica cómo opera el usuario a mano (2026-08-09): al VENDER un put arranca cerca del ask y baja de a
pasos hasta que llena; al RECOMPRARLO arranca cerca del bid y sube de a pasos. Nunca cruza el mid (no
regala más de medio spread). El PASO es adaptativo al spread (el usuario: "en acciones chicas te paga
centavos, en otras dólares... debés saber cuánto bajar dependiendo"): spreads finos → pasos chicos
(hasta 1 centavo) para no saltear buenos precios; spreads anchos → pasos más grandes para llenar en
tiempo razonable dado el intervalo de reemplazo (~30 s).

Todo es lógica PURA (sin red ni broker): el ejecutor real la usa para decidir cada precio límite y
cuándo reemplazar la orden. Así se puede testear al 100% sin arriesgar nada.
"""

from __future__ import annotations

SIDE_SELL = "SELL"   # abrir: vender el put (queremos cobrar lo más ALTO)
SIDE_BUY = "BUY"     # cerrar: recomprar el put (queremos pagar lo más BAJO)


def mid_price(bid: float, ask: float) -> float:
    return round((bid + ask) / 2.0, 2)


def adaptive_step(
    bid: float,
    ask: float,
    *,
    base_step: float = 0.02,
    spread_fraction: float = 0.25,
    min_step: float = 0.01,
    max_step: float = 0.10,
) -> float:
    """Cuánto mover el precio por paso, ADAPTADO al spread (en $ de prima).

    - `base_step` (0.02 = 2 centavos = $2/contrato): el paso preferido del usuario para spreads normales.
    - Para spreads ANCHOS el paso crece hasta `spread_fraction` del spread (así llena en tiempo razonable).
    - Para spreads muy FINOS baja hasta `min_step` (1 centavo) para no saltear buenos precios.
    - Nunca supera `max_step` (freno de seguridad para no dar saltos enormes).

    Ejemplos: spread 0.04 → paso 0.02 (base); spread 0.40 → paso 0.10 (cap); spread 0.02 → paso 0.02→
    pero clamp por medio-spread lo baja (ver `next_limit_price`)."""
    spread = max(0.0, round(ask - bid, 4))
    step = max(base_step, round(spread * spread_fraction, 2))
    step = min(step, max_step)
    step = max(step, min_step)
    return round(step, 2)


def spread_based_step(
    bid: float,
    ask: float,
    *,
    narrow_step: float = 0.02,
    wide_step: float = 0.05,
    wide_threshold: float = 0.50,
) -> float:
    """El paso según el ANCHO del spread (usuario 2026-08-09): en spreads GRANDES el paso es más grande
    ($5/contrato = 0.05) para no gastar decenas de envíos caminando de a $2; en spreads CHICOS el paso es
    de $2/contrato (0.02). El corte es `wide_threshold` ($0.50 por default).

    Ej.: strike 132 con bid 11.50 / ask 12.50 → spread 1.00 > 0.50 → paso 0.05 ($5/contrato).
         TSLA 5.50–5.80 → spread 0.30 ≤ 0.50 → paso 0.02 ($2/contrato)."""
    spread = max(0.0, round(ask - bid, 4))
    return wide_step if spread > wide_threshold else narrow_step


def sell_stop_price(bid: float, ask: float, *, stop_at_mid: bool = True,
                    price_floor: float | None = None) -> float | None:
    """El precio MÁS BAJO al que aceptamos VENDER: el mayor entre el mid (si `stop_at_mid`) y el piso
    duro pedido por el usuario (`price_floor`, ej. 'no bajes de 3.00'). None si no hay ningún tope.
    Con esto la orden nunca se recoloca por debajo de lo que el usuario pidió, aunque el mercado caiga
    (usuario 2026-08-11: la orden llenó a 2.94 pidiendo mínimo 3.00 — no vuelve a pasar)."""
    lo = mid_price(bid, ask) if stop_at_mid else None
    if price_floor is not None:
        lo = price_floor if lo is None else max(lo, price_floor)
    return lo


def next_limit_price(
    side: str,
    bid: float,
    ask: float,
    *,
    step: float,
    current: float | None = None,
    stop_at_mid: bool = True,
    price_floor: float | None = None,
) -> float:
    """Próximo precio límite al caminar hacia el mid.

    - Primer intento (`current=None`): VENDER arranca en `ask - step`; RECOMPRAR en `bid + step`.
    - Intentos siguientes: VENDER baja `step`; RECOMPRAR sube `step`.
    - Con `stop_at_mid` (default) nunca cruza el mid: al vender no baja del mid; al recomprar no sube
      del mid. Así el peor caso es llenar al mid (medio spread), nunca peor.
    - `price_floor` (solo VENTA): piso DURO extra pedido por el usuario. Al vender, el precio nunca baja
      de `price_floor` (aunque el mid esté por debajo). Si el piso queda por encima del mercado, la orden
      simplemente no llena — es lo correcto: mejor no vender que vender por debajo de lo pedido.

    Devuelve el precio redondeado a centavos."""
    m = mid_price(bid, ask)
    if side == SIDE_SELL:
        nxt = (ask - step) if current is None else (current - step)
        lo = sell_stop_price(bid, ask, stop_at_mid=stop_at_mid, price_floor=price_floor)
        if lo is not None:
            nxt = max(nxt, lo)       # vender: nunca por debajo del mid NI del piso pedido
        return round(nxt, 2)
    if side == SIDE_BUY:
        nxt = (bid + step) if current is None else (current + step)
        if stop_at_mid:
            nxt = min(nxt, m)        # recomprar: nunca por encima del mid
        return round(nxt, 2)
    raise ValueError(f"side inválido: {side!r} (usá SIDE_SELL o SIDE_BUY)")


def reached_mid(side: str, price: float, bid: float, ask: float, *,
                price_floor: float | None = None) -> bool:
    """¿El precio actual ya tocó (o cruzó) el tope? Si es así, el ejecutor deja de caminar y mantiene la
    orden ahí — es el peor precio que aceptamos (usuario: "estoy dispuesto hasta mid price"). Al VENDER,
    el tope es `max(mid, price_floor)`: con un piso duro pedido por el usuario, se detiene ANTES del mid."""
    m = mid_price(bid, ask)
    if side == SIDE_SELL:
        lo = sell_stop_price(bid, ask, stop_at_mid=True, price_floor=price_floor)
        return price <= (lo if lo is not None else m)
    if side == SIDE_BUY:
        return price >= m
    raise ValueError(f"side inválido: {side!r}")


def build_price_ladder(
    side: str,
    bid: float,
    ask: float,
    *,
    step: float | None = None,
    base_step: float = 0.02,
    spread_fraction: float = 0.25,
    min_step: float = 0.01,
    max_step: float = 0.10,
    stop_at_mid: bool = True,
    price_floor: float | None = None,
) -> list[float]:
    """La secuencia COMPLETA de precios que se irían probando, del más favorable al tope (inclusive).
    Útil para mostrarle al usuario en el dashboard cómo va a negociar, y para tests.

    Si se pasa `step`, se usa ESE paso fijo (usuario 2026-08-09: "bajo de 2 en 2" = 0.02 siempre). Si no,
    cae al paso adaptativo (opcional, no default). `price_floor` (solo VENTA): piso duro — la escalera nunca
    baja de ahí (usuario 2026-08-11: 'no bajes de 3.00')."""
    if step is None:
        step = adaptive_step(bid, ask, base_step=base_step, spread_fraction=spread_fraction,
                             min_step=min_step, max_step=max_step)
    ladder: list[float] = []
    price = next_limit_price(side, bid, ask, step=step, current=None, stop_at_mid=stop_at_mid,
                             price_floor=price_floor)
    ladder.append(price)
    # Camina hasta el tope (mid o piso), sin loop infinito (freno duro por las dudas).
    for _ in range(200):
        if reached_mid(side, price, bid, ask, price_floor=price_floor):
            break
        price = next_limit_price(side, bid, ask, step=step, current=price, stop_at_mid=stop_at_mid,
                                 price_floor=price_floor)
        if ladder and price == ladder[-1]:
            break
        ladder.append(price)
    return ladder
