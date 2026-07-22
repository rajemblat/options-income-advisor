from __future__ import annotations

import csv
import json
import math
from datetime import date, timedelta
from pathlib import Path

from py_vollib.black_scholes_merton import black_scholes_merton

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import (
    Greeks,
    OptionChain,
    OptionContract,
    OptionType,
    PriceBar,
    Quote,
)
from options_advisor.indicators.greeks import calculate_greeks

DEFAULT_RISK_FREE_RATE = 0.045


def _next_weekday(d: date) -> date:
    while d.weekday() >= 5:  # 5=sábado, 6=domingo
        d += timedelta(days=1)
    return d


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
        return Quote(
            symbol=symbol,
            as_of=as_of_date,
            last_price=bar.close,
            bid=round(bar.close - spread, 2),
            ask=round(bar.close + spread, 2),
        )

    def get_price_history(self, symbol: str, lookback_days: int) -> list[PriceBar]:
        as_of_date = self._resolve_as_of_date(symbol)
        history = [b for b in self._load_price_history(symbol) if b.trade_date <= as_of_date]
        return history[-lookback_days:]

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
