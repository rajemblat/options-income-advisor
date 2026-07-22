from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from options_advisor.broker.models import OptionChain, PriceBar, Quote


class BrokerClient(ABC):
    """Interfaz agnóstica de broker. Todo el motor de análisis programa contra esta clase,
    nunca contra MockBrokerClient o SchwabBrokerClient directamente (Sección 7.2 de la hoja de ruta)."""

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        ...

    @abstractmethod
    def get_option_chain(
        self, symbol: str, expiration_range_days: tuple[int, int] = (7, 60)
    ) -> OptionChain:
        """Cadena de opciones con bid/ask, IV y griegos (marcados como 'broker' o 'calculated'
        según el broker los provea o haya que usar el fallback de indicators/greeks.py)."""
        ...

    @abstractmethod
    def get_price_history(self, symbol: str, lookback_days: int) -> list[PriceBar]:
        """OHLCV diario, usado para ATR/RSI/medias móviles/soportes-resistencias e IV Rank proxy."""
        ...

    @abstractmethod
    def is_authenticated(self) -> bool:
        ...
