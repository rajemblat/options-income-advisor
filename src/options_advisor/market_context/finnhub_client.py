from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
_TIMEOUT = 10.0


def get_next_earnings_date(symbol: str, as_of: date, api_key: str | None, lookahead_days: int = 180) -> date | None:
    """Próxima fecha de earnings conocida para `symbol` a partir de `as_of`, vía Finnhub
    `/calendar/earnings`. None si no hay API key, la llamada falla, o no hay earnings
    programados dentro de `lookahead_days` — nunca rompe el pipeline (mismo patrón que el
    resto de fuentes externas: el narrador se queda sin este dato, no sin la alerta)."""
    if not api_key:
        return None
    try:
        response = httpx.get(
            f"{BASE_URL}/calendar/earnings",
            params={
                "symbol": symbol,
                "from": as_of.isoformat(),
                "to": (as_of + timedelta(days=lookahead_days)).isoformat(),
                "token": api_key,
            },
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        rows = response.json().get("earningsCalendar", [])
        dates = [date.fromisoformat(row["date"]) for row in rows if row.get("date")]
        upcoming = [d for d in dates if d >= as_of]
        return min(upcoming) if upcoming else None
    except Exception:
        logger.warning("Finnhub earnings calendar no disponible para %s; se omite este dato", symbol, exc_info=True)
        return None


def get_recent_news(symbol: str, as_of: date, api_key: str | None, lookback_days: int = 7, limit: int = 5) -> list[dict]:
    """Noticias recientes de `symbol` vía Finnhub `/company-news`. Lista vacía (nunca
    excepción) si no hay API key, falla la llamada, o no hay noticias en el rango."""
    if not api_key:
        return []
    try:
        response = httpx.get(
            f"{BASE_URL}/company-news",
            params={
                "symbol": symbol,
                "from": (as_of - timedelta(days=lookback_days)).isoformat(),
                "to": as_of.isoformat(),
                "token": api_key,
            },
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        rows = response.json()
        rows.sort(key=lambda r: r.get("datetime", 0), reverse=True)
        return [
            {
                "headline": row.get("headline"),
                "source": row.get("source"),
                "url": row.get("url"),
                "summary": row.get("summary"),
                "published_at": datetime.fromtimestamp(row["datetime"], tz=timezone.utc) if row.get("datetime") else None,
            }
            for row in rows[:limit]
        ]
    except Exception:
        logger.warning("Finnhub company news no disponible para %s; se omite este dato", symbol, exc_info=True)
        return []
