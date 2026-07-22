"""Genera fixtures deterministas para MockBrokerClient: ~400 días de price_history e
iv_history por símbolo, más una plantilla de cadena de opciones. Reproducible (semilla
derivada del ticker), así que correrlo de nuevo regenera los mismos datos salvo que
cambie la fecha de anclaje (hoy).

Uso: python scripts/seed_fixtures.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(PROJECT_ROOT / "src"))

from options_advisor.config import load_symbols  # noqa: E402

HISTORY_DAYS = 400
ETFS = {"SPY", "QQQ", "IWM"}

# Precio base aproximado y volatilidad anualizada por símbolo (valores ilustrativos, no cotizaciones reales).
SYMBOL_PROFILE = {
    "SPY": {"base_price": 620.0, "annual_vol": 0.14, "base_iv": 0.13, "drift": 0.08},
    "QQQ": {"base_price": 540.0, "annual_vol": 0.19, "base_iv": 0.19, "drift": 0.10},
    "IWM": {"base_price": 220.0, "annual_vol": 0.21, "base_iv": 0.22, "drift": 0.06},
    "AAPL": {"base_price": 230.0, "annual_vol": 0.24, "base_iv": 0.25, "drift": 0.09},
    "MSFT": {"base_price": 470.0, "annual_vol": 0.22, "base_iv": 0.23, "drift": 0.10},
    "NVDA": {"base_price": 145.0, "annual_vol": 0.45, "base_iv": 0.48, "drift": 0.15},
    "AMZN": {"base_price": 210.0, "annual_vol": 0.28, "base_iv": 0.29, "drift": 0.10},
    "GOOGL": {"base_price": 195.0, "annual_vol": 0.26, "base_iv": 0.27, "drift": 0.09},
    "META": {"base_price": 640.0, "annual_vol": 0.32, "base_iv": 0.33, "drift": 0.11},
    "JPM": {"base_price": 260.0, "annual_vol": 0.20, "base_iv": 0.21, "drift": 0.07},
    "KO": {"base_price": 68.0, "annual_vol": 0.14, "base_iv": 0.15, "drift": 0.04},
    "JNJ": {"base_price": 155.0, "annual_vol": 0.15, "base_iv": 0.16, "drift": 0.03},
    "PG": {"base_price": 165.0, "annual_vol": 0.15, "base_iv": 0.16, "drift": 0.04},
}

DEFAULT_PROFILE = {"base_price": 100.0, "annual_vol": 0.25, "base_iv": 0.27, "drift": 0.08}

DTE_BUCKETS = [7, 14, 21, 30, 45, 60]
STRIKE_OFFSETS = [-0.10, -0.05, -0.025, 0.0, 0.025, 0.05, 0.10]


def _seeded_random(symbol: str) -> random.Random:
    seed = int(hashlib.md5(symbol.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed)


def _trading_dates(end: date, n_days: int) -> list[date]:
    dates = []
    d = end
    while len(dates) < n_days:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    return list(reversed(dates))


def generate_price_history(symbol: str, profile: dict, rng: random.Random, dates: list[date]) -> list[dict]:
    price = profile["base_price"]
    daily_sigma = profile["annual_vol"] / math.sqrt(252)
    daily_mu = profile["drift"] / 252
    rows = []
    for d in dates:
        z = rng.gauss(0, 1)
        price = max(1.0, price * math.exp((daily_mu - 0.5 * daily_sigma**2) + daily_sigma * z))
        open_ = price * (1 + rng.uniform(-0.003, 0.003))
        close = price
        high = max(open_, close) * (1 + abs(rng.uniform(0, 0.006)))
        low = min(open_, close) * (1 - abs(rng.uniform(0, 0.006)))
        volume = rng.randint(3_000_000, 9_000_000) if symbol in ETFS else rng.randint(800_000, 5_000_000)
        rows.append(
            {
                "date": d.isoformat(),
                "open": round(open_, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": volume,
            }
        )
    return rows


def generate_iv_history(profile: dict, rng: random.Random, dates: list[date]) -> list[dict]:
    base_iv = profile["base_iv"]
    phase = rng.uniform(0, 2 * math.pi)
    period_days = rng.uniform(60, 120)
    rows = []
    for i, d in enumerate(dates):
        cycle = 0.35 * base_iv * math.sin(2 * math.pi * i / period_days + phase)
        noise = rng.gauss(0, base_iv * 0.05)
        iv = min(0.9, max(0.06, base_iv + cycle + noise))
        rows.append({"date": d.isoformat(), "iv_atm": round(iv, 4)})
    return rows


def generate_chain_template(symbol: str) -> dict:
    spread_pct = 0.012 if symbol in ETFS else 0.025
    contracts = []
    for dte in DTE_BUCKETS:
        for offset in STRIKE_OFFSETS:
            iv_skew = -offset * 0.15  # skew típico: puts OTM (offset negativo) algo más caras
            for option_type in ("call", "put"):
                contracts.append(
                    {
                        "dte": dte,
                        "strike_offset_pct": offset,
                        "iv_skew": round(iv_skew, 4),
                        "option_type": option_type,
                    }
                )
    return {"symbol": symbol, "bid_ask_spread_pct": spread_pct, "contracts": contracts}


def main() -> None:
    symbols = load_symbols()
    fixtures_dir = PROJECT_ROOT / "data" / "fixtures"
    anchor = date.today()
    dates = _trading_dates(anchor, HISTORY_DAYS)

    for symbol in symbols:
        profile = SYMBOL_PROFILE.get(symbol, DEFAULT_PROFILE)
        rng = _seeded_random(symbol)

        price_rows = generate_price_history(symbol, profile, rng, dates)
        price_path = fixtures_dir / "price_history" / f"{symbol}.csv"
        with open(price_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            writer.writerows(price_rows)

        iv_rows = generate_iv_history(profile, rng, dates)
        iv_path = fixtures_dir / "iv_history" / f"{symbol}.csv"
        with open(iv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "iv_atm"])
            writer.writeheader()
            writer.writerows(iv_rows)

        chain_path = fixtures_dir / "option_chains" / f"{symbol}.json"
        with open(chain_path, "w") as f:
            json.dump(generate_chain_template(symbol), f, indent=2)

        print(f"OK  {symbol}: {len(price_rows)} días de precio/IV + plantilla de cadena de opciones")

    print(f"\nFixtures generadas en {fixtures_dir} (ancla: {anchor.isoformat()})")


if __name__ == "__main__":
    main()
