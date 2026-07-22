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
DEFAULT_RISK_FREE_RATE = 0.045


class SchwabBrokerClient(BrokerClient):
    """Implementación real de BrokerClient contra la Schwab Trader API.

    IMPORTANTE: no verificada todavía contra la API en vivo — el acceso está pendiente de
    aprobación (ver Sección 7.2 y "Riesgos" del plan de Fase 1). El mapeo de campos de la
    cadena de opciones (nombres exactos que devuelve /marketdata/v1/chains) está hecho de
    buena fe según la documentación pública de Schwab; hay que correr
    scripts/verify_schwab_client.py apenas lleguen las credenciales y ajustar lo que no
    coincida antes de confiar en esto para alertas reales.
    """

    def __init__(self, auth: SchwabAuth, risk_free_rate: float = DEFAULT_RISK_FREE_RATE):
        self.auth = auth
        self.risk_free_rate = risk_free_rate
        self._client = httpx.Client(base_url=MARKET_DATA_BASE_URL, timeout=15.0)

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
        contracts = []
        for option_type, exp_map_key in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
            for _exp_key, strikes in data.get(exp_map_key, {}).items():
                for _strike_key, contract_list in strikes.items():
                    for raw in contract_list:
                        contracts.append(self._parse_contract(symbol, option_type, raw, underlying_price, today))

        return OptionChain(symbol=symbol, as_of=today, underlying_price=underlying_price, contracts=contracts)

    def _parse_contract(
        self, symbol: str, option_type: OptionType, raw: dict, underlying_price: float, as_of: date
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
                risk_free_rate=self.risk_free_rate,
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
