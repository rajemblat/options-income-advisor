from __future__ import annotations

import csv
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from py_vollib.black_scholes_merton import black_scholes_merton

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import (
    AccountPosition,
    FilledOrder,
    Greeks,
    IntradayBar,
    Mover,
    OptionChain,
    OptionContract,
    OptionType,
    PriceBar,
    Quote,
)
from options_advisor.indicators.greeks import calculate_greeks
from options_advisor.scheduler.market_calendar import session_bounds

DEFAULT_RISK_FREE_RATE = 0.045


def _next_weekday(d: date) -> date:
    while d.weekday() >= 5:  # 5=sábado, 6=domingo
        d += timedelta(days=1)
    return d


def _synthetic_intraday_closes(rng: random.Random, day_bar: PriceBar, n: int) -> list[float]:
    """`n` precios de cierre sintéticos que van de `day_bar.open` a `day_bar.close` (última
    barra siempre exacta), con ruido gaussiano acotado al rango real del día — un paseo
    aleatorio simple, no un modelo de mercado, solo para tener forma de vela plausible en modo
    mock."""
    closes = []
    for i in range(1, n + 1):
        target = day_bar.open + (day_bar.close - day_bar.open) * (i / n)
        noise = rng.gauss(0, max(day_bar.high - day_bar.low, 0.01) * 0.15)
        closes.append(min(max(target + noise, day_bar.low), day_bar.high))
    closes[-1] = day_bar.close
    return closes


class MockBrokerClient(BrokerClient):
    """Implementación de BrokerClient sobre fixtures locales, para desarrollar y testear
    todo el pipeline sin depender de las credenciales de Schwab (ver Sección 3 del plan de Fase 1).

    - price_history/{SYMBOL}.csv y iv_history/{SYMBOL}.csv son series históricas fijas.
    - option_chains/{SYMBOL}.json es una plantilla relativa (strikes/IV/expiración expresados
      como offsets) que se resuelve contra el precio/IV del `as_of_date` actual, permitiendo
      simular el avance de días con set_as_of_date() sin necesitar un fixture por fecha.
    """

    def __init__(self, fixtures_dir: Path, risk_free_rate: float = DEFAULT_RISK_FREE_RATE):
        self.fixtures_dir = Path(fixtures_dir)
        self.risk_free_rate = risk_free_rate
        self._price_history_cache: dict[str, list[PriceBar]] = {}
        self._iv_history_cache: dict[str, dict[date, float]] = {}
        self._chain_template_cache: dict[str, dict] = {}
        self._as_of_date: date | None = None

    def set_as_of_date(self, as_of_date: date) -> None:
        self._as_of_date = as_of_date

    def is_authenticated(self) -> bool:
        return True

    def get_all_share_positions(self) -> dict[str, int]:
        return {}  # sin cuentas reales en modo mock — Covered Call/Collar nunca se habilitan acá

    def get_all_positions(self) -> list[AccountPosition]:
        return []  # sin cuentas reales en modo mock

    def get_recent_filled_orders(self, since: datetime) -> list[FilledOrder]:
        return []  # sin cuentas reales en modo mock

    def screen_universe(self, symbols: list[str], max_shortlist: int = 60) -> list[str]:
        return list(symbols)  # sin datos reales de mercado para filtrar/rankear en modo mock

    # -- carga de fixtures -------------------------------------------------

    def _load_price_history(self, symbol: str) -> list[PriceBar]:
        if symbol not in self._price_history_cache:
            path = self.fixtures_dir / "price_history" / f"{symbol}.csv"
            bars = []
            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    bars.append(
                        PriceBar(
                            symbol=symbol,
                            trade_date=date.fromisoformat(row["date"]),
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=int(row["volume"]),
                        )
                    )
            bars.sort(key=lambda b: b.trade_date)
            self._price_history_cache[symbol] = bars
        return self._price_history_cache[symbol]

    def _load_iv_history(self, symbol: str) -> dict[date, float]:
        if symbol not in self._iv_history_cache:
            path = self.fixtures_dir / "iv_history" / f"{symbol}.csv"
            history = {}
            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    history[date.fromisoformat(row["date"])] = float(row["iv_atm"])
            self._iv_history_cache[symbol] = history
        return self._iv_history_cache[symbol]

    def _load_chain_template(self, symbol: str) -> dict:
        if symbol not in self._chain_template_cache:
            path = self.fixtures_dir / "option_chains" / f"{symbol}.json"
            with open(path) as f:
                self._chain_template_cache[symbol] = json.load(f)
        return self._chain_template_cache[symbol]

    def _resolve_as_of_date(self, symbol: str) -> date:
        if self._as_of_date is not None:
            return self._as_of_date
        history = self._load_price_history(symbol)
        if not history:
            raise ValueError(f"No hay price_history fixture para {symbol}")
        return history[-1].trade_date

    def _price_on_or_before(self, symbol: str, as_of_date: date) -> PriceBar:
        history = self._load_price_history(symbol)
        eligible = [b for b in history if b.trade_date <= as_of_date]
        if not eligible:
            raise ValueError(f"No hay precio disponible para {symbol} en o antes de {as_of_date}")
        return eligible[-1]

    def _iv_atm_on_or_before(self, symbol: str, as_of_date: date) -> float:
        history = self._load_iv_history(symbol)
        eligible_dates = [d for d in history if d <= as_of_date]
        if not eligible_dates:
            raise ValueError(f"No hay IV histórica disponible para {symbol} en o antes de {as_of_date}")
        return history[max(eligible_dates)]

    # -- interfaz BrokerClient ----------------------------------------------

    def get_quote(self, symbol: str) -> Quote:
        as_of_date = self._resolve_as_of_date(symbol)
        bar = self._price_on_or_before(symbol, as_of_date)
        spread = round(bar.close * 0.0005, 2)
        history = [b for b in self._load_price_history(symbol) if b.trade_date < bar.trade_date]
        net_change = round(bar.close - history[-1].close, 4) if history else 0.0
        net_change_pct = round(net_change / history[-1].close * 100, 4) if history else 0.0
        return Quote(
            symbol=symbol,
            as_of=as_of_date,
            last_price=bar.close,
            bid=round(bar.close - spread, 2),
            ask=round(bar.close + spread, 2),
            net_change=net_change,
            net_change_pct=net_change_pct,
            # Sin sesión extendida simulada en las fixtures — el modo mock no tiene datos de
            # pre/after-market, a diferencia del cierre regular (arriba, sí calculado).
            post_market_change_pct=None,
            total_volume=bar.volume,
        )

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        # Market Movers (pedido 2026-07-29) cotiza en batch universos grandes (S&P 500/Nasdaq-
        # 100/Dow 30 completos) que en modo mock casi seguro no tienen fixture propia — a
        # diferencia de la watchlist chica de siempre, folerar símbolos sin datos acá (se
        # omiten) en vez de que uno solo tire abajo todo el batch.
        quotes: dict[str, Quote] = {}
        for symbol in symbols:
            try:
                quotes[symbol] = self.get_quote(symbol)
            except Exception:
                continue
        return quotes

    def get_movers(self, index: str, sort: str, frequency: int = 0) -> list[Mover]:
        """Sin datos reales de mercado en modo mock — un puñado de filas representativas fijas
        para poder desarrollar/ver la sección de Market Movers sin credenciales de Schwab."""
        canned = [
            Mover(symbol="NVDA", description="NVIDIA Corp", last_price=185.32, change_pct=4.71, direction="up", total_volume=62_000_000),
            Mover(symbol="TSLA", description="Tesla Inc", last_price=298.10, change_pct=3.85, direction="up", total_volume=88_000_000),
            Mover(symbol="AAPL", description="Apple Inc", last_price=241.55, change_pct=1.92, direction="up", total_volume=45_000_000),
            Mover(symbol="INTC", description="Intel Corp", last_price=27.84, change_pct=-6.32, direction="down", total_volume=71_000_000),
            Mover(symbol="BA", description="Boeing Co", last_price=178.20, change_pct=-3.14, direction="down", total_volume=12_000_000),
        ]
        if sort == "PERCENT_CHANGE_DOWN":
            return sorted([m for m in canned if m.direction == "down"], key=lambda m: m.change_pct)
        if sort == "VOLUME":
            return sorted(canned, key=lambda m: m.total_volume, reverse=True)
        return sorted([m for m in canned if m.direction == "up"], key=lambda m: m.change_pct, reverse=True)

    def get_price_history(self, symbol: str, lookback_days: int) -> list[PriceBar]:
        as_of_date = self._resolve_as_of_date(symbol)
        history = [b for b in self._load_price_history(symbol) if b.trade_date <= as_of_date]
        return history[-lookback_days:]

    def get_intraday_bars(
        self, symbol: str, session_date: date, interval_minutes: int = 1
    ) -> list[IntradayBar]:
        # Sin datos intradía reales en modo mock (las fixtures son diarias) — sintetiza barras
        # deterministas (seed = símbolo+fecha+intervalo) que respetan el O/H/L/C real del día,
        # suficiente para desarrollar/testear el gráfico de velas sin depender de Schwab.
        bounds = session_bounds(session_date)
        day_bar = next(
            (b for b in self._load_price_history(symbol) if b.trade_date == session_date), None
        )
        if bounds is None or day_bar is None:
            return []
        market_open, market_close = bounds
        n_bars = int((market_close - market_open).total_seconds() // 60 // interval_minutes)
        if n_bars <= 0:
            return []
        rng = random.Random(f"{symbol}-{session_date}-{interval_minutes}")
        closes = _synthetic_intraday_closes(rng, day_bar, n_bars)
        day_range = max(day_bar.high - day_bar.low, 0.01)
        bars = []
        prev_close = day_bar.open
        ts = market_open
        for close in closes:
            bar_open = prev_close
            bar_high = min(max(bar_open, close) + rng.uniform(0, day_range * 0.02), day_bar.high)
            bar_low = max(min(bar_open, close) - rng.uniform(0, day_range * 0.02), day_bar.low)
            volume = max(1, int(rng.gauss(day_bar.volume / n_bars, day_bar.volume / n_bars * 0.3)))
            bars.append(
                IntradayBar(
                    symbol=symbol,
                    timestamp=ts,
                    open=round(bar_open, 4),
                    high=round(bar_high, 4),
                    low=round(bar_low, 4),
                    close=round(close, 4),
                    volume=volume,
                )
            )
            prev_close = close
            ts += timedelta(minutes=interval_minutes)
        return bars

    def get_option_chain(
        self, symbol: str, expiration_range_days: tuple[int, int] = (7, 60)
    ) -> OptionChain:
        as_of_date = self._resolve_as_of_date(symbol)
        underlying_price = self._price_on_or_before(symbol, as_of_date).close
        iv_atm = self._iv_atm_on_or_before(symbol, as_of_date)
        template = self._load_chain_template(symbol)
        spread_pct = template["bid_ask_spread_pct"]

        min_days, max_days = expiration_range_days
        contracts: list[OptionContract] = []
        for spec in template["contracts"]:
            dte = spec["dte"]
            if not (min_days <= dte <= max_days):
                continue
            expiration = _next_weekday(as_of_date + timedelta(days=dte))
            strike = round(underlying_price * (1 + spec["strike_offset_pct"]), 1)
            iv = max(0.01, iv_atm + spec["iv_skew"])
            option_type: OptionType = spec["option_type"]

            flag = "c" if option_type == "call" else "p"
            t = max((expiration - as_of_date).days / 365.0, 1 / 365)
            theoretical_price = black_scholes_merton(
                flag, underlying_price, strike, t, self.risk_free_rate, iv, 0.0
            )
            half_spread = max(0.01, round(theoretical_price * spread_pct / 2, 2))

            greeks: Greeks = calculate_greeks(
                option_type=option_type,
                underlying_price=underlying_price,
                strike=strike,
                expiration=expiration,
                as_of_date=as_of_date,
                implied_volatility=iv,
                risk_free_rate=self.risk_free_rate,
            )

            distance_pct = abs(spec["strike_offset_pct"])
            open_interest = max(50, round(5000 * math.exp(-6 * distance_pct)))

            contracts.append(
                OptionContract(
                    symbol=symbol,
                    option_type=option_type,
                    strike=strike,
                    expiration=expiration,
                    bid=round(max(0.01, theoretical_price - half_spread), 2),
                    ask=round(theoretical_price + half_spread, 2),
                    last_price=round(theoretical_price, 2),
                    implied_volatility=round(iv, 4),
                    open_interest=open_interest,
                    volume=max(1, open_interest // 10),
                    greeks=greeks,
                )
            )

        return OptionChain(
            symbol=symbol,
            as_of=as_of_date,
            underlying_price=underlying_price,
            contracts=contracts,
        )
