from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

from options_advisor.broker.models import (
    AccountPosition,
    FilledOrder,
    IntradayBar,
    Mover,
    OptionChain,
    PriceBar,
    Quote,
)


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
    def get_intraday_bars(
        self, symbol: str, session_date: date, interval_minutes: int = 1
    ) -> list[IntradayBar]:
        """OHLCV intradía de la sesión REGULAR (9:30-16:00 ET, sin pre/after-market) de
        `session_date`, en el intervalo pedido — base del gráfico de velas + VWAP
        (2026-07-31). `interval_minutes` debe ser uno de 1/5/10/15/30 (únicos valores que
        Schwab acepta para frequencyType=minute, confirmado en vivo). [] si `session_date` no
        es día hábil o si el broker no tiene datos para esa sesión."""
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
    def get_recent_filled_orders(self, since: datetime) -> list[FilledOrder]:
        """Órdenes LLENADAS (`status=FILLED`) desde `since` (timezone-aware, UTC) en todas las
        cuentas vinculadas — usado por la Pestaña Operaciones (Sección 'rediseño vía /orders',
        2026-07-28) para detectar aperturas nuevas con el fill EXACTO de cada orden, en vez de
        diffear posiciones contra un promedio blendeado. [] si el broker no tiene cuentas
        reales (MockBrokerClient)."""
        ...

    @abstractmethod
    def get_movers(self, index: str, sort: str, frequency: int = 0) -> list[Mover]:
        """Top movers de un índice de referencia (`$SPX`/`$DJI`/`$COMPX`/etc.), usado en la
        página principal estilo CNBC. `sort` es uno de `PERCENT_CHANGE_UP`,
        `PERCENT_CHANGE_DOWN`, `VOLUME`, `TRADES` (valores del endpoint real de Schwab).
        [] fuera de horario de mercado (el endpoint real no tiene datos que devolver) o en
        modo mock si no hay fixture cargada."""
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
