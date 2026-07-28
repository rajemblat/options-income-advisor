from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import (
    AccountPosition,
    FilledOrder,
    FilledOrderLeg,
    Greeks,
    Mover,
    OptionChain,
    OptionContract,
    OptionType,
    PriceBar,
    Quote,
    parse_occ_option_symbol,
)
from options_advisor.broker.schwab_auth import DEFAULT_TOKEN_STORE_PATH, SchwabAuth
from options_advisor.indicators.greeks import calculate_greeks

logger = logging.getLogger(__name__)

MARKET_DATA_BASE_URL = "https://api.schwabapi.com/marketdata/v1"
TRADER_API_BASE_URL = "https://api.schwabapi.com/trader/v1"
DEFAULT_RISK_FREE_RATE = 0.045


def _parse_schwab_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse_schwab_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_filled_order(raw: dict) -> FilledOrder | None:
    """Arma un `FilledOrder` a partir de una orden cruda `status=FILLED` de `/orders` — agrega
    quantity/precio PONDERADO por `legId` a través de todas las ejecuciones de la orden (fills
    parciales en más de un evento en `orderActivityCollection`), en vez de asumir un solo
    evento. None si la orden no tiene ninguna pata de OPCIÓN con ejecución matcheada (ej. orden
    de acciones, o alguna inconsistencia de datos puntual — no debe romper el resto del poll)."""
    order_id = raw.get("orderId")
    if order_id is None:
        return None
    leg_meta = {
        leg["legId"]: leg
        for leg in raw.get("orderLegCollection", [])
        if leg.get("orderLegType") == "OPTION" and leg.get("instrument", {}).get("symbol")
    }
    if not leg_meta:
        return None

    fills: dict[int, dict] = {}
    for activity in raw.get("orderActivityCollection", []):
        for exec_leg in activity.get("executionLegs", []):
            leg_id = exec_leg.get("legId")
            if leg_id not in leg_meta:
                continue
            qty = exec_leg.get("quantity", 0.0) or 0.0
            price = exec_leg.get("price", 0.0) or 0.0
            entry = fills.setdefault(leg_id, {"qty": 0.0, "notional": 0.0, "time": None})
            entry["qty"] += qty
            entry["notional"] += qty * price
            if exec_leg.get("time"):
                entry["time"] = exec_leg["time"]

    legs: list[FilledOrderLeg] = []
    latest_time: str | None = None
    for leg_id, meta in leg_meta.items():
        fill = fills.get(leg_id)
        if not fill or fill["qty"] <= 0:
            continue
        legs.append(
            FilledOrderLeg(
                occ_symbol=meta["instrument"]["symbol"],
                instruction=meta.get("instruction", ""),
                position_effect=meta.get("positionEffect", ""),
                quantity=fill["qty"],
                price=round(fill["notional"] / fill["qty"], 4),
            )
        )
        if fill["time"] and (latest_time is None or fill["time"] > latest_time):
            latest_time = fill["time"]
    if not legs:
        return None

    fill_time = _parse_schwab_datetime(latest_time or raw.get("closeTime") or raw.get("enteredTime"))
    if fill_time is None:
        return None

    return FilledOrder(order_id=order_id, account_number=str(raw.get("accountNumber", "")), fill_time=fill_time, legs=legs)


def _classify_instrument_type(entry: dict) -> str | None:
    """"stock" | "etf" | "index" a partir de `assetMainType`/`assetSubType` — ya vienen en la
    MISMA respuesta de `/quotes` que ya se pide (`entry`, el dict completo por símbolo, no
    `entry["quote"]"), confirmado en vivo 2026-07-27: SPY -> EQUITY/ETF, AAPL -> EQUITY/COE,
    $RUT -> INDEX/None. Sección 'Pestaña Screener', filtro de tipo de instrumento — no hace
    falta pedir `/instruments` aparte, el dato ya estaba disponible sin usar."""
    main_type = entry.get("assetMainType")
    if main_type == "INDEX":
        return "index"
    if main_type == "EQUITY":
        return "etf" if entry.get("assetSubType") == "ETF" else "stock"
    return None


def _parse_quote_change_fields(quote: dict) -> dict:
    """`netChange`/`netPercentChange` reflejan el precio "actual" (incluye pre/after-market si
    hay sesión extendida en curso) vs. el cierre anterior. `postMarketChange`/
    `postMarketPercentChange` son el movimiento ADICIONAL específicamente después del cierre
    regular — ausentes (no `None` explícito, la clave no está) fuera de esa sesión, de ahí el
    `.get()` en vez de indexar directo."""
    return {
        "net_change": quote.get("netChange", 0.0),
        "net_change_pct": quote.get("netPercentChange", 0.0),
        "post_market_change_pct": quote.get("postMarketPercentChange"),
    }


def _parse_mover(item: dict) -> Mover:
    """Normaliza una fila del endpoint `/movers` — bug real encontrado 2026-07-27 corriendo
    contra la API en vivo CON el mercado abierto (la verificación anterior, 2026-07-25, cayó
    con el mercado cerrado y `screeners: []`, así que este parseo nunca se había ejercitado
    contra una fila real): Schwab devuelve `lastPrice`/`netChange`/`netPercentChange`, NO
    `last`/`change`/`direction` como asumía este código antes — esos tres siempre caían al
    default (0.0/0.0/"up"), de ahí el +0.00%/$0.00 en todos los símbolos que reportó el
    usuario. `netPercentChange` viene como fracción acá (ej. -0.0512 = -5.12%), a diferencia
    del mismo campo en `/quotes` (`_parse_quote`, ya en %) — confirmado con la matemática real
    de un ítem en vivo (netChange=-10.6 sobre lastPrice=196.24 ≈ -0.0512 de fracción)."""
    net_percent_change = item.get("netPercentChange", 0.0) * 100
    direction = "up" if net_percent_change >= 0 else "down"
    return Mover(
        symbol=item["symbol"],
        description=item.get("description", ""),
        last_price=item.get("lastPrice", 0.0),
        change_pct=net_percent_change,
        direction=direction,
        total_volume=item.get("totalVolume", 0),
    )


def _parse_next_ex_dividend_date(fundamental: dict) -> date | None:
    """`divExDate` de Schwab NO es siempre la fecha futura — probado en vivo con dos símbolos
    reales el mismo día (2026-07-24): JNJ tenía `divExDate=2026-08-25` (futura) y
    `nextDivExDate=2026-11-25` (del ciclo siguiente); QQQ tenía `divExDate=2026-06-22` (YA
    PASADA) y `nextDivExDate=2026-09-22` (la próxima real). Parece depender de si ya se pagó el
    dividendo del ciclo actual o no en el momento de la consulta, no de un orden fijo. Por eso
    se toma la fecha MÁS PRÓXIMA que sea hoy o futura entre los dos campos, no un campo fijo.
    None para símbolos sin dividendo (ETFs apalancados, la mayoría de las acciones de
    crecimiento, índices) o si ninguno de los dos campos da una fecha futura."""
    today = date.today()
    candidates = [
        d for d in (_parse_schwab_date(fundamental.get("divExDate")), _parse_schwab_date(fundamental.get("nextDivExDate"))) if d and d >= today
    ]
    return min(candidates) if candidates else None


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
        entry = data[symbol]
        quote = entry["quote"]
        last_price = quote["lastPrice"]
        return Quote(
            symbol=symbol,
            as_of=date.today(),
            last_price=last_price,
            # Los índices ($SPX, $RUT, $NDX, $VIX, etc.) no son instrumentos operables — no
            # tienen bid/ask, solo lastPrice. Sin esos campos, se usa lastPrice para ambos en
            # vez de fallar (no hay spread real que reportar para un índice).
            bid=quote.get("bidPrice", last_price),
            ask=quote.get("askPrice", last_price),
            next_ex_dividend_date=_parse_next_ex_dividend_date(entry.get("fundamental") or {}),
            instrument_type=_classify_instrument_type(entry),
            **_parse_quote_change_fields(quote),
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
            last_price = quote["lastPrice"]
            quotes[symbol] = Quote(
                symbol=symbol,
                as_of=date.today(),
                last_price=last_price,
                bid=quote.get("bidPrice", last_price),  # índices sin bid/ask, ver get_quote()
                ask=quote.get("askPrice", last_price),
                next_ex_dividend_date=_parse_next_ex_dividend_date(entry.get("fundamental") or {}),
                instrument_type=_classify_instrument_type(entry),
                **_parse_quote_change_fields(quote),
            )
        return quotes

    def get_movers(self, index: str, sort: str, frequency: int = 0) -> list[Mover]:
        try:
            data = self._get(f"/movers/{index}", params={"sort": sort, "frequency": frequency})
        except Exception:
            logger.exception("Fallo al pedir movers de Schwab para %s; se omite", index)
            return []
        return [_parse_mover(item) for item in data.get("screeners", [])]

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
            option_fields = parse_occ_option_symbol(symbol) if instrument.get("assetType") == "OPTION" else None
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

    def get_recent_filled_orders(self, since: datetime) -> list[FilledOrder]:
        """Órdenes LLENADAS (`status=FILLED`) desde `since` en todas las cuentas vinculadas —
        reemplaza el diff de posiciones/promedio blendeado de la Fase 1 anterior (Sección
        'rediseño de Operaciones vía /orders', 2026-07-28): cada orden trae el fill EXACTO de
        cada pata (`orderActivityCollection[].executionLegs[].price`), y una orden con una
        pata OPENING + una CLOSING en la MISMA orden es un roll detectable con certeza, sin
        heurística de ventana temporal entre corridas. `since` debe ser timezone-aware (UTC) —
        Schwab lo exige así en `fromEnteredTime`. Ventana angosta recomendada (no días): pedir
        varios días de una sola vez es lento (timeout real observado pidiendo 3 días, ~260
        órdenes) — se espera que el caller pida algo como los últimos ~15 min con margen sobre
        la cadencia del cron, no un historial largo."""
        headers = {"Authorization": f"Bearer {self.auth.get_valid_access_token()}"}
        from_str = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        to_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        try:
            response = self._trader_client.get("/accounts/accountNumbers", headers=headers)
            response.raise_for_status()
            accounts = response.json()
        except Exception:
            logger.exception("Fallo al listar cuentas de Schwab; sin órdenes esta corrida")
            return []

        orders: list[FilledOrder] = []
        for account in accounts:
            try:
                response = self._trader_client.get(
                    f"/accounts/{account['hashValue']}/orders",
                    params={"fromEnteredTime": from_str, "toEnteredTime": to_str, "status": "FILLED"},
                    headers=headers,
                )
                response.raise_for_status()
                for raw_order in response.json():
                    parsed = _parse_filled_order(raw_order)
                    if parsed:
                        orders.append(parsed)
            except Exception:
                logger.exception("Fallo al leer órdenes de la cuenta %s; se continúa con el resto", account.get("accountNumber"))
        return orders

    # Rango de precio y liquidez razonables para vender prima con capital manejable — evita
    # penny stocks (spreads horribles) y nombres de $1000+ (100 acciones de colateral inviables).
    _SCREEN_MIN_PRICE = 10.0
    _SCREEN_MAX_PRICE = 800.0
    _SCREEN_MIN_AVG_VOLUME = 300_000
    _SCREEN_BATCH_SIZE = 200  # probado en vivo hasta 100+ sin problema; margen de seguridad
    _SCREEN_MAX_SHORTLIST = 60  # tope duro: la Fase 2 (cadena+Finnhub+Claude) es cara por símbolo

    def screen_universe(self, symbols: list[str], max_shortlist: int = _SCREEN_MAX_SHORTLIST) -> list[str]:
        """Fase 1 barata: 1 llamada batch cada 200 símbolos (no una cadena de opciones por
        símbolo) — optionable, precio y volumen promedio razonables. Entre los que pasan el
        filtro, rankea por rango 52 semanas / precio (proxy gratis de volatilidad histórica,
        mismos datos del batch, sin llamada extra) y devuelve solo los primeros
        `max_shortlist` — sin esto, un universo de cientos de large-caps líquidos casi no se
        reduce (probado: 385 -> 355 solo con el filtro de liquidez), y la Fase 2 se vuelve
        impagable. Usa el JSON crudo de Schwab directo (no el Quote genérico) porque necesita
        optionable/volumen/rango 52 semanas, específicos de este screen."""
        candidates: list[tuple[str, float]] = []  # (symbol, volatility_proxy)
        headers = {"Authorization": f"Bearer {self.auth.get_valid_access_token()}"}
        for i in range(0, len(symbols), self._SCREEN_BATCH_SIZE):
            batch = symbols[i : i + self._SCREEN_BATCH_SIZE]
            try:
                response = self._client.get(
                    "/quotes", params={"symbols": ",".join(batch)}, headers=headers
                )
                response.raise_for_status()
                data = response.json()
            except Exception:
                logger.exception("Fallo al screenear un lote de %d símbolos; se omite ese lote", len(batch))
                continue

            for symbol, entry in data.items():
                quote = entry.get("quote", {})
                reference = entry.get("reference", {})
                fundamental = entry.get("fundamental", {})
                price = quote.get("lastPrice", 0) or 0
                avg_volume = fundamental.get("avg10DaysVolume", 0) or 0
                if not (
                    reference.get("optionable")
                    and self._SCREEN_MIN_PRICE <= price <= self._SCREEN_MAX_PRICE
                    and avg_volume >= self._SCREEN_MIN_AVG_VOLUME
                ):
                    continue
                high_52w, low_52w = quote.get("52WeekHigh"), quote.get("52WeekLow")
                volatility_proxy = (high_52w - low_52w) / price if high_52w and low_52w and price else 0.0
                candidates.append((symbol, volatility_proxy))

        candidates.sort(key=lambda pair: pair[1], reverse=True)
        return [symbol for symbol, _ in candidates[:max_shortlist]]
