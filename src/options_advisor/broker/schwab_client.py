from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import Greeks, OptionChain, OptionContract, OptionType, PriceBar, Quote
from options_advisor.broker.schwab_auth import DEFAULT_TOKEN_STORE_PATH, SchwabAuth
from options_advisor.indicators.greeks import calculate_greeks

logger = logging.getLogger(__name__)

MARKET_DATA_BASE_URL = "https://api.schwabapi.com/marketdata/v1"
TRADER_API_BASE_URL = "https://api.schwabapi.com/trader/v1"
DEFAULT_RISK_FREE_RATE = 0.045


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

    def get_all_share_positions(self) -> dict[str, int]:
        """Suma `longQuantity` de todas las posiciones EQUITY, a través de todas las cuentas
        vinculadas — habilita Covered Call/Collar con la tenencia REAL en vez de una tabla
        interna de seguimiento (ver strategy/selector.py::select_candidate_strategies). Una
        cuenta que falle no tumba las demás; sin cuentas legibles, {} (mismo resultado que
        MockBrokerClient, Covered Call/Collar simplemente no se ofrecen esa corrida)."""
        headers = {"Authorization": f"Bearer {self.auth.get_valid_access_token()}"}
        positions: dict[str, int] = {}
        try:
            response = self._trader_client.get("/accounts/accountNumbers", headers=headers)
            response.raise_for_status()
            accounts = response.json()
        except Exception:
            logger.exception("Fallo al listar cuentas de Schwab; sin posiciones reales esta corrida")
            return positions

        for account in accounts:
            try:
                response = self._trader_client.get(
                    f"/accounts/{account['hashValue']}", params={"fields": "positions"}, headers=headers
                )
                response.raise_for_status()
                for position in response.json().get("securitiesAccount", {}).get("positions", []):
                    instrument = position.get("instrument", {})
                    if instrument.get("assetType") != "EQUITY":
                        continue
                    symbol = instrument.get("symbol")
                    qty = int(position.get("longQuantity", 0))
                    positions[symbol] = positions.get(symbol, 0) + qty
            except Exception:
                logger.exception("Fallo al leer posiciones de la cuenta %s; se continúa con el resto", account.get("accountNumber"))

        return positions
