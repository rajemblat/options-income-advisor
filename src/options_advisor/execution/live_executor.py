"""Cerebro de la ejecución real: junta guardián + caminar-el-precio + armado de orden en UN plan.

Dado una OPORTUNIDAD que el robot quiere ejecutar (vender/recomprar un put), `plan_live_order`:
  1) calcula el paso adaptativo y el precio límite inicial (price_walker),
  2) pasa la orden por el GUARDIÁN (topes duros; nunca más contratos que lo pedido),
  3) si pasa, arma el payload de Schwab y lo describe.

En dry-run devuelve el plan completo SIN enviar nada — es lo que el usuario revisa antes de arriesgar.
El envío real (POST) y la reconciliación se enchufan después de este plan, con el usuario presente.
Todo testeable sin red."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from options_advisor.execution import live_guard as guard
from options_advisor.execution import price_walker as pw
from options_advisor.execution import schwab_orders as so
from options_advisor.execution.live_guard import (
    AccountSnapshot,
    DayState,
    IntendedOrder,
    LiveLimits,
)


@dataclass(frozen=True)
class Opportunity:
    """Lo que el robot decidió hacer, antes de tocar el guardián/broker."""
    symbol: str                 # raíz OCC (AAL, NU, SPY…)
    action: str                 # guard.ACTION_OPEN (vender) | guard.ACTION_CLOSE (recomprar)
    expiration: date
    strike: float
    requested_contracts: int
    bid: float
    ask: float
    underlying_price: float
    collateral_per_contract: float = 0.0   # margen real por contrato (0 = el guard usa strike×100)


@dataclass
class WalkConfig:
    # El paso depende del ANCHO del spread (usuario 2026-08-09): spread grande → $5/contrato (0.05) para
    # no gastar decenas de envíos; spread chico → $2/contrato (0.02). Corte en `wide_threshold`. Reemplaza
    # la orden cada `interval_seconds` (10 s).
    narrow_step: float = 0.02
    wide_step: float = 0.05
    wide_threshold: float = 0.50
    interval_seconds: int = 10
    stop_at_mid: bool = True
    # Piso DURO de precio al VENDER (usuario 2026-08-11: "no bajes de 3.00"): la orden nunca se coloca ni
    # se re-precia por debajo de esto, aunque el mid caiga. None = sin piso (comportamiento de siempre).
    price_floor: float | None = None

    def step_for(self, bid: float, ask: float) -> float:
        return pw.spread_based_step(bid, ask, narrow_step=self.narrow_step, wide_step=self.wide_step,
                                    wide_threshold=self.wide_threshold)


@dataclass
class LivePlan:
    approved: bool
    is_dry_run: bool
    final_contracts: int = 0
    start_limit_price: float | None = None
    price_ladder: list[float] = field(default_factory=list)
    order_payload: dict | None = None
    description: str = ""
    reasons: list[str] = field(default_factory=list)


def _side_for(action: str) -> str:
    return pw.SIDE_SELL if action == guard.ACTION_OPEN else pw.SIDE_BUY


def plan_live_order(
    opp: Opportunity,
    account: AccountSnapshot,
    limits: LiveLimits,
    day: DayState,
    armed: bool,
    walk: WalkConfig | None = None,
) -> LivePlan:
    """Arma el plan de ejecución de una oportunidad. Nunca envía nada."""
    walk = walk or WalkConfig()
    side = _side_for(opp.action)

    # Paso (fijo de 2 centavos por default) + precio límite inicial (extremo favorable) + escalera al mid.
    # El piso duro (`price_floor`) solo aplica al VENDER (abrir); al recomprar (cerrar) se ignora.
    floor = walk.price_floor if side == pw.SIDE_SELL else None
    step = walk.step_for(opp.bid, opp.ask)
    start_price = pw.next_limit_price(side, opp.bid, opp.ask, step=step, current=None,
                                      stop_at_mid=walk.stop_at_mid, price_floor=floor)
    ladder = pw.build_price_ladder(side, opp.bid, opp.ask, step=step, stop_at_mid=walk.stop_at_mid,
                                   price_floor=floor)

    intended = IntendedOrder(
        symbol=opp.symbol, action=opp.action, option_type="PUT", strike=opp.strike,
        expiration=opp.expiration.isoformat(), requested_contracts=opp.requested_contracts,
        limit_price=start_price, underlying_price=opp.underlying_price,
        collateral_per_contract=opp.collateral_per_contract,
    )
    decision = guard.evaluate(intended, account, limits, day, armed)
    if not decision.approved:
        return LivePlan(approved=False, is_dry_run=limits.dry_run, reasons=decision.reasons,
                        description="RECHAZADA por el guardián: " + "; ".join(decision.reasons))

    if opp.action == guard.ACTION_OPEN:
        payload = so.build_sell_put_to_open(opp.symbol, opp.expiration, opp.strike,
                                            decision.final_contracts, start_price)
    else:
        payload = so.build_buy_put_to_close(opp.symbol, opp.expiration, opp.strike,
                                            decision.final_contracts, start_price)

    return LivePlan(
        approved=True, is_dry_run=limits.dry_run, final_contracts=decision.final_contracts,
        start_limit_price=start_price, price_ladder=ladder, order_payload=payload,
        description=so.describe_order(payload), reasons=decision.reasons,
    )
