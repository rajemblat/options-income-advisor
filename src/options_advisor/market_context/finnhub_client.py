from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
_TIMEOUT = 10.0

# ── Caché y freno de rate-limit ────────────────────────────────────────────────
#
# El robot escanea ~23 símbolos por minuto y para cada uno preguntaba la fecha de earnings. Son
# ~1.400 llamadas por hora contra un plan gratis que no las aguanta: el 28/08 hubo 121 respuestas
# 429 en un día, y cada una deja al análisis sin el dato de earnings de ese símbolo.
#
# La fecha de earnings de una empresa cambia cuatro veces al año. Preguntarla cada minuto no aporta
# nada: se cachea 12 horas. Con eso pasan a ser ~23 llamadas por día.
#
# Y cuando igual llega un 429, se para de llamar por 15 minutos en vez de seguir golpeando. Insistir
# contra un límite de tasa solo alarga el bloqueo.
_CACHE_TTL_SEGUNDOS = 12 * 3600
_PAUSA_TRAS_429_SEGUNDOS = 15 * 60

_cache: dict[tuple, tuple[float, object]] = {}
_pausado_hasta: float = 0.0


def _en_pausa() -> bool:
    return time.monotonic() < _pausado_hasta


def _pausar_por_rate_limit() -> None:
    global _pausado_hasta
    _pausado_hasta = time.monotonic() + _PAUSA_TRAS_429_SEGUNDOS
    logger.warning("Finnhub devolvió 429 (límite del plan): se deja de consultar por %d minutos",
                   _PAUSA_TRAS_429_SEGUNDOS // 60)


def _de_cache(clave):
    """Valor cacheado y todavía fresco, o `_VACIO` si no hay. Se cachea también el None: "esta
    empresa no tiene earnings a la vista" es una respuesta tan válida como una fecha."""
    guardado = _cache.get(clave)
    if guardado is None:
        return _VACIO
    puesto, valor = guardado
    if time.monotonic() - puesto > _CACHE_TTL_SEGUNDOS:
        _cache.pop(clave, None)
        return _VACIO
    return valor


def _a_cache(clave, valor):
    _cache[clave] = (time.monotonic(), valor)
    return valor


class _Vacio:
    pass


_VACIO = _Vacio()


def limpiar_cache() -> None:
    """Para los tests y para forzar una relectura a mano."""
    global _pausado_hasta
    _cache.clear()
    _pausado_hasta = 0.0


def get_next_earnings_date(symbol: str, as_of: date, api_key: str | None, lookahead_days: int = 180) -> date | None:
    """Próxima fecha de earnings conocida para `symbol` a partir de `as_of`, vía Finnhub
    `/calendar/earnings`. None si no hay API key, la llamada falla, o no hay earnings
    programados dentro de `lookahead_days` — nunca rompe el pipeline (mismo patrón que el
    resto de fuentes externas: el narrador se queda sin este dato, no sin la alerta)."""
    if not api_key:
        return None
    clave = ("next_earnings", symbol, as_of.isoformat(), lookahead_days)
    guardado = _de_cache(clave)
    if not isinstance(guardado, _Vacio):
        return guardado
    if _en_pausa():
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
        if response.status_code == 429:
            _pausar_por_rate_limit()
            return None
        response.raise_for_status()
        rows = response.json().get("earningsCalendar", [])
        dates = [date.fromisoformat(row["date"]) for row in rows if row.get("date")]
        upcoming = [d for d in dates if d >= as_of]
        return _a_cache(clave, min(upcoming) if upcoming else None)
    except Exception:
        logger.warning("Finnhub earnings calendar no disponible para %s; se omite este dato", symbol, exc_info=True)
        return None


def get_symbol_earnings_dates(symbol: str, from_date: date, to_date: date, api_key: str | None) -> list[date]:
    """Fechas de earnings HISTÓRICAS de UN símbolo en [from_date, to_date], vía
    `/calendar/earnings?symbol=X&from=&to=` (mismo endpoint que `get_next_earnings_date` pero con rango
    hacia atrás y filtrado por símbolo del lado del servidor — mucho más liviano que traer el calendario
    completo, y con más chance en el plan free). [] si no hay key/datos o falla. Usado por el backtest
    de earnings (usuario 2026-08-07)."""
    if not api_key:
        return []
    try:
        response = httpx.get(
            f"{BASE_URL}/calendar/earnings",
            params={"symbol": symbol, "from": from_date.isoformat(), "to": to_date.isoformat(), "token": api_key},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        rows = response.json().get("earningsCalendar", [])
        out = []
        for row in rows:
            if row.get("date"):
                try:
                    out.append(date.fromisoformat(row["date"]))
                except (ValueError, TypeError):
                    pass
        return sorted(set(out))
    except Exception:
        logger.warning("Finnhub earnings (histórico) no disponible para %s; se omite", symbol, exc_info=True)
        return []


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
