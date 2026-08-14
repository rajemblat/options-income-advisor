from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

OptionType = Literal["call", "put"]
GreeksSource = Literal["broker", "calculated"]

# Símbolo OCC: 6 caracteres de root (padded con espacios) + YYMMDD + C/P + strike*1000 en 8
# dígitos. Formato estable de la industria (no de un broker específico) — movido acá desde
# broker/schwab_client.py (2026-07-28, rediseño de detección de operaciones reales vía /orders)
# para que tanto el parseo de posiciones (schwab_client.py) como el de patas de órdenes
# (alerts/real_trades.py) compartan la misma función en vez de duplicarla.
_OCC_OPTION_SYMBOL_RE = re.compile(r"^(?P<root>.{6})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<cp>[CP])(?P<strike>\d{8})$")


def parse_occ_option_symbol(symbol: str) -> tuple[str, date, str, float] | None:
    """(underlying, expiration, option_type, strike) a partir del símbolo OCC, o None si no
    matchea el formato (no es una opción estándar)."""
    match = _OCC_OPTION_SYMBOL_RE.match(symbol)
    if not match:
        return None
    try:
        expiration = date(2000 + int(match["yy"]), int(match["mm"]), int(match["dd"]))
    except ValueError:
        return None
    option_type = "call" if match["cp"] == "C" else "put"
    strike = int(match["strike"]) / 1000
    return match["root"].strip(), expiration, option_type, strike


class Quote(BaseModel):
    symbol: str
    as_of: date
    last_price: float
    bid: float
    ask: float
    # Próxima fecha ex-dividendo conocida — None en modo mock (sin datos de dividendos en las
    # fixtures) o si el broker no la expone para este símbolo (ETFs/índices sin dividendo
    # calendarizado). Usado para advertir sobre riesgo de asignación anticipada en calls
    # vendidas (Covered Call/Collar/Iron Condor) que vencen después del ex-date.
    next_ex_dividend_date: date | None = None
    # Ticker estilo CNBC (sumado 2026-07-26): variación vs. cierre anterior (incluye
    # after-hours/pre-market, es lo que Schwab llama `netChange`/`netPercentChange` — el precio
    # "actual" sea cual sea la sesión). `post_market_change_pct` es la variación ADICIONAL
    # ocurrida específicamente después del cierre regular (`postMarketChange` de Schwab) — None
    # si no hay sesión extendida en curso o el broker no la expone (modo mock).
    net_change: float = 0.0
    net_change_pct: float = 0.0
    post_market_change_pct: float | None = None
    # "stock" | "etf" | "index" | None — sumado 2026-07-27 para el filtro de tipo de instrumento
    # de la Pestaña Screener. Viene de assetMainType/assetSubType que Schwab YA devuelve en
    # /quotes (sin llamada nueva, ver schwab_client.py::_classify_instrument_type) — None en
    # modo mock (las fixtures no tienen esta clasificación) o si Schwab no la expone para ese
    # símbolo puntual.
    instrument_type: str | None = None
    # Nombre de la empresa y volumen total del día — sumados 2026-07-29 para armar Market
    # Movers a partir de quotes en batch (`reference.description`/`quote.totalVolume` de
    # Schwab, ya venían en la respuesta de /quotes sin costo extra). None/0 en modo mock (las
    # fixtures no tienen nombre de empresa ni volumen real de mercado).
    description: str | None = None
    total_volume: int | None = None


class PriceBar(BaseModel):
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class IntradayBar(BaseModel):
    """OHLCV de una sesión intradía (gráfico de velas + VWAP, 2026-07-31) — a diferencia de
    `PriceBar` (una barra = un día), acá una barra es un intervalo dentro de la sesión regular
    (9:30-16:00 ET), con `timestamp` tz-aware en vez de `trade_date`."""

    symbol: str
    timestamp: datetime
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
    # OJO: `symbol` es el símbolo del SUBYACENTE (ej. "$SPX", "AAPL"), NO el de esta opción. Se llama
    # así desde el principio y media app depende de eso, por eso no se renombra.
    symbol: str
    # Símbolo OCC de 21 caracteres de ESTA opción, tal cual lo devuelve el broker
    # (ej. "SPXW  260814P07765000"). Es lo único que sirve para mandar una orden sobre esta pata:
    # reconstruirlo a mano es un riesgo real en índices, donde el root del semanal (SPXW) no coincide
    # con el del subyacente (SPX). None en modo mock y en fixtures de tests.
    # Bug real que motivó el campo (2026-08-14, con el condor real ya autorizado y el mercado
    # abierto): el motor tomaba `symbol` de las 4 patas para armar la orden combinada y recibía
    # cuatro "$SPX" idénticos, así que `build_iron_condor_open` la rechazaba cada minuto con
    # "hacen falta 4 símbolos OCC distintos". El símbolo bueno venía de Schwab y se descartaba al parsear.
    occ_symbol: str | None = None
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


# El root OCC de una opción de índice (parseado por `_parse_occ_option_symbol` en
# schwab_client.py) es el ticker "pelado" (ej. "RUT", "NDX", weeklies "RUTW"/"NDXW") — pero
# `get_quote`/`get_quotes`/`get_option_chain` de Schwab exigen el símbolo con prefijo `$`
# (ej. "$RUT"). Sin este mapeo, cualquier página que pida cotización/cadena para el
# `underlying_symbol` de una posición de opción de índice real falla en silencio (no matchea
# nada en el dict de `get_quotes`, o el request de cadena usa un símbolo que Schwab no reconoce).
_INDEX_OCC_ROOT_TO_QUOTE_SYMBOL = {
    "RUT": "$RUT",
    "RUTW": "$RUT",
    "NDX": "$NDX",
    "NDXW": "$NDX",
    "SPX": "$SPX",
    "SPXW": "$SPX",
    "VIX": "$VIX",
}


def index_quote_symbol(underlying_symbol: str) -> str:
    """Convierte un root OCC de índice a su símbolo de cotización (`$`-prefijado). Para
    cualquier otro símbolo (acciones/ETFs normales) lo devuelve sin cambios."""
    return _INDEX_OCC_ROOT_TO_QUOTE_SYMBOL.get(underlying_symbol, underlying_symbol)


MoverDirection = Literal["up", "down"]


class Mover(BaseModel):
    """Una fila del endpoint `/movers` de Schwab (heredado de TD Ameritrade) — confirmado en
    vivo 2026-07-25 (ver NOTES.md). `change_pct` viene siempre con el signo correcto (positivo
    para `direction == "up"`, negativo para `direction == "down"`) independientemente de cómo
    lo devuelva el broker."""

    symbol: str
    description: str
    last_price: float
    change_pct: float
    direction: MoverDirection
    total_volume: int


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
    # Margen/garantía REAL que exige el broker por esta posición (Schwab: maintenanceRequirement).
    # Es el colateral verdadero de tu cuenta (portfolio margin) — sirve para calibrar la estimación
    # del simulador a tu broker (usuario 2026-08-06). None si el broker no lo reporta.
    maintenance_requirement: float | None = None


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


class FilledOrderLeg(BaseModel):
    """Una pata de una orden YA LLENADA (`status=FILLED` en Schwab) — Sección 'rediseño de
    Operaciones vía /orders' (2026-07-28), reemplaza el diff de posiciones/promedio blendeado
    de la Fase 1 anterior. `price` es el fill EXACTO de ESTA pata en ESTA orden puntual (de
    `orderActivityCollection[].executionLegs[]`, agregado si hubo más de una ejecución parcial)
    — no un promedio de toda la posición acumulada."""

    occ_symbol: str
    instruction: str  # "SELL_TO_OPEN" | "BUY_TO_OPEN" | "SELL_TO_CLOSE" | "BUY_TO_CLOSE"
    position_effect: str  # "OPENING" | "CLOSING"
    quantity: float
    price: float


class FilledOrder(BaseModel):
    """Una orden llenada de Schwab (`/accounts/{hash}/orders`), con sus patas ya resueltas a
    fill exacto. Una orden con una pata OPENING y una CLOSING en la MISMA orden es un roll
    (cerrar+abrir combinado) — detectable con certeza por la composición de la orden, sin
    heurística de ventana temporal/subyacente entre corridas (ver alerts/real_trades.py)."""

    order_id: int
    account_number: str
    fill_time: datetime
    legs: list[FilledOrderLeg]
