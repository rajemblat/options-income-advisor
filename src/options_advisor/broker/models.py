from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

OptionType = Literal["call", "put"]
GreeksSource = Literal["broker", "calculated"]


class Quote(BaseModel):
    symbol: str
    as_of: date
    last_price: float
    bid: float
    ask: float


class PriceBar(BaseModel):
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class Greeks(BaseModel):
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    source: GreeksSource


class OptionContract(BaseModel):
    symbol: str
    option_type: OptionType
    strike: float
    expiration: date
    bid: float
    ask: float
    last_price: float
    implied_volatility: float
    open_interest: int
    volume: int
    greeks: Greeks

    @property
    def mid_price(self) -> float:
        return round((self.bid + self.ask) / 2, 4)


class AccountPosition(BaseModel):
    """Una posición real de cuenta. Entrega 1: símbolo, cantidad, precio de entrada, valor
    actual, P&L. Entrega 2 (análisis sin IA): se suman underlying_symbol/option_type/strike/
    expiration para posiciones de opciones — parseados del símbolo OCC (formato estable, no
    depende del texto de `description`) — habilitan % de retorno y proyecciones."""

    account_number: str
    symbol: str
    asset_type: str  # "EQUITY" | "OPTION" | "COLLECTIVE_INVESTMENT" | otros de Schwab
    quantity: float  # positivo = largo, negativo = corto
    average_price: float
    market_value: float
    unrealized_pnl: float
    description: str | None = None
    underlying_symbol: str | None = None  # solo si asset_type == "OPTION"
    option_type: str | None = None  # "put" | "call", solo si asset_type == "OPTION"
    strike: float | None = None
    expiration: date | None = None


class OptionChain(BaseModel):
    symbol: str
    as_of: date
    underlying_price: float
    contracts: list[OptionContract]

    def atm_contract(self, option_type: OptionType, expiration: date | None = None) -> OptionContract:
        """Contrato más cercano al dinero (ATM), opcionalmente filtrado por vencimiento."""
        candidates = [c for c in self.contracts if c.option_type == option_type]
        if expiration is not None:
            candidates = [c for c in candidates if c.expiration == expiration]
        if not candidates:
            raise ValueError(f"No hay contratos {option_type} disponibles para {self.symbol}")
        return min(candidates, key=lambda c: abs(c.strike - self.underlying_price))

    def nearest_expiration(self, min_days: int = 30, max_days: int = 45) -> date:
        expirations = sorted({c.expiration for c in self.contracts})
        window = [
            e for e in expirations
            if min_days <= (e - self.as_of).days <= max_days
        ]
        if window:
            return window[0]
        if not expirations:
            raise ValueError(f"No hay vencimientos disponibles para {self.symbol}")
        return min(expirations, key=lambda e: abs((e - self.as_of).days - (min_days + max_days) / 2))
