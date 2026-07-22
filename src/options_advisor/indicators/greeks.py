from __future__ import annotations

from datetime import date

from py_vollib.black_scholes_merton.greeks.analytical import delta, gamma, rho, theta, vega

from options_advisor.broker.models import Greeks, OptionType

_MIN_TIME_TO_EXPIRY_YEARS = 1 / 365  # evita división por cero cuando expiration == as_of_date


def calculate_greeks(
    option_type: OptionType,
    underlying_price: float,
    strike: float,
    expiration: date,
    as_of_date: date,
    implied_volatility: float,
    risk_free_rate: float,
    dividend_yield: float = 0.0,
) -> Greeks:
    """Fallback de griegos vía Black-Scholes-Merton, usado cuando el broker no los provee
    (Sección 7.1 de la hoja de ruta). Requiere la IV del contrato como dato conocido."""
    flag = "c" if option_type == "call" else "p"
    t = max((expiration - as_of_date).days / 365.0, _MIN_TIME_TO_EXPIRY_YEARS)
    args = (flag, underlying_price, strike, t, risk_free_rate, implied_volatility, dividend_yield)

    return Greeks(
        delta=round(delta(*args), 4),
        gamma=round(gamma(*args), 4),
        theta=round(theta(*args), 4),
        vega=round(vega(*args), 4),
        rho=round(rho(*args), 4),
        source="calculated",
    )
