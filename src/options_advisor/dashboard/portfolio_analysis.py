from __future__ import annotations

from datetime import date

from py_vollib.black_scholes_merton import black_scholes_merton

from options_advisor.broker.models import AccountPosition, OptionChain

_OPTION_MULTIPLIER = 100
_MIN_YEARS_TO_EXPIRATION = 1 / 365  # evita división por cero cuando target_date == expiration


def position_multiplier(position: AccountPosition) -> int:
    return _OPTION_MULTIPLIER if position.asset_type == "OPTION" else 1


def position_cost_basis(position: AccountPosition) -> float:
    """Base de costo absoluta — denominador del % de retorno. Para una posición corta, es el
    valor nominal comprometido (prima recibida × multiplicador), no un "costo" en el sentido
    tradicional, pero es el denominador que hace que el % sea comparable entre posiciones."""
    return abs(position.average_price * position.quantity) * position_multiplier(position)


def position_pct_return(position: AccountPosition) -> float | None:
    """% de ganancia/pérdida sobre la base de costo. None si la base de costo es 0 (posición
    sin costo registrado — no debería pasar en la práctica, pero evita división por cero)."""
    cost_basis = position_cost_basis(position)
    if cost_basis == 0:
        return None
    return (position.unrealized_pnl / cost_basis) * 100


def intrinsic_value(option_type: str, strike: float, underlying_price: float) -> float:
    if option_type == "call":
        return max(0.0, underlying_price - strike)
    return max(0.0, strike - underlying_price)


def projected_pnl_at_own_expiration(position: AccountPosition, underlying_price: float) -> float | None:
    """P&L proyectado si el precio del subyacente se mantiene en `underlying_price` hasta el
    PROPIO vencimiento de esta posición — solo valor intrínseco, no hace falta IV porque en el
    vencimiento el valor extrínseco ya es 0. None si no es una posición de opción con strike/
    tipo conocidos (ver AccountPosition, parseado del símbolo OCC)."""
    if position.asset_type != "OPTION" or position.strike is None or position.option_type is None:
        return None
    terminal_value = intrinsic_value(position.option_type, position.strike, underlying_price)
    return position.quantity * position_multiplier(position) * (terminal_value - position.average_price)


def reprice_option_bsm(
    option_type: str,
    underlying_price: float,
    strike: float,
    years_to_expiration: float,
    iv: float,
    risk_free_rate: float,
) -> float:
    """Precio teórico de la opción a `years_to_expiration` años del vencimiento, vía
    Black-Scholes-Merton, manteniendo precio del subyacente e IV constantes ('si nada cambia')
    — mismo patrón que ya usa strategy/payoff.py para calendars/diagonals."""
    if years_to_expiration <= 0:
        return intrinsic_value(option_type, strike, underlying_price)
    flag = "c" if option_type == "call" else "p"
    return black_scholes_merton(flag, underlying_price, strike, years_to_expiration, risk_free_rate, iv, 0.0)


def projected_pnl_at_date(
    position: AccountPosition,
    underlying_price: float,
    target_date: date,
    iv: float | None,
    risk_free_rate: float,
) -> float | None:
    """P&L proyectado a una fecha específica elegida por el usuario, manteniendo precio del
    subyacente e IV actuales constantes. Si `target_date` ya pasó el vencimiento de la
    posición, es un cálculo de solo valor intrínseco (no hace falta IV). Si es antes del
    vencimiento, hace falta la IV vigente del contrato (fetch de cadena en vivo, ver
    dashboard/pages/7_portafolio.py) — None si no está disponible y hace falta."""
    if position.asset_type != "OPTION" or position.strike is None or position.option_type is None or position.expiration is None:
        return None

    years_remaining = max((position.expiration - target_date).days, 0) / 365
    if years_remaining <= 0:
        theoretical_price = intrinsic_value(position.option_type, position.strike, underlying_price)
    else:
        if iv is None:
            return None
        theoretical_price = reprice_option_bsm(position.option_type, underlying_price, position.strike, years_remaining, iv, risk_free_rate)

    return position.quantity * position_multiplier(position) * (theoretical_price - position.average_price)


def effective_projected_pnl_at_own_expiration(position: AccountPosition, underlying_price: float | None) -> float | None:
    """Wrapper para la tabla de portafolio: en no-opciones (acciones/ETFs, sin vencimiento) no
    hay decaimiento de tiempo — "si el precio no cambia" es literalmente el P&L de hoy. Solo
    las opciones delegan en projected_pnl_at_own_expiration."""
    if position.asset_type != "OPTION":
        return position.unrealized_pnl
    if underlying_price is None:
        return None
    return projected_pnl_at_own_expiration(position, underlying_price)


def effective_projected_pnl_at_date(
    position: AccountPosition, underlying_price: float | None, target_date: date, iv: float | None, risk_free_rate: float
) -> float | None:
    """Mismo criterio que effective_projected_pnl_at_own_expiration, para la proyección a
    fecha elegida por el usuario."""
    if position.asset_type != "OPTION":
        return position.unrealized_pnl
    if underlying_price is None:
        return None
    return projected_pnl_at_date(position, underlying_price, target_date, iv, risk_free_rate)


def compute_concentration(underlying_values: list[tuple[str, float]]) -> list[dict]:
    """% del valor total del portafolio (valor absoluto de mercado, para que cortos y largos
    sumen exposición en vez de cancelarse) por símbolo subyacente — Entrega 3 (análisis de
    exposición). `underlying_values` = [(símbolo_subyacente, market_value), ...], una entrada
    por posición — el caller decide qué símbolo corresponde a cada una (una opción usa el
    subyacente, no su propio símbolo OCC). Devuelve ordenado de mayor a menor concentración;
    lista vacía si el portafolio no tiene valor (todo en $0)."""
    totals: dict[str, float] = {}
    for symbol, market_value in underlying_values:
        totals[symbol] = totals.get(symbol, 0.0) + abs(market_value)

    grand_total = sum(totals.values())
    if grand_total == 0:
        return []

    rows = [{"symbol": symbol, "value": value, "pct": value / grand_total * 100} for symbol, value in totals.items()]
    return sorted(rows, key=lambda r: r["pct"], reverse=True)


def compute_earnings_clusters(earnings_by_symbol: dict[str, date | None], window_days: int = 10) -> list[dict]:
    """Agrupa símbolos del portafolio cuyas próximas fechas de earnings caen dentro de
    `window_days` entre sí — riesgo de gap simultáneo en varias posiciones a la vez, no
    diversificado en el tiempo (Entrega 3). Cada símbolo aparece en como máximo un cluster (el
    primero, por orden de fecha) — un cluster de 1 solo símbolo no cuenta como riesgo
    concentrado y se omite. Devuelve ordenado por fecha más próxima primero."""
    dated = sorted(((symbol, d) for symbol, d in earnings_by_symbol.items() if d is not None), key=lambda x: x[1])

    clusters: list[dict] = []
    used: set[str] = set()
    for i, (symbol, earnings_date) in enumerate(dated):
        if symbol in used:
            continue
        group = [symbol]
        for other_symbol, other_date in dated[i + 1 :]:
            if other_symbol in used:
                continue
            if (other_date - earnings_date).days <= window_days:
                group.append(other_symbol)
        if len(group) > 1:
            used.update(group)
            clusters.append({"symbols": group, "earliest_date": earnings_date.isoformat()})

    return clusters


def find_matching_contract_iv(chain: OptionChain, position: AccountPosition) -> float | None:
    """IV actual del contrato exacto de `position` dentro de una cadena recién pedida al
    broker — necesaria para proyectar a una fecha ANTES del vencimiento (ver
    projected_pnl_at_date). None si el contrato ya no aparece en la cadena (vencimiento fuera
    del rango pedido, o liquidez nula)."""
    for contract in chain.contracts:
        if (
            contract.option_type == position.option_type
            and contract.strike == position.strike
            and contract.expiration == position.expiration
        ):
            return contract.implied_volatility
    return None
