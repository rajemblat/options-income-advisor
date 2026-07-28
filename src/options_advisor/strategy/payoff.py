from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import log, sqrt
from statistics import NormalDist

from py_vollib.black_scholes_merton import black_scholes_merton

from options_advisor.broker.models import OptionContract
from options_advisor.strategy import constants as c
from options_advisor.strategy.candidates import CandidateBuild, Leg

_NORMAL = NormalDist()
_CONTRACT_MULTIPLIER = 100
_UNBOUNDED_SLOPE_EPS = 1e-9

# Estrategias con vencimientos combinados (calendar/diagonal): no hay fórmula cerrada para
# beneficio máximo/breakeven, se estima proyectando la pata lejana con Black-Scholes sobre
# una grilla de precios al vencimiento de la pata cercana (mismo patrón de "aproximación
# documentada" que ya usa el bootstrap de IV Rank).
_GRID_POINTS = 400
_GRID_RANGE = (0.4, 1.8)  # múltiplos del precio actual del subyacente

_STOCK_INCLUDED_STRATEGIES = {c.COVERED_CALL, c.COLLAR}


@dataclass
class PayoffResult:
    legs: list[dict]
    net_premium: float  # positivo = crédito recibido, negativo = débito pagado
    max_profit: float | None  # None = beneficio no acotado
    max_loss: float | None  # siempre positivo si está acotado; None = pérdida no acotada
    breakevens: list[float] = field(default_factory=list)
    probability_of_profit: float | None = None
    dte: int = 0
    underlying_price: float = 0.0
    is_estimate: bool = False
    annualized_return_pct: float | None = None
    early_close_projection: list[dict] = field(default_factory=list)


def _leg_dict(leg: Leg) -> dict:
    ct = leg.contract
    return {
        "side": leg.side,
        "quantity": leg.quantity,
        "option_type": ct.option_type,
        "strike": ct.strike,
        "expiration": ct.expiration.isoformat(),
        "premium": ct.mid_price,
        "implied_volatility": ct.implied_volatility,
        # bid/ask crudos (no solo el mid_price ya usado para "premium") — hacen falta para
        # advertir sobre spreads anchos, ver alerts/formatting.py::assess_liquidity.
        "bid": ct.bid,
        "ask": ct.ask,
        # Sumados 2026-07-27 para la vista tabla de Escaneo (columnas Volume/Open Interest) —
        # ya venían del broker en OptionContract, solo faltaba copiarlos acá para que
        # sobrevivan la persistencia en candidate_contracts.legs_json.
        "open_interest": ct.open_interest,
        "volume": ct.volume,
    }


def _prob_above(underlying_price: float, target: float, years: float, risk_free_rate: float, sigma: float) -> float:
    """Probabilidad risk-neutral (Black-Scholes) de que el subyacente termine por encima de
    `target` al cabo de `years`. P(S_T > K) = N(d2) — vale para calls y puts por igual, es una
    propiedad de la distribución lognormal del precio, no del tipo de contrato."""
    if years <= 0 or sigma <= 0 or target <= 0:
        return 1.0 if underlying_price > target else 0.0
    d1 = (log(underlying_price / target) + (risk_free_rate + 0.5 * sigma**2) * years) / (sigma * sqrt(years))
    d2 = d1 - sigma * sqrt(years)
    return _NORMAL.cdf(d2)


def _prob_below(underlying_price: float, target: float, years: float, risk_free_rate: float, sigma: float) -> float:
    return 1 - _prob_above(underlying_price, target, years, risk_free_rate, sigma)


def probability_otm(
    option_type: str, underlying_price: float, strike: float, dte: int, risk_free_rate: float, sigma: float
) -> float:
    """Probabilidad de que el precio termine del lado OTM del STRIKE al vencimiento — DISTINTA
    de `probability_of_profit` (que mide contra el BREAKEVEN, ya con el colchón de la prima
    cobrada incluido). Para un put vendido, probability_otm < probability_of_profit siempre
    (el breakeven está más abajo que el strike). Sección 'Pestaña Screener' (pedido 2026-07-27,
    columna "Probabilidad OTM" — corrección a la auditoría del pedido original, que asumía que
    esto era lo mismo que el POP ya calculado). Misma maquinaria Black-Scholes que ya usa POP
    (`_prob_above`/`_prob_below`), expuesta acá como función pública porque, a diferencia de
    POP (que necesita conocer TODAS las patas de la estrategia para ubicar el breakeven), esto
    es una probabilidad de un solo punto (el strike), calculable con los datos que ya vienen en
    cada leg persistido — no depende de compute_payoff."""
    years = dte / 365
    # Un put vendido queda OTM (expira sin valor) si el precio final queda POR ENCIMA del
    # strike; una call vendida queda OTM si queda POR DEBAJO — lo inverso de lo que "protege"
    # cada una (mismo sentido que _intrinsic: put paga max(K-S,0), call paga max(S-K,0)).
    if option_type == "put":
        return _prob_above(underlying_price, strike, years, risk_free_rate, sigma)
    return _prob_below(underlying_price, strike, years, risk_free_rate, sigma)


# ---------------------------------------------------------------------------
# Motor genérico: cualquier combinación de patas de LA MISMA expiración (spreads, condors,
# ratios/backspreads, y el short/covered-call/collar con su pata implícita de acciones) tiene
# un payoff a vencimiento lineal a tramos, con quiebres únicamente en cada strike. Evaluar el
# P&L en S=0 y en cada strike alcanza para obtener máximo, mínimo y breakevens EXACTOS (no una
# aproximación), incluyendo detectar beneficio/pérdida no acotada por la pendiente más allá
# del strike más extremo (solo las calls aportan pendiente ahí; los puts valen 0 por encima de
# su propio strike). Esto reemplaza fórmulas cerradas por estrategia, que son fáciles de
# transcribir mal justo en las estrategias más inusuales (ratios, condors).
# ---------------------------------------------------------------------------


def _intrinsic(option_type: str, strike: float, price: float) -> float:
    if option_type == "call":
        return max(0.0, price - strike)
    return max(0.0, strike - price)


def _net_premium(legs: list[Leg]) -> float:
    total = 0.0
    for leg in legs:
        amount = leg.quantity * leg.contract.mid_price * _CONTRACT_MULTIPLIER
        total += amount if leg.side == "sell" else -amount
    return total


def _pnl_at(legs: list[Leg], net_premium: float, price: float, include_stock: bool, underlying_price: float) -> float:
    total = net_premium
    if include_stock:
        total += (price - underlying_price) * _CONTRACT_MULTIPLIER
    for leg in legs:
        sign = 1 if leg.side == "buy" else -1
        total += sign * leg.quantity * _CONTRACT_MULTIPLIER * _intrinsic(leg.contract.option_type, leg.contract.strike, price)
    return total


def _slope_above_max_strike(legs: list[Leg], include_stock: bool) -> float:
    """Pendiente del payoff (en $ por $1 de movimiento del subyacente) más allá del strike más
    alto entre las patas: ahí, solo las calls (y la acción, si corresponde) siguen aportando
    pendiente — los puts ya valen 0 por encima de su propio strike."""
    slope = _CONTRACT_MULTIPLIER * sum(
        (1 if leg.side == "buy" else -1) * leg.quantity for leg in legs if leg.contract.option_type == "call"
    )
    if include_stock:
        slope += _CONTRACT_MULTIPLIER
    return slope


def _representative_sigma(legs: list[Leg], target_price: float) -> float:
    """IV de la pata cuyo strike está más cerca de `target_price` — proxy razonable para la
    probabilidad en ese punto (mismo criterio que ya usaba Iron Condor: la IV de la pata corta
    relevante a cada breakeven, generalizado a N patas)."""
    nearest = min(legs, key=lambda leg: abs(leg.contract.strike - target_price))
    return nearest.contract.implied_volatility


def _probability_of_profit(
    legs: list[Leg],
    net_premium: float,
    include_stock: bool,
    underlying_price: float,
    breakevens: list[float],
    slope_above_max: float,
    dte: int,
    risk_free_rate: float,
) -> float:
    years = dte / 365
    if not breakevens:
        sample = _pnl_at(legs, net_premium, underlying_price, include_stock, underlying_price)
        return 1.0 if sample >= 0 else 0.0

    bounds = [0.0] + sorted(breakevens)
    pop = 0.0
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        mid = (lo + hi) / 2
        if _pnl_at(legs, net_premium, mid, include_stock, underlying_price) >= 0:
            sigma = _representative_sigma(legs, mid)
            pop += _prob_below(underlying_price, hi, years, risk_free_rate, sigma) - _prob_below(
                underlying_price, lo, years, risk_free_rate, sigma
            )

    last = bounds[-1]
    probe_price = last * 1.5 + 1.0
    if _pnl_at(legs, net_premium, probe_price, include_stock, underlying_price) >= 0:
        sigma = _representative_sigma(legs, last)
        pop += _prob_above(underlying_price, last, years, risk_free_rate, sigma)

    return max(0.0, min(1.0, round(pop, 4)))


def annualized_return_pct(max_profit: float | None, max_loss: float | None, dte: int) -> float | None:
    """(beneficio_máximo / riesgo_máximo) * (365/DTE) * 100 — pedido explícito del usuario
    2026-07-24 para comparar eficiencia de capital entre estrategias, no solo el crédito
    nominal. None si el riesgo no está acotado (max_loss=inf, no hay "capital en riesgo"
    finito con el que dividir), es 0, o si DTE es 0 (división por cero)."""
    if max_profit is None or max_loss is None or max_loss in (0, float("inf")) or dte <= 0:
        return None
    return round((max_profit / max_loss) * (365 / dte) * 100, 2)


_EARLY_CLOSE_TARGETS = (0.30, 0.50, 1.00)


def _reprice_leg(contract: OptionContract, years_remaining: float, underlying_price: float, risk_free_rate: float) -> float:
    if years_remaining <= 0:
        return _intrinsic(contract.option_type, contract.strike, underlying_price)
    flag = "c" if contract.option_type == "call" else "p"
    return black_scholes_merton(flag, underlying_price, contract.strike, years_remaining, risk_free_rate, contract.implied_volatility, 0.0)


def _pnl_at_days_elapsed(legs: list[Leg], net_premium: float, underlying_price: float, dte: int, days_elapsed: int, risk_free_rate: float) -> float:
    """P&L de la posición si pasaron `days_elapsed` días desde hoy, manteniendo precio del
    subyacente e IV de cada pata constantes ('si nada cambia', mismo criterio que
    dashboard/portfolio_analysis.py) — repriceando cada pata con Black-Scholes al tiempo
    restante en vez de usar solo valor intrínseco (que asumiría que ya venció)."""
    years_remaining = max(dte - days_elapsed, 0) / 365
    total = net_premium
    for leg in legs:
        sign = 1 if leg.side == "buy" else -1
        value = _reprice_leg(leg.contract, years_remaining, underlying_price, risk_free_rate)
        total += sign * leg.quantity * _CONTRACT_MULTIPLIER * value
    return total


def early_close_projection(
    legs: list[Leg], net_premium: float, underlying_price: float, dte: int, risk_free_rate: float, max_profit: float | None
) -> list[dict]:
    """Días aproximados para alcanzar 30%/50%/100% del beneficio máximo si se cerrara la
    posición antes del vencimiento (pedido explícito del usuario 2026-07-24). SIMPLIFICACIÓN
    real y documentada: asume que el precio del subyacente y la IV de cada pata NO cambian
    desde hoy — solo modela el decaimiento de tiempo (theta) puro, ignorando movimiento de
    precio (delta/gamma) y cambios de IV (vega), los dos factores que más mueven el P&L día a
    día en la práctica. Es un orden de magnitud de cuánto tarda el decaimiento puro en llegar a
    cada nivel, no una fecha garantizada — mismo espíritu que `payoff_is_estimate` en otras
    estrategias, pero acá aplica siempre que hay beneficio máximo finito, no solo en
    calendars/diagonals.

    Un solo escaneo día por día (no búsqueda binaria: el decaimiento no es necesariamente
    monótono para todas las combinaciones de patas posibles, más seguro no asumirlo) revisando
    los 3 objetivos a la vez, en vez de un escaneo separado por objetivo. `days` queda en None
    si ese nivel no se alcanza dentro del DTE bajo este escenario (el precio actual no cae en
    la zona de beneficio máximo, por ejemplo)."""
    if max_profit is None or max_profit <= 0 or dte <= 0:
        return []
    targets = {pct: max_profit * pct for pct in _EARLY_CLOSE_TARGETS}
    days_needed: dict[float, int | None] = dict.fromkeys(_EARLY_CLOSE_TARGETS)
    for day in range(dte + 1):
        pending = [pct for pct, needed in days_needed.items() if needed is None]
        if not pending:
            break
        pnl = _pnl_at_days_elapsed(legs, net_premium, underlying_price, dte, day, risk_free_rate)
        for pct in pending:
            if pnl >= targets[pct]:
                days_needed[pct] = day
    return [{"pct": round(pct * 100), "days": days_needed[pct]} for pct in _EARLY_CLOSE_TARGETS]


def _generic_multileg_payoff(
    legs: list[Leg], underlying_price: float, dte: int, risk_free_rate: float, include_stock: bool = False
) -> PayoffResult:
    net_premium = _net_premium(legs)
    strikes = sorted({leg.contract.strike for leg in legs})
    eval_points = [0.0] + strikes
    pnls = [_pnl_at(legs, net_premium, p, include_stock, underlying_price) for p in eval_points]

    slope_above_max = _slope_above_max_strike(legs, include_stock)
    # inf (no None) para "no acotado": None está reservado para "sin dato" en llamadores que
    # no pasan payoff (ej. tests viejos del narrador) — son dos cosas distintas para mostrar.
    max_profit = float("inf") if slope_above_max > _UNBOUNDED_SLOPE_EPS else round(max(pnls), 2)
    max_loss = float("inf") if slope_above_max < -_UNBOUNDED_SLOPE_EPS else round(max(0.0, -min(pnls)), 2)

    breakevens: list[float] = []
    for i in range(len(pnls) - 1):
        lo_val, hi_val = pnls[i], pnls[i + 1]
        if lo_val == 0:
            breakevens.append(round(eval_points[i], 2))
        elif (lo_val < 0) != (hi_val < 0):
            frac = -lo_val / (hi_val - lo_val)
            breakevens.append(round(eval_points[i] + frac * (eval_points[i + 1] - eval_points[i]), 2))
    if pnls[-1] == 0:
        breakevens.append(round(eval_points[-1], 2))
    elif abs(slope_above_max) > _UNBOUNDED_SLOPE_EPS and (pnls[-1] < 0) != (slope_above_max < 0):
        breakevens.append(round(eval_points[-1] + (-pnls[-1]) / slope_above_max, 2))

    pop = _probability_of_profit(legs, net_premium, include_stock, underlying_price, breakevens, slope_above_max, dte, risk_free_rate)

    return PayoffResult(
        legs=[_leg_dict(leg) for leg in legs],
        net_premium=round(net_premium, 2),
        max_profit=max_profit,
        max_loss=max_loss,
        breakevens=breakevens,
        probability_of_profit=round(pop, 4),
        dte=dte,
        underlying_price=underlying_price,
        annualized_return_pct=annualized_return_pct(max_profit, max_loss, dte),
        early_close_projection=early_close_projection(legs, net_premium, underlying_price, dte, risk_free_rate, max_profit),
    )


# ---------------------------------------------------------------------------
# Calendar/diagonal: vencimientos combinados, no aplica el motor genérico de arriba (que
# asume una sola expiración). Se mantiene la proyección numérica con Black-Scholes.
# ---------------------------------------------------------------------------


def _payoff_calendar(legs: list[Leg], underlying_price: float, as_of: date, risk_free_rate: float) -> PayoffResult:
    short_leg = next(leg for leg in legs if leg.side == "sell")
    long_leg = next(leg for leg in legs if leg.side == "buy")
    near, far = short_leg.contract, long_leg.contract
    option_type = near.option_type
    bs_flag = "c" if option_type == "call" else "p"
    # DTE relevante para esta estrategia es el de la pata corta (near): es el primer
    # punto de decisión/gestión de la posición, no el vencimiento de la pata larga.
    dte = (near.expiration - as_of).days

    near_credit = near.mid_price
    far_debit = far.mid_price
    net_premium = (near_credit - far_debit) * _CONTRACT_MULTIPLIER

    t_gap = max((far.expiration - near.expiration).days, 1) / 365
    far_sigma = far.implied_volatility

    def pnl_at(price: float) -> float:
        near_payoff = near_credit - max(0.0, _intrinsic(option_type, near.strike, price))
        far_value = black_scholes_merton(bs_flag, max(price, 0.01), far.strike, t_gap, risk_free_rate, far_sigma, 0.0)
        far_payoff = far_value - far_debit
        return (near_payoff + far_payoff) * _CONTRACT_MULTIPLIER

    lo, hi = underlying_price * _GRID_RANGE[0], underlying_price * _GRID_RANGE[1]
    step = (hi - lo) / _GRID_POINTS
    prices = [lo + i * step for i in range(_GRID_POINTS + 1)]
    pnls = [pnl_at(p) for p in prices]

    max_profit = max(max(pnls), 0.0)
    max_loss = abs(min(min(pnls), 0.0))

    breakevens: list[float] = []
    for i in range(len(pnls) - 1):
        if pnls[i] == 0:
            breakevens.append(round(prices[i], 2))
        elif (pnls[i] < 0) != (pnls[i + 1] < 0):
            frac = -pnls[i] / (pnls[i + 1] - pnls[i])
            breakevens.append(round(prices[i] + frac * step, 2))

    years_near = dte / 365
    pop: float | None = None
    if len(breakevens) >= 2:
        p_below = _prob_below(underlying_price, breakevens[0], years_near, risk_free_rate, near.implied_volatility)
        p_above = _prob_above(underlying_price, breakevens[-1], years_near, risk_free_rate, near.implied_volatility)
        pop = max(0.0, 1 - p_below - p_above)
    elif len(breakevens) == 1:
        profitable_at_center = pnl_at(near.strike) >= 0
        pop = (
            _prob_above(underlying_price, breakevens[0], years_near, risk_free_rate, near.implied_volatility)
            if profitable_at_center
            else _prob_below(underlying_price, breakevens[0], years_near, risk_free_rate, near.implied_volatility)
        )

    return PayoffResult(
        legs=[_leg_dict(short_leg), _leg_dict(long_leg)],
        net_premium=round(net_premium, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        breakevens=breakevens,
        probability_of_profit=round(pop, 4) if pop is not None else None,
        dte=dte,
        underlying_price=underlying_price,
        is_estimate=True,
    )


def compute_payoff(build: CandidateBuild, underlying_price: float, as_of: date, risk_free_rate: float) -> PayoffResult:
    """Calcula precio de cada pata, crédito/débito neto, beneficio máximo, pérdida máxima,
    breakeven(s) y probabilidad de beneficio para un candidato ya armado por `candidates.py`.
    Determinístico: nunca lo decide el LLM."""
    strategy = build.strategy_type
    if strategy not in c.ALL_INCOME_STRATEGIES:
        raise ValueError(f"Estrategia desconocida para el cálculo de payoff: {strategy}")

    if strategy in (
        c.CALENDAR_PUT_SPREAD,
        c.CALENDAR_CALL_SPREAD,
        c.DIAGONAL_PUT_SPREAD,
        c.DIAGONAL_CALL_SPREAD,
    ):
        return _payoff_calendar(build.legs, underlying_price, as_of, risk_free_rate)

    dte = (build.expiration_date - as_of).days
    include_stock = strategy in _STOCK_INCLUDED_STRATEGIES
    return _generic_multileg_payoff(build.legs, underlying_price, dte, risk_free_rate, include_stock=include_stock)
