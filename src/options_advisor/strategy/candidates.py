from __future__ import annotations

from datetime import date
from typing import NamedTuple

from options_advisor.broker.models import OptionChain, OptionContract, OptionType
from options_advisor.strategy import constants as c

TARGET_SHORT_DELTA = 0.25  # convención estándar de venta de prima: ~25 delta (~75-80% prob. OTM)
CALENDAR_SHORT_DELTA = 0.35  # pata corta de calendar/diagonal, más cercana al dinero
DEBIT_LONG_DELTA = 0.50  # pata comprada de un debit spread: cerca del dinero
RATIO_BACKSPREAD_SHORT_DELTA = 0.35  # única pata vendida del backspread (1x), cerca del dinero
RATIO_FRONT_LONG_DELTA = 0.40  # única pata comprada del ratio front spread (1x), cerca del dinero
CONDOR_TARGET_DELTAS = (0.65, 0.50, 0.30, 0.15)  # 4 strikes del condor de un solo tipo de opción
NEAR_LEG_DTE_RANGE = (20, 30)
FAR_LEG_DTE_RANGE = (45, 60)
SINGLE_LEG_DTE_RANGE = (25, 50)


class Leg(NamedTuple):
    side: str  # "sell" | "buy"
    contract: OptionContract
    quantity: int = 1
    # Precio REAL de ejecución de esta pata (por acción, ej. 3.15) — solo para operaciones
    # reales YA ejecutadas (ver build_from_contract/alerts/real_trades.py). Cuando está seteado,
    # reemplaza `contract.mid_price` para el cálculo de prima/P&L (bug real encontrado
    # 2026-07-28: usaba el mark price ACTUAL de la cadena en vivo en vez del fill real del
    # usuario, dando breakeven/prima/riesgo máximo incorrectos). `contract.bid`/`ask` NO se
    # tocan — siguen siendo el spread real de mercado, correcto para advertir liquidez
    # (assess_liquidity), que es una pregunta distinta ("¿es operable ahora?", no "¿qué pagué?").
    override_premium: float | None = None


class CandidateBuild(NamedTuple):
    strategy_type: str
    expiration_date: date
    strikes: dict
    net_greeks: dict
    greeks_source: str
    legs: list[Leg]


def _contracts_for(chain: OptionChain, option_type: OptionType, expiration: date) -> list[OptionContract]:
    return sorted(
        (ct for ct in chain.contracts if ct.option_type == option_type and ct.expiration == expiration),
        key=lambda ct: ct.strike,
    )


def _pick_by_target_delta(contracts: list[OptionContract], target_delta: float) -> OptionContract | None:
    if not contracts:
        return None
    return min(contracts, key=lambda ct: abs(abs(ct.greeks.delta) - target_delta))


def _coverage_pct(option_type: OptionType, strike: float, underlying_price: float) -> float:
    """Mismo cálculo que alerts/formatting.py::compute_coverage, duplicado acá a propósito:
    ahí es un dato informativo post-hoc sobre una alerta ya armada; acá es un FILTRO que decide
    qué strikes son elegibles antes de armar el candidato — importar entre módulos de capas
    distintas (alertas → estrategia) invertiría la dependencia."""
    if option_type == "put":
        return (underlying_price - strike) / underlying_price
    return (strike - underlying_price) / underlying_price


def _has_good_support(option_type: OptionType, strike: float, underlying_price: float, support_sma_values: list[float | None]) -> bool:
    """"Buen soporte" (Sección 'perfil de riesgo' refinado, 2026-07-24): al menos UNA de las
    SMA de referencia del perfil debe actuar de piso real para un put vendido — el precio
    sigue por ENCIMA de la media (todavía no se rompió) y el strike vendido queda POR DEBAJO o
    igual a esa media (la media queda entre precio y strike, colchón técnico adicional al del
    delta). Para una call vendida es el espejo: la media actúa de techo (precio por debajo,
    strike por encima). `support_sma_values` ya viene resuelto por el caller (ver
    alerts/engine.py) a los valores concretos de las SMA que el perfil acepta — conservador
    solo SMA8, normal/agresivo SMA8 o SMA20 (cualquiera de las dos alcanza).

    Lista vacía o todo None = no hay ninguna SMA para evaluar (caller no pasó requisito de
    soporte — estrategias fuera del MVP —, o hay muy poco historial de precio todavía para
    calcular ninguna SMA) → el chequeo se considera "no aplicable" y pasa, no se descarta el
    candidato por falta de dato (mismo criterio que el resto del motor: dato faltante relaja
    el filtro, no lo hace fallar)."""
    values = [sma for sma in support_sma_values if sma is not None]
    if not values:
        return True
    for sma in values:
        if option_type == "put":
            if underlying_price > sma and strike <= sma:
                return True
        else:
            if underlying_price < sma and strike >= sma:
                return True
    return False


def _pick_short_leg(
    contracts: list[OptionContract],
    target_delta: float,
    option_type: OptionType,
    underlying_price: float | None,
    min_coverage_pct: float,
    support_sma_values: list[float | None],
) -> OptionContract | None:
    """Elige la pata VENDIDA: arranca del mismo criterio de siempre (strike más cercano al
    delta objetivo) pero restringido a los strikes que cumplen el mínimo de cobertura del
    perfil Y tienen buen soporte técnico — si el strike del delta objetivo no alcanza ninguno
    de los dos, se sigue buscando más OTM hasta encontrar uno que sí cumpla ambos (pedido
    explícito del usuario 2026-07-24: nunca descartar el candidato por esto, ajustar el
    strike). Sin `underlying_price` (no debería pasar en producción, pero algunos tests viejos
    arman contratos a mano) cae al criterio de siempre, sin filtro, porque no hay con qué
    evaluar cobertura/soporte. Sin ninguna restricción real pedida (min_coverage_pct<=0 Y sin
    ninguna SMA) también cae al criterio de siempre — mantiene el comportamiento exacto de
    antes de este refinamiento para las estrategias fuera del MVP, que no pasan estos
    parámetros (Sección 'perfil de riesgo' refinado, 2026-07-24). None si NINGÚN strike de la
    cadena cumple ambos requisitos cuando sí hay una restricción real pedida."""
    if not contracts:
        return None
    no_real_constraint = min_coverage_pct <= 0.0 and not any(v is not None for v in support_sma_values)
    if underlying_price is None or no_real_constraint:
        return _pick_by_target_delta(contracts, target_delta)

    eligible = [
        ct
        for ct in contracts
        if _coverage_pct(option_type, ct.strike, underlying_price) >= min_coverage_pct
        and _has_good_support(option_type, ct.strike, underlying_price, support_sma_values)
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda ct: abs(abs(ct.greeks.delta) - target_delta))


def _pick_further_otm(contracts: list[OptionContract], reference: OptionContract, option_type: OptionType) -> OptionContract | None:
    """Devuelve el contrato inmediatamente más OTM que `reference` (strike más bajo para puts,
    más alto para calls). Genérico sobre qué pata es la de referencia — sirve tanto para
    "vendo cerca, compro más lejos" (credit spread) como "compro cerca, vendo más lejos"
    (debit spread / ratio), la dirección de "más lejos" depende solo del tipo de opción."""
    idx = contracts.index(reference)
    if option_type == "put":
        lower = contracts[:idx]
        return lower[-1] if lower else None
    higher = contracts[idx + 1 :]
    return higher[0] if higher else None


def _position_greeks(contract: OptionContract, is_short: bool, quantity: int = 1) -> dict:
    sign = -1 if is_short else 1
    g = contract.greeks
    return {
        "delta": sign * quantity * g.delta,
        "gamma": sign * quantity * g.gamma,
        "theta": sign * quantity * g.theta,
        "vega": sign * quantity * g.vega,
        "rho": sign * quantity * g.rho,
    }


def _sum_greeks(*legs: dict) -> dict:
    keys = ("delta", "gamma", "theta", "vega", "rho")
    return {k: round(sum(leg[k] for leg in legs), 4) for k in keys}


def _greeks_source(*contracts: OptionContract) -> str:
    return "broker" if all(ct.greeks.source == "broker" for ct in contracts) else "calculated"


def _nearest_expiration_or_none(chain: OptionChain, dte_range: tuple[int, int]) -> date | None:
    try:
        return chain.nearest_expiration(*dte_range)
    except ValueError:
        return None


def _build_single_short_leg(
    strategy_type: str,
    chain: OptionChain,
    option_type: OptionType,
    target_short_delta: float,
    min_coverage_pct: float = 0.0,
    support_sma_values: list[float | None] | None = None,
) -> CandidateBuild | None:
    expiration = _nearest_expiration_or_none(chain, SINGLE_LEG_DTE_RANGE)
    if expiration is None:
        return None
    contracts = _contracts_for(chain, option_type, expiration)
    short = _pick_short_leg(contracts, target_short_delta, option_type, chain.underlying_price, min_coverage_pct, support_sma_values or [])
    if short is None:
        return None
    return CandidateBuild(
        strategy_type=strategy_type,
        expiration_date=expiration,
        strikes={"short_strike": short.strike},
        net_greeks=_position_greeks(short, is_short=True),
        greeks_source=_greeks_source(short),
        legs=[Leg("sell", short)],
    )


def _build_vertical_spread(
    strategy_type: str,
    chain: OptionChain,
    option_type: OptionType,
    target_short_delta: float = TARGET_SHORT_DELTA,
    min_coverage_pct: float = 0.0,
    support_sma_values: list[float | None] | None = None,
) -> CandidateBuild | None:
    """Credit spread: vende la pata cercana al dinero (target delta), compra la pata más OTM
    como cobertura. Sirve tanto para Bull Put Spread (puts) como Bear Call Spread (calls) —
    esas dos NO pasan min_coverage_pct/support_sma_values (fuera del MVP, sin cambios de
    comportamiento); Iron Condor sí, para su pata de puts."""
    expiration = _nearest_expiration_or_none(chain, SINGLE_LEG_DTE_RANGE)
    if expiration is None:
        return None
    contracts = _contracts_for(chain, option_type, expiration)
    short = _pick_short_leg(contracts, target_short_delta, option_type, chain.underlying_price, min_coverage_pct, support_sma_values or [])
    if short is None:
        return None
    long = _pick_further_otm(contracts, short, option_type)
    if long is None:
        return None
    return CandidateBuild(
        strategy_type=strategy_type,
        expiration_date=expiration,
        strikes={"short_strike": short.strike, "long_strike": long.strike},
        net_greeks=_sum_greeks(_position_greeks(short, True), _position_greeks(long, False)),
        greeks_source=_greeks_source(short, long),
        legs=[Leg("sell", short), Leg("buy", long)],
    )


def _build_debit_vertical_spread(strategy_type: str, chain: OptionChain, option_type: OptionType) -> CandidateBuild | None:
    """Debit spread: compra la pata cercana al dinero (mayor delta), vende la pata más OTM
    para reducir el costo. Bull Call Spread (calls) / Bear Put Spread (puts)."""
    expiration = _nearest_expiration_or_none(chain, SINGLE_LEG_DTE_RANGE)
    if expiration is None:
        return None
    contracts = _contracts_for(chain, option_type, expiration)
    long = _pick_by_target_delta(contracts, DEBIT_LONG_DELTA)
    if long is None:
        return None
    short = _pick_further_otm(contracts, long, option_type)
    if short is None:
        return None
    return CandidateBuild(
        strategy_type=strategy_type,
        expiration_date=expiration,
        strikes={"long_strike": long.strike, "short_strike": short.strike},
        net_greeks=_sum_greeks(_position_greeks(long, False), _position_greeks(short, True)),
        greeks_source=_greeks_source(long, short),
        legs=[Leg("buy", long), Leg("sell", short)],
    )


def _build_collar(
    chain: OptionChain,
    target_short_delta: float = TARGET_SHORT_DELTA,
    min_coverage_pct: float = 0.0,
    support_sma_values: list[float | None] | None = None,
) -> CandidateBuild | None:
    """Vende un call OTM (ingreso) + compra un put OTM (protección) sobre 100 acciones ya en
    cartera, misma expiración. Deltas objetivo simétricas (simplificación documentada — un
    collar real suele ajustar cada pata para calzar costo, acá se prioriza consistencia con
    el resto del motor). min_coverage_pct/support_sma_values solo restringen la call VENDIDA
    (resistencia como techo) — el put comprado es protección, no le aplica el filtro."""
    expiration = _nearest_expiration_or_none(chain, SINGLE_LEG_DTE_RANGE)
    if expiration is None:
        return None
    short_call = _pick_short_leg(
        _contracts_for(chain, "call", expiration), target_short_delta, "call", chain.underlying_price, min_coverage_pct, support_sma_values or []
    )
    long_put = _pick_by_target_delta(_contracts_for(chain, "put", expiration), target_short_delta)
    if short_call is None or long_put is None:
        return None
    return CandidateBuild(
        strategy_type=c.COLLAR,
        expiration_date=expiration,
        strikes={"call_strike": short_call.strike, "put_strike": long_put.strike},
        net_greeks=_sum_greeks(_position_greeks(short_call, True), _position_greeks(long_put, False)),
        greeks_source=_greeks_source(short_call, long_put),
        legs=[Leg("sell", short_call), Leg("buy", long_put)],
    )


def _build_ratio_backspread(strategy_type: str, chain: OptionChain, option_type: OptionType) -> CandidateBuild | None:
    """1x2: vende 1 pata cerca del dinero, compra 2 patas más OTM — neto largo, beneficio no
    acotado hacia el lado de las patas compradas."""
    expiration = _nearest_expiration_or_none(chain, SINGLE_LEG_DTE_RANGE)
    if expiration is None:
        return None
    contracts = _contracts_for(chain, option_type, expiration)
    short = _pick_by_target_delta(contracts, RATIO_BACKSPREAD_SHORT_DELTA)
    if short is None:
        return None
    long = _pick_further_otm(contracts, short, option_type)
    if long is None:
        return None
    return CandidateBuild(
        strategy_type=strategy_type,
        expiration_date=expiration,
        strikes={"short_strike": short.strike, "long_strike": long.strike},
        net_greeks=_sum_greeks(_position_greeks(short, True, 1), _position_greeks(long, False, 2)),
        greeks_source=_greeks_source(short, long),
        legs=[Leg("sell", short, 1), Leg("buy", long, 2)],
    )


def _build_ratio_front_spread(strategy_type: str, chain: OptionChain, option_type: OptionType) -> CandidateBuild | None:
    """2x1: compra 1 pata cerca del dinero, vende 2 patas más OTM — neto corto, riesgo grande
    o no acotado del lado de las patas vendidas (ver strategy/profiles.py, restringido a
    perfil agresivo)."""
    expiration = _nearest_expiration_or_none(chain, SINGLE_LEG_DTE_RANGE)
    if expiration is None:
        return None
    contracts = _contracts_for(chain, option_type, expiration)
    long = _pick_by_target_delta(contracts, RATIO_FRONT_LONG_DELTA)
    if long is None:
        return None
    short = _pick_further_otm(contracts, long, option_type)
    if short is None:
        return None
    return CandidateBuild(
        strategy_type=strategy_type,
        expiration_date=expiration,
        strikes={"long_strike": long.strike, "short_strike": short.strike},
        net_greeks=_sum_greeks(_position_greeks(long, False, 1), _position_greeks(short, True, 2)),
        greeks_source=_greeks_source(long, short),
        legs=[Leg("buy", long, 1), Leg("sell", short, 2)],
    )


def _build_short_condor(strategy_type: str, chain: OptionChain, option_type: OptionType) -> CandidateBuild | None:
    """Short Call/Put Condor: 4 strikes de un solo tipo de opción (K1<K2<K3<K4), elegidos por
    delta objetivo y ordenados por strike — vende-compra-compra-vende. Mismo perfil de
    beneficio que el Iron Condor (rango central de ganancia) pero construido con un solo tipo
    de contrato en vez de mezclar puts y calls."""
    expiration = _nearest_expiration_or_none(chain, SINGLE_LEG_DTE_RANGE)
    if expiration is None:
        return None
    contracts = _contracts_for(chain, option_type, expiration)
    picked: list[OptionContract] = []
    seen_strikes: set[float] = set()
    for target in CONDOR_TARGET_DELTAS:
        ct = _pick_by_target_delta(contracts, target)
        if ct is None or ct.strike in seen_strikes:
            return None  # cadena sin suficientes strikes distintos para armar el condor
        picked.append(ct)
        seen_strikes.add(ct.strike)
    picked.sort(key=lambda ct: ct.strike)
    sides = ["sell", "buy", "buy", "sell"]
    legs = [Leg(side, ct) for side, ct in zip(sides, picked)]
    return CandidateBuild(
        strategy_type=strategy_type,
        expiration_date=expiration,
        strikes={f"strike_{i + 1}": ct.strike for i, ct in enumerate(picked)},
        net_greeks=_sum_greeks(*(_position_greeks(ct, side == "sell") for side, ct in zip(sides, picked))),
        greeks_source=_greeks_source(*picked),
        legs=legs,
    )


def _build_iron_condor(
    chain: OptionChain,
    target_short_delta: float = TARGET_SHORT_DELTA,
    min_coverage_pct: float = 0.0,
    support_sma_values: list[float | None] | None = None,
) -> CandidateBuild | None:
    """Ambas patas vendidas (put y call) pasan por el filtro de cobertura/soporte — Iron Condor
    tiene cobertura a la baja Y al alza, cada una evaluada independiente contra la SMA de
    referencia como piso/techo respectivamente."""
    put_spread = _build_vertical_spread(c.IRON_CONDOR, chain, "put", target_short_delta, min_coverage_pct, support_sma_values)
    expiration = _nearest_expiration_or_none(chain, SINGLE_LEG_DTE_RANGE)
    if put_spread is None or expiration is None:
        return None
    call_contracts = _contracts_for(chain, "call", expiration)
    short_call = _pick_short_leg(call_contracts, target_short_delta, "call", chain.underlying_price, min_coverage_pct, support_sma_values or [])
    if short_call is None:
        return None
    long_call = _pick_further_otm(call_contracts, short_call, "call")
    if long_call is None:
        return None

    put_short_strike = put_spread.strikes["short_strike"]
    put_long_strike = put_spread.strikes["long_strike"]
    return CandidateBuild(
        strategy_type=c.IRON_CONDOR,
        expiration_date=expiration,
        strikes={
            "put_short_strike": put_short_strike,
            "put_long_strike": put_long_strike,
            "call_short_strike": short_call.strike,
            "call_long_strike": long_call.strike,
        },
        net_greeks=_sum_greeks(
            put_spread.net_greeks,
            _position_greeks(short_call, True),
            _position_greeks(long_call, False),
        ),
        greeks_source=put_spread.greeks_source if put_spread.greeks_source == "calculated" else _greeks_source(short_call, long_call),
        legs=[*put_spread.legs, Leg("sell", short_call), Leg("buy", long_call)],
    )


def _build_calendar_or_diagonal(
    strategy_type: str, chain: OptionChain, same_strike: bool, option_type: OptionType = "put"
) -> CandidateBuild | None:
    near_expiration = _nearest_expiration_or_none(chain, NEAR_LEG_DTE_RANGE)
    far_expiration = _nearest_expiration_or_none(chain, FAR_LEG_DTE_RANGE)
    if near_expiration is None or far_expiration is None or near_expiration == far_expiration:
        return None

    near_contracts = _contracts_for(chain, option_type, near_expiration)
    short_near = _pick_by_target_delta(near_contracts, CALENDAR_SHORT_DELTA)
    if short_near is None:
        return None

    far_contracts = _contracts_for(chain, option_type, far_expiration)
    if same_strike:
        long_far = min(far_contracts, key=lambda ct: abs(ct.strike - short_near.strike), default=None)
    else:
        long_far = _pick_by_target_delta(far_contracts, TARGET_SHORT_DELTA)
    if long_far is None:
        return None

    return CandidateBuild(
        strategy_type=strategy_type,
        expiration_date=far_expiration,
        strikes={
            "near_expiration": near_expiration.isoformat(),
            "near_strike": short_near.strike,
            "far_expiration": far_expiration.isoformat(),
            "far_strike": long_far.strike,
        },
        net_greeks=_sum_greeks(_position_greeks(short_near, True), _position_greeks(long_far, False)),
        greeks_source=_greeks_source(short_near, long_far),
        legs=[Leg("sell", short_near), Leg("buy", long_far)],
    )


def find_contract(chain: OptionChain, option_type: OptionType, expiration: date, strike: float) -> OptionContract | None:
    """Contrato EXACTO de la cadena por tipo/vencimiento/strike — a diferencia del resto de
    este módulo, que ELIGE strikes por delta objetivo, esto reconstruye el contrato vigente
    de una posición YA ABIERTA (Pestaña Operaciones, réplica automática de operaciones reales,
    pedido 2026-07-25) a partir de los datos OCC ya parseados de `AccountPosition`. None si la
    cadena en vivo ya no tiene ese contrato exacto (vencimiento muy próximo/pasado o strike
    delisted) — el caller decide cómo degradar (ver alerts/real_trades.py)."""
    for ct in chain.contracts:
        if ct.option_type == option_type and ct.expiration == expiration and ct.strike == strike:
            return ct
    return None


def build_from_contract(strategy_type: str, contract: OptionContract, quantity: int, entry_price: float | None = None) -> CandidateBuild:
    """Arma un CandidateBuild de una sola pata VENDIDA a partir de un contrato YA CONOCIDO
    (strike/vencimiento fijos, no elegidos por delta) — usado para operaciones reales ya
    ejecutadas, donde el strike lo decidió el usuario al operar, no el motor. `quantity` son
    los contratos de ESTA operación puntual (puede ser menos que el total de la posición si ya
    había contratos vendidos antes, ver alerts/real_trades.py::_detect_new_short_trades).
    `entry_price`: precio REAL de ejecución (Schwab `averageShortPrice`) — cuando se pasa, la
    prima/P&L se calculan con ESE precio en vez del mark price actual de la cadena en vivo (ver
    `Leg.override_premium`), porque una operación real ya tiene un precio fijo, no uno hipotético."""
    return CandidateBuild(
        strategy_type=strategy_type,
        expiration_date=contract.expiration,
        strikes={"short_strike": contract.strike},
        net_greeks=_position_greeks(contract, is_short=True, quantity=quantity),
        greeks_source=_greeks_source(contract),
        legs=[Leg("sell", contract, quantity, override_premium=entry_price)],
    )


def build_candidate(
    strategy_type: str,
    chain: OptionChain,
    target_short_delta: float = TARGET_SHORT_DELTA,
    min_coverage_pct: float = 0.0,
    support_sma_values: list[float | None] | None = None,
) -> CandidateBuild | None:
    """Construye los strikes/vencimiento concretos para una estrategia dada, a partir de la
    cadena de opciones. Devuelve None si no hay contratos suficientes en la cadena para armarla.

    `target_short_delta`: delta objetivo de la(s) pata(s) corta(s), ajustado por perfil de
    riesgo (settings.strategy.target_short_delta) — más bajo = más OTM = más colchón, menos
    prima. Solo threadeado en las 4 estrategias del MVP (Sección 'perfil de riesgo'
    2026-07-24); el resto sigue con el default del módulo, pausadas de todos modos.

    `min_coverage_pct`/`support_sma_values` (Sección 'perfil de riesgo' refinado, 2026-07-24):
    filtran qué strikes son elegibles para la(s) pata(s) VENDIDA(S) antes de elegir por delta —
    cobertura mínima (settings.strategy.min_coverage_pct) y que el strike se apoye en alguna
    SMA de referencia del perfil (settings.strategy.support_sma_periods, resuelto a valores
    concretos por el caller). Si el strike del delta objetivo no cumple, se sigue buscando más
    OTM hasta encontrar uno que sí — nunca se descarta el candidato por esto solo, salvo que
    NINGÚN strike de la cadena cumpla. Igual que target_short_delta, solo threadeado en las 4
    estrategias del MVP; el resto ignora estos parámetros (comportamiento sin cambios)."""
    if strategy_type in (c.CASH_SECURED_PUT, c.SHORT_PUT_NAKED):
        return _build_single_short_leg(strategy_type, chain, "put", target_short_delta, min_coverage_pct, support_sma_values)
    if strategy_type in (c.COVERED_CALL, c.SHORT_CALL_NAKED):
        return _build_single_short_leg(strategy_type, chain, "call", target_short_delta, min_coverage_pct, support_sma_values)
    if strategy_type == c.BULL_PUT_SPREAD:
        return _build_vertical_spread(strategy_type, chain, "put", target_short_delta)
    if strategy_type == c.BEAR_CALL_SPREAD:
        return _build_vertical_spread(strategy_type, chain, "call", target_short_delta)
    if strategy_type == c.BULL_CALL_SPREAD:
        return _build_debit_vertical_spread(strategy_type, chain, "call")
    if strategy_type == c.BEAR_PUT_SPREAD:
        return _build_debit_vertical_spread(strategy_type, chain, "put")
    if strategy_type == c.COLLAR:
        return _build_collar(chain, target_short_delta, min_coverage_pct, support_sma_values)
    if strategy_type == c.IRON_CONDOR:
        return _build_iron_condor(chain, target_short_delta, min_coverage_pct, support_sma_values)
    if strategy_type == c.CALENDAR_PUT_SPREAD:
        return _build_calendar_or_diagonal(strategy_type, chain, same_strike=True, option_type="put")
    if strategy_type == c.CALENDAR_CALL_SPREAD:
        return _build_calendar_or_diagonal(strategy_type, chain, same_strike=True, option_type="call")
    if strategy_type == c.DIAGONAL_PUT_SPREAD:
        return _build_calendar_or_diagonal(strategy_type, chain, same_strike=False, option_type="put")
    if strategy_type == c.DIAGONAL_CALL_SPREAD:
        return _build_calendar_or_diagonal(strategy_type, chain, same_strike=False, option_type="call")
    if strategy_type == c.CALL_RATIO_BACKSPREAD:
        return _build_ratio_backspread(strategy_type, chain, "call")
    if strategy_type == c.CALL_RATIO_SPREAD:
        return _build_ratio_front_spread(strategy_type, chain, "call")
    if strategy_type == c.PUT_RATIO_SPREAD:
        return _build_ratio_front_spread(strategy_type, chain, "put")
    if strategy_type == c.SHORT_CALL_CONDOR:
        return _build_short_condor(strategy_type, chain, "call")
    if strategy_type == c.SHORT_PUT_CONDOR:
        return _build_short_condor(strategy_type, chain, "put")
    raise ValueError(f"Estrategia desconocida: {strategy_type}")
