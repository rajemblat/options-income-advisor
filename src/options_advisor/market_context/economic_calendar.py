from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
_TIMEOUT = 10.0

# Fallback de bajo mantenimiento si /calendar/economic de Finnhub no está disponible en el
# plan (Finnhub mueve endpoints entre free/premium con frecuencia): calendario oficial de
# reuniones FOMC publicado por la Fed (federalreserve.gov/monetarypolicy/fomccalendars.htm),
# 8 reuniones/año anunciadas con mucha anticipación — no requiere una API para esto.
_FOMC_MEETING_DATES_FALLBACK = [
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9),
    date(2027, 7, 28), date(2027, 9, 15), date(2027, 10, 27), date(2027, 12, 8),
]


def _fomc_dates_fallback(as_of: date, lookahead_days: int) -> list[dict]:
    horizon = as_of + timedelta(days=lookahead_days)
    return [
        {"date": d.isoformat(), "event": "Decisión de tasas de la Fed (FOMC)", "country": "US", "impact": "high"}
        for d in _FOMC_MEETING_DATES_FALLBACK
        if as_of <= d <= horizon
    ]


def get_upcoming_macro_events(api_key: str | None, as_of: date, lookahead_days: int = 30) -> list[dict]:
    """Próximos eventos macro relevantes (FOMC, CPI, empleo, PBI) en los próximos
    `lookahead_days`, vía Finnhub `/calendar/economic`. Si Finnhub no responde o el endpoint
    no está disponible en el plan actual, cae al calendario oficial de reuniones FOMC (ver
    `_FOMC_MEETING_DATES_FALLBACK`) — cubre menos eventos pero nunca deja el dato en blanco
    para lo más importante de esta lista."""
    if api_key:
        try:
            response = httpx.get(
                f"{BASE_URL}/calendar/economic",
                params={
                    "from": as_of.isoformat(),
                    "to": (as_of + timedelta(days=lookahead_days)).isoformat(),
                    "token": api_key,
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            rows = response.json().get("economicCalendar", [])
            relevant = [r for r in rows if r.get("country") == "US" and r.get("impact") in ("high", "medium")]
            if relevant:
                return sorted(
                    (
                        {
                            "date": r.get("date"),
                            "event": r.get("event"),
                            "country": r.get("country"),
                            "impact": r.get("impact"),
                            "actual": r.get("actual"),
                            "estimate": r.get("estimate"),
                            "prev": r.get("prev"),
                        }
                        for r in relevant
                    ),
                    key=lambda e: e["date"],
                )
        except Exception:
            logger.warning("Finnhub economic calendar no disponible; se usa el calendario FOMC de respaldo", exc_info=True)

    return _fomc_dates_fallback(as_of, lookahead_days)
