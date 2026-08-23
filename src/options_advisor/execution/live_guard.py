"""Guardián de órdenes reales — el freno de seguridad que se pone DELANTE de cualquier envío al broker.

Diseño (usuario 2026-08-07, trading real en Schwab con poco capital): "buena seguridad, que no me
ponga más de lo que pedí, que sea todo como yo lo estoy pidiendo, pero que sea perfecto".

Propiedad central e inviolable: `evaluate()` NUNCA devuelve más contratos que los pedidos. La cantidad
final es `min(pedido, todos los topes)`, y si algún tope duro no se cumple, la orden se RECHAZA (no se
"arregla" agrandándola). Esto está afirmado con un assert al final y cubierto por tests.

Capas de defensa (todas deben pasar para una APERTURA):
  1. `enabled` maestro en False  → rechaza (default: trading real APAGADO).
  2. `kill_switch` en True       → rechaza (freno de emergencia).
  3. `armed` en False            → rechaza (hay que "armar" el día desde el dashboard).
  4. whitelist de símbolos       → si está definida, solo esos símbolos.
  5. tope de precio del subyacente.
  6. tope de órdenes por día.
  7. tope de contratos por orden (recorta hacia abajo, nunca hacia arriba).
  8. tope de notional por orden  (recorta contratos para entrar; si ni 1 entra, rechaza).
  9. tope de capital total comprometido en el día (recorta; si ni 1 entra, rechaza).
 10. colchón de cash libre mínimo (recorta; si ni 1 entra, rechaza).

Los CIERRES (BUY_TO_CLOSE) reducen riesgo, así que solo pasan por las compuertas 1-3 (enabled / kill /
armed) y el tope de contratos (nunca cerrar más de lo que hay abierto): siempre hay que poder salir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import logging

logger = logging.getLogger(__name__)

CONTRACT_MULTIPLIER = 100

ACTION_OPEN = "SELL_TO_OPEN"
ACTION_CLOSE = "BUY_TO_CLOSE"


@dataclass(frozen=True)
class IntendedOrder:
    """Lo que el robot QUIERE hacer, antes de pasar por el guard."""
    symbol: str
    action: str                 # ACTION_OPEN | ACTION_CLOSE
    option_type: str            # "PUT" (por ahora solo CSP)
    strike: float
    expiration: str             # ISO
    requested_contracts: int
    limit_price: float          # precio límite (nunca market)
    underlying_price: float
    # Colateral/MARGEN que el broker traba por contrato (usuario 2026-08-09: "NU te pide ~$300, C ~$1200").
    # Es lo que consume del presupuesto (`max_total_deployed`), distinto del notional de asignación
    # (strike×100). 0 = desconocido → el guard cae, conservador, al notional strike×100.
    collateral_per_contract: float = 0.0


@dataclass(frozen=True)
class AccountSnapshot:
    """Estado de la cuenta real al momento de evaluar."""
    cash: float
    equity: float


@dataclass(frozen=True)
class DayState:
    """Cuánto se operó REAL, para los topes diarios Y semanales (usuario 2026-08-09: "una orden diaria,
    no más de 5 por semana")."""
    orders_today: int = 0
    deployed_today: float = 0.0
    orders_this_week: int = 0


@dataclass(frozen=True)
class LiveLimits:
    """Topes DUROS de trading real. Copia defensiva de lo que el usuario configuró — el guard nunca
    los relaja. Deben coincidir con (o ser más estrictos que) lo que pide la estrategia."""
    enabled: bool = False
    dry_run: bool = True
    kill_switch: bool = False
    require_manual_arm: bool = True
    max_contracts_per_order: int = 4
    max_notional_per_order: float = 40_000.0
    max_orders_per_day: int = 5
    max_orders_per_week: int = 0            # 0 = sin tope semanal (usuario 2026-08-09: Fase 1 = 5/semana)
    max_total_deployed: float = 50_000.0
    max_underlying_price: float = 700.0
    min_account_cash_buffer: float = 0.0
    allowed_symbols: tuple[str, ...] = ()
    price_cap_exempt_symbols: tuple[str, ...] = ()   # símbolos exentos del tope de precio (usuario: SPY)


@dataclass
class GuardDecision:
    approved: bool
    final_contracts: int
    notional: float
    reasons: list[str] = field(default_factory=list)   # por qué se rechazó y/o se recortó
    is_dry_run: bool = True

    @property
    def rejected(self) -> bool:
        return not self.approved


def limits_from_settings(live) -> LiveLimits:
    """Construye `LiveLimits` desde `settings.live_trading` (una `LiveTradingSettings`). Copia defensiva:
    el guard trabaja sobre este objeto inmutable, no sobre el settings vivo."""
    return LiveLimits(
        enabled=live.enabled,
        dry_run=live.dry_run,
        kill_switch=live.kill_switch,
        require_manual_arm=live.require_manual_arm,
        max_contracts_per_order=live.max_contracts_per_order,
        max_notional_per_order=live.max_notional_per_order,
        max_orders_per_day=live.max_orders_per_day,
        max_orders_per_week=getattr(live, "max_orders_per_week", 0),
        price_cap_exempt_symbols=tuple(getattr(live, "price_cap_exempt_symbols", ()) or ()),
        max_total_deployed=live.max_total_deployed,
        max_underlying_price=live.max_underlying_price,
        min_account_cash_buffer=live.min_account_cash_buffer,
        allowed_symbols=tuple(live.allowed_symbols or ()),
    )


def _collateral(strike: float, contracts: int) -> float:
    """Garantía cash-secured / notional de asignación de un put: strike × 100 × contratos."""
    return strike * CONTRACT_MULTIPLIER * contracts


def evaluate(
    order: IntendedOrder,
    account: AccountSnapshot,
    limits: LiveLimits,
    day: DayState,
    armed: bool,
) -> GuardDecision:
    """Decide si una orden real puede mandarse y con cuántos contratos. NUNCA agranda la orden.

    Devuelve `GuardDecision`. Si `approved` es False, `reasons` explica todos los motivos. Si es True,
    `final_contracts` es lo que efectivamente se mandaría (siempre ≤ `requested_contracts`), y
    `is_dry_run` indica si además está en modo simulación de envío (construye y loguea, no manda)."""
    reasons: list[str] = []
    requested = int(order.requested_contracts)

    # --- Compuertas duras (valen para aperturas Y cierres) ---
    # El sistema está "activo para EVALUAR" si está enabled O en dry_run (así el dry-run muestra el plan
    # aprobado sin enviar nada). El ENVÍO real ocurre aparte, solo si enabled=True y dry_run=False.
    if not (limits.enabled or limits.dry_run):
        reasons.append("Sistema de trading real apagado (enabled=false y dry_run=false).")
    if limits.kill_switch:
        reasons.append("KILL SWITCH activo — todas las órdenes frenadas.")
    if limits.require_manual_arm and not armed:
        reasons.append("El día no está ARMADO — hay que habilitar el trading real de hoy desde el dashboard.")
    if requested < 1:
        reasons.append("Cantidad pedida inválida (<1 contrato).")

    if reasons:
        return GuardDecision(False, 0, 0.0, reasons, limits.dry_run)

    # --- CIERRE: reduce riesgo. Solo limita que no cierre más de lo pedido. Siempre hay que poder salir. ---
    if order.action == ACTION_CLOSE:
        final = min(requested, max(1, limits.max_contracts_per_order))
        if final < requested:
            reasons.append(f"Cierre recortado de {requested} a {final} por tope de contratos.")
        dec = GuardDecision(True, final, _collateral(order.strike, final), reasons, limits.dry_run)
        assert dec.final_contracts <= requested, "INVARIANTE: nunca más que lo pedido"
        return dec

    # --- APERTURA (SELL_TO_OPEN): todas las capas ---
    if limits.allowed_symbols and order.symbol not in limits.allowed_symbols:
        reasons.append(f"{order.symbol} no está en la whitelist de símbolos permitidos.")
    # Tope de precio del subyacente — con excepción para símbolos exentos (usuario 2026-08-09: SPY se
    # opera aunque supere los $700).
    exento_precio = order.symbol in limits.price_cap_exempt_symbols
    if limits.max_underlying_price > 0 and not exento_precio and order.underlying_price >= limits.max_underlying_price:
        reasons.append(f"Precio del subyacente ${order.underlying_price:,.2f} ≥ tope ${limits.max_underlying_price:,.0f}.")
    if limits.max_orders_per_day > 0 and day.orders_today >= limits.max_orders_per_day:
        reasons.append(f"Ya se alcanzó el tope de {limits.max_orders_per_day} órdenes reales por día.")
    if limits.max_orders_per_week > 0 and day.orders_this_week >= limits.max_orders_per_week:
        reasons.append(f"Ya se alcanzó el tope de {limits.max_orders_per_week} órdenes reales por semana.")

    if reasons:
        return GuardDecision(False, 0, 0.0, reasons, limits.dry_run)

    # A partir de acá solo RECORTAMOS contratos hacia abajo (nunca hacia arriba).
    final = requested

    # 1) Tope de contratos por orden.
    if limits.max_contracts_per_order > 0 and final > limits.max_contracts_per_order:
        reasons.append(f"Recortado de {final} a {limits.max_contracts_per_order} por tope de contratos por orden.")
        final = limits.max_contracts_per_order

    # Dos costos por contrato, distintos (usuario 2026-08-09):
    #  - notional_pc = strike×100 = EXPOSICIÓN de asignación (lo que comprarías si te asignan).
    #  - collateral_pc = MARGEN que el broker traba de verdad (~$300 NU, ~$1200 C); consume el presupuesto.
    # Si no sabemos el margen (0), caemos conservador al notional.
    notional_pc = _collateral(order.strike, 1)
    collateral_pc = order.collateral_per_contract if order.collateral_per_contract > 0 else notional_pc
    if notional_pc <= 0 or collateral_pc <= 0:
        return GuardDecision(False, 0, 0.0, ["Strike/colateral inválido (≤0)."], limits.dry_run)

    # 2) Tope de notional (EXPOSICIÓN) por orden.
    if limits.max_notional_per_order > 0:
        fit = int(limits.max_notional_per_order // notional_pc)
        if fit < final:
            reasons.append(f"Recortado a {max(fit, 0)} por tope de notional ${limits.max_notional_per_order:,.0f}/orden.")
            final = fit

    # 3) Tope de capital total comprometido (MARGEN) en el día.
    if limits.max_total_deployed > 0:
        libre = limits.max_total_deployed - day.deployed_today
        fit = int(libre // collateral_pc) if libre > 0 else 0
        if fit < final:
            reasons.append(f"Recortado a {max(fit, 0)} por tope de colateral total del día ${limits.max_total_deployed:,.0f}.")
            final = fit

    # 4) Colchón de cash libre mínimo (contra el MARGEN que se traba).
    # AVISO DE TOPE MUERTO (auditoria 2026-08-22). `min_account_cash_buffer` aparenta ser una capa
    # activa del guardian, pero NO lo es: `live_engine` pasa siempre AccountSnapshot(cash=1e9), tanto
    # en real como en dry-run, porque no existe ninguna llamada que traiga el cash/buying power real
    # de Schwab. Con el valor en 0.0 es inocuo. El dia que alguien ponga 20000 creyendo que se
    # protege, el guardian lo aceptaria, lo mostraria como capa activa, y no frenaria ni un contrato.
    # Que grite hasta que se implemente la lectura del saldo real.
    if limits.min_account_cash_buffer > 0 and account.cash >= 1e8:
        logger.error(
            "min_account_cash_buffer=%.2f esta configurado pero NO se esta aplicando: el guardian no "
            "recibio el cash real de la cuenta (llego el valor de relleno). Ese tope no te protege.",
            limits.min_account_cash_buffer,
        )
    disponible = account.cash - limits.min_account_cash_buffer
    fit = int(disponible // collateral_pc) if disponible > 0 else 0
    if fit < final:
        reasons.append(f"Recortado a {max(fit, 0)} para dejar ${limits.min_account_cash_buffer:,.0f} de cash libre.")
        final = fit

    if final < 1:
        reasons.append("No entra ni 1 contrato respetando todos los topes → rechazada.")
        return GuardDecision(False, 0, 0.0, reasons, limits.dry_run)

    dec = GuardDecision(True, final, _collateral(order.strike, final), reasons, limits.dry_run)
    # INVARIANTE de seguridad: pase lo que pase, nunca más contratos que lo pedido.
    assert dec.final_contracts <= requested, "INVARIANTE VIOLADA: el guard agrandó la orden"
    assert dec.final_contracts >= 1
    return dec
