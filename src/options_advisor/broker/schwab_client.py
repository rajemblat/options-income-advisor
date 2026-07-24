from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import AccountPosition, Greeks, OptionChain, OptionContract, OptionType, PriceBar, Quote
from options_advisor.broker.schwab_auth import DEFAULT_TOKEN_STORE_PATH, SchwabAuth
from options_advisor.indicators.greeks import calculate_greeks

logger = logging.getLogger(__name__)

MARKET_DATA_BASE_URL = "https://api.schwabapi.com/marketdata/v1"
TRADER_API_BASE_URL = "https://api.schwabapi.com/trader/v1"
DEFAULT_RISK_FREE_RATE = 0.045

# Símbolo OCC de un contrato de opción: 6 chars de raíz (rellenados con espacios) + YYMMDD +
# C/P + strike*1000 en 8 dígitos. Formato estable de la industria (no de Schwab específicamente)
# — más confiable que parsear el texto libre de `description`.
_OCC_OPTION_SYMBOL_RE = re.compile(r"^(?P<root>.{6})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<cp>[CP])(?P<strike>\d{8})$")


def _parse_occ_option_symbol(symbol: str) -> tuple[str, date, str, float] | None:
    """(underlying, expiration, option_type, strike) a partir del símbolo OCC, o None si no
    matchea el formato (posición no es una opción estándar)."""
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


class SchwabBrokerClient(BrokerClient):
    """Implementación real de BrokerClient contra la Schwab Trader API — verificada en vivo
    (autenticación, quotes, historial de precios, cadena de opciones con griegos/IV/OI/volumen
    reales, y lectura de posiciones de cuenta real) el 2026-07-23/24."""

    def __init__(self, auth: SchwabAuth, risk_free_rate: float = DEFAULT_RISK_FREE_RATE):
        self.auth = auth
        self.risk_free_rate = risk_free_rate
        self._client = httpx.Client(base_url=MARKET_DATA_BASE_URL, timeout=15.0)
        self._trader_client = httpx.Client(base_url=TRADER_API_BASE_URL, timeout=15.0)

    @classmethod
    def from_env(cls) -> SchwabBrokerClient:
        client_id = os.environ["SCHWAB_CLIENT_ID"]
        client_secret = os.environ["SCHWAB_CLIENT_SECRET"]
        redirect_uri = os.environ.get("SCHWAB_REDIRECT_URI", "https://127.0.0.1:8182/callback")
        auth = SchwabAuth(client_id, client_secret, redirect_uri, DEFAULT_TOKEN_STORE_PATH)
        return cls(auth)

    def is_authenticated(self) -> bool:
        return self.auth.is_authenticated()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _get(self, path: str, params: dict) -> dict:
        response = self._client.get(
            path, params=params, headers={"Authorization": f"Bearer {self.auth.get_valid_access_token()}"}
        )
        if response.status_code == 429:
            logger.warning("Rate limit de Schwab alcanzado en %s, reintentando con backoff", path)
        response.raise_for_status()
        return response.json()

    def get_quote(self, symbol: str) -> Quote:
        data = self._get(f"/{symbol}/quotes", params={})
        quote = data[symbol]["quote"]
        return Quote(
            symbol=symbol,
            as_of=date.today(),
            last_price=quote["lastPrice"],
            bid=quote["bidPrice"],
            ask=quote["askPrice"],
        )

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Batch real de Schwab (probado en vivo: 100+ símbolos en una sola llamada, sin
        rate-limit en 20 llamadas seguidas) — evita 1 llamada por subyacente en las
        proyecciones de portafolio real."""
        if not symbols:
            return {}
        try:
            data = self._get("/quotes", params={"symbols": ",".join(symbols)})
        except Exception:
            logger.exception("Fallo al pedir quotes en batch de Schwab; se omite")
            return {}
        quotes: dict[str, Quote] = {}
        for symbol, entry in data.items():
            quote = entry.get("quote")
            if not quote:
                continue
            quotes[symbol] = Quote(
                symbol=symbol,
                as_of=date.today(),
                last_price=quote["lastPrice"],
                bid=quote["bidPrice"],
                ask=quote["askPrice"],
            )
        return quotes

    def get_price_history(self, symbol: str, lookback_days: int) -> list[PriceBar]:
        data = self._get(
            "/pricehistory",
            params={
                "symbol": symbol,
                "periodType": "year",
                "period": 2,
                "frequencyType": "daily",
                "frequency": 1,
            },
        )
        bars = []
        for candle in data.get("candles", []):
            bars.append(
                PriceBar(
                    symbol=symbol,
                    trade_date=datetime.fromtimestamp(candle["datetime"] / 1000).date(),
                    open=candle["open"],
                    high=candle["high"],
                    low=candle["low"],
                    close=candle["close"],
                    volume=candle["volume"],
                )
            )
        bars.sort(key=lambda b: b.trade_date)
        return bars[-lookback_days:]

    def get_option_chain(self, symbol: str, expiration_range_days: tuple[int, int] = (7, 60)) -> OptionChain:
        min_days, max_days = expiration_range_days
        today = date.today()
        data = self._get(
            "/chains",
            params={
                "symbol": symbol,
                "contractType": "ALL",
                "fromDate": (today + timedelta(days=min_days)).isoformat(),
                "toDate": (today + timedelta(days=max_days)).isoformat(),
            },
        )
        underlying_price = data["underlyingPrice"]
        # Schwab devuelve la tasa libre de riesgo y el dividend yield vigentes junto con la
        # cadena — más precisos que la tasa fija de config/settings.yaml, usados en el fallback
        # de Black-Scholes-Merton cuando Schwab no da griegos (ver _parse_contract). Con `None`
        # o 0 caemos al valor fijo de config, mismo comportamiento que antes.
        interest_rate = (data.get("interestRate") or 0) / 100 or self.risk_free_rate
        dividend_yield = (data.get("dividendYield") or 0) / 100

        contracts = []
        for option_type, exp_map_key in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
            for _exp_key, strikes in data.get(exp_map_key, {}).items():
                for _strike_key, contract_list in strikes.items():
                    for raw in contract_list:
                        contracts.append(
                            self._parse_contract(symbol, option_type, raw, underlying_price, today, interest_rate, dividend_yield)
                        )

        return OptionChain(symbol=symbol, as_of=today, underlying_price=underlying_price, contracts=contracts)

    def _parse_contract(
        self,
        symbol: str,
        option_type: OptionType,
        raw: dict,
        underlying_price: float,
        as_of: date,
        interest_rate: float,
        dividend_yield: float,
    ) -> OptionContract:
        expiration = date.fromisoformat(raw["expirationDate"].split("T")[0])
        strike = raw["strikePrice"]
        implied_volatility = raw["volatility"] / 100  # Schwab la expresa en porcentaje (ej. 23.5 -> 0.235)

        has_broker_greeks = all(raw.get(k) is not None for k in ("delta", "gamma", "theta", "vega", "rho"))
        if has_broker_greeks:
            greeks = Greeks(
                delta=raw["delta"], gamma=raw["gamma"], theta=raw["theta"], vega=raw["vega"], rho=raw["rho"], source="broker"
            )
        else:
            greeks = calculate_greeks(
                option_type=option_type,
                underlying_price=underlying_price,
                strike=strike,
                expiration=expiration,
                as_of_date=as_of,
                implied_volatility=implied_volatility,
                risk_free_rate=interest_rate,
                dividend_yield=dividend_yield,
            )

        return OptionContract(
            symbol=symbol,
            option_type=option_type,
            strike=strike,
            expiration=expiration,
            bid=raw["bid"],
            ask=raw["ask"],
            last_price=raw["last"],
            implied_volatility=implied_volatility,
            open_interest=raw.get("openInterest", 0),
            volume=raw.get("totalVolume", 0),
            greeks=greeks,
        )

    def _iter_raw_positions(self):
        """Generador de (número de cuenta, posición cruda) a través de todas las cuentas
        vinculadas — compartido por get_all_share_positions y get_all_positions para no
        duplicar el fetch de cuentas. Una cuenta que falle no tumba las demás; sin cuentas
        legibles, no yieldea nada (mismo resultado que MockBrokerClient: sin datos reales)."""
        headers = {"Authorization": f"Bearer {self.auth.get_valid_access_token()}"}
        try:
            response = self._trader_client.get("/accounts/accountNumbers", headers=headers)
            response.raise_for_status()
            accounts = response.json()
        except Exception:
            logger.exception("Fallo al listar cuentas de Schwab; sin posiciones reales esta corrida")
            return

        for account in accounts:
            try:
                response = self._trader_client.get(
                    f"/accounts/{account['hashValue']}", params={"fields": "positions"}, headers=headers
                )
                response.raise_for_status()
                for position in response.json().get("securitiesAccount", {}).get("positions", []):
                    yield account["accountNumber"], position
            except Exception:
                logger.exception("Fallo al leer posiciones de la cuenta %s; se continúa con el resto", account.get("accountNumber"))

    def get_all_share_positions(self) -> dict[str, int]:
        """Suma `longQuantity` de todas las posiciones EQUITY, a través de todas las cuentas
        vinculadas — habilita Covered Call/Collar con la tenencia REAL en vez de una tabla
        interna de seguimiento (ver strategy/selector.py::select_candidate_strategies)."""
        positions: dict[str, int] = {}
        for _account_number, position in self._iter_raw_positions():
            instrument = position.get("instrument", {})
            if instrument.get("assetType") != "EQUITY":
                continue
            symbol = instrument.get("symbol")
            qty = int(position.get("longQuantity", 0))
            positions[symbol] = positions.get(symbol, 0) + qty
        return positions

    def get_all_positions(self) -> list[AccountPosition]:
        """Todas las posiciones reales (acciones, opciones, ETFs) de todas las cuentas
        vinculadas — página de portafolio real, Entrega 1 (símbolo/cantidad/precio entrada/
        valor actual/P&L). `longOpenProfitLoss` es el campo que Schwab usa para el P&L no
        realizado tanto en posiciones largas como cortas (confirmado con datos reales)."""
        positions: list[AccountPosition] = []
        for account_number, position in self._iter_raw_positions():
            instrument = position.get("instrument", {})
            long_qty = position.get("longQuantity", 0) or 0
            short_qty = position.get("shortQuantity", 0) or 0
            average_price = position.get("averageLongPrice") if long_qty else position.get("averageShortPrice")

            symbol = instrument.get("symbol", "")
            option_fields = _parse_occ_option_symbol(symbol) if instrument.get("assetType") == "OPTION" else None
            underlying_symbol, expiration, option_type, strike = option_fields if option_fields else (
                instrument.get("underlyingSymbol"), None, None, None
            )

            positions.append(
                AccountPosition(
                    account_number=account_number,
                    symbol=symbol,
                    asset_type=instrument.get("assetType", ""),
                    quantity=long_qty - short_qty,
                    average_price=average_price if average_price is not None else (position.get("averagePrice") or 0.0),
                    market_value=position.get("marketValue", 0.0) or 0.0,
                    unrealized_pnl=position.get("longOpenProfitLoss", 0.0) or 0.0,
                    description=instrument.get("description"),
                    underlying_symbol=underlying_symbol,
                    option_type=option_type,
                    strike=strike,
                    expiration=expiration,
                )
            )
        return positions
