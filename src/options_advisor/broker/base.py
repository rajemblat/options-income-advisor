from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from options_advisor.broker.models import AccountPosition, OptionChain, PriceBar, Quote


class BrokerClient(ABC):
    """Interfaz agnóstica de broker. Todo el motor de análisis programa contra esta clase,
    nunca contra MockBrokerClient o SchwabBrokerClient directamente (Sección 7.2 de la hoja de ruta)."""

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        ...

    @abstractmethod
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Quotes de varios símbolos en una sola llamada cuando el broker lo soporta (Schwab:
        probado en vivo, 100+ símbolos por llamada) — usado para las proyecciones de portafolio
        real, donde hace falta el precio de varios subyacentes a la vez sin pegarle al broker
        una vez por símbolo. Símbolos sin quote disponible simplemente no aparecen en el dict."""
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

    @abstractmethod
    def get_all_share_positions(self) -> dict[str, int]:
        """Símbolo -> cantidad de acciones actualmente en cartera, sumado a través de todas las
        cuentas si el broker tiene noción de cuenta real. Usado para habilitar Covered Call/
        Collar (requieren 100+ acciones ya en cartera) con la posición REAL, no una tabla
        interna de seguimiento. {} si el broker no tiene cuentas reales (MockBrokerClient)."""
        ...

    @abstractmethod
    def get_all_positions(self) -> list[AccountPosition]:
        """Todas las posiciones reales (acciones, opciones, ETFs) de todas las cuentas
        vinculadas — página de portafolio real, Entrega 1. [] si el broker no tiene cuentas
        reales (MockBrokerClient)."""
        ...

    @abstractmethod
    def screen_universe(self, symbols: list[str], max_shortlist: int = 60) -> list[str]:
        """Fase 1 del escaneo de universo amplio (Sección 'universo amplio' 2026-07-24): filtro
        barato usando solo quotes en batch (sin cadenas de opciones) — optionable, rango de
        precio razonable, liquidez mínima — y rankeo por un proxy gratis de volatilidad
        histórica (rango 52 semanas / precio), devolviendo como máximo `max_shortlist`
        símbolos. Reduce cientos de símbolos a un shortlist manejable antes de correr el
        pipeline completo (caro) solo sobre esos. MockBrokerClient devuelve la lista sin
        cambios — no hay datos reales de mercado para filtrar/rankear."""
        ...
