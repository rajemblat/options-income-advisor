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


def get_earnings_calendar_range(from_date: date, to_date: date, api_key: str | None) -> list[dict]:
    """Earnings de TODAS las empresas (no un símbolo puntual) publicados en [from_date, to_date]
    — mismo endpoint que `get_next_earnings_date` pero SIN el filtro `symbol`, Finnhub devuelve
    el calendario completo en una sola llamada. Usado por el selector de rango de fechas de
    Eventos de riesgo (Sección 'Calendario de earnings', pedido 2026-07-26) para el modo
    "universo amplio" — evita pedir earnings símbolo por símbolo (cientos de llamadas) cuando
    se quiere ver todo lo que reporta en una ventana, no solo la watchlist. [] si no hay API key
    o falla la llamada (mismo criterio que el resto del módulo: nunca rompe el caller)."""
    if not api_key:
        return []
    try:
        response = httpx.get(
            f"{BASE_URL}/calendar/earnings",
            params={"from": from_date.isoformat(), "to": to_date.isoformat(), "token": api_key},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        rows = response.json().get("earningsCalendar", [])
        return sorted(
            (
                {"symbol": row["symbol"], "date": row["date"], "hour": row.get("hour")}
                for row in rows
                if row.get("symbol") and row.get("date")
            ),
            key=lambda r: (r["date"], r["symbol"]),
        )
    except Exception:
        logger.warning("Finnhub earnings calendar (rango completo) no disponible; se omite", exc_info=True)
        return []


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
