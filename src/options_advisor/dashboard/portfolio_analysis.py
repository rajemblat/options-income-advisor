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
