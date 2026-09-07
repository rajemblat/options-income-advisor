from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

from options_advisor.market_context import fred_client

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
_TIMEOUT = 10.0

# El 403 de Finnhub NO es un error a investigar: es el plan free diciendo que `/calendar/economic`
# es premium. Ya está previsto y el fallback (FOMC + FRED) cubre lo mismo. Pero se logueaba como
# WARNING con traceback completo en CADA corrida: en el log del 2026-09-03 hay decenas de esos
# tracebacks idénticos, y ese ruido es justamente lo que hace que un error de verdad pase
# desapercibido. Se avisa UNA vez por proceso y después silencio.
#
# Cualquier otro fallo (timeout, 500, red caída) SÍ se sigue logueando cada vez y con detalle:
# esos son transitorios y pueden significar algo.
_aviso_403_dado = False

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


def get_upcoming_macro_events(
    finnhub_api_key: str | None, fred_api_key: str | None, as_of: date, lookahead_days: int = 30
) -> list[dict]:
    """Próximos eventos macro relevantes (FOMC, CPI, empleo, PBI) en los próximos
    `lookahead_days`. Intenta primero Finnhub `/calendar/economic` (el más completo cuando el
    plan lo incluye). Si no responde o el endpoint no está disponible en el plan actual
    (confirmado 403 en el plan free al momento de escribir esto), arma el calendario con dos
    fuentes gratis: el calendario oficial de reuniones FOMC (`_FOMC_MEETING_DATES_FALLBACK`) +
    fechas exactas de publicación de CPI/empleo/PBI vía FRED `/release/dates`
    (`fred_client.get_upcoming_release_dates`) — cubre lo mismo que pedía el plan original sin
    depender de un endpoint pago."""
    if finnhub_api_key:
        try:
            response = httpx.get(
                f"{BASE_URL}/calendar/economic",
                params={
                    "from": as_of.isoformat(),
                    "to": (as_of + timedelta(days=lookahead_days)).isoformat(),
                    "token": finnhub_api_key,
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            rows = response.json().get("economicCalendar", [])
            # Incluye impacto "low" también (Sección 'Eventos de riesgo' punto #1): el filtro
            # anterior (solo high/medium) escondía eventos reales del panorama completo; el
            # nivel de riesgo bajo ya se refleja visualmente en risk_calendar._classify_macro_event.
            relevant = [r for r in rows if r.get("country") == "US" and r.get("impact") in ("high", "medium", "low")]
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
        except httpx.HTTPStatusError as exc:
            global _aviso_403_dado
            if exc.response is not None and exc.response.status_code in (401, 403):
                if not _aviso_403_dado:
                    _aviso_403_dado = True
                    logger.info(
                        "Finnhub: /calendar/economic no está incluido en el plan (HTTP %s). Es lo "
                        "esperado en el plan free — el calendario se arma con FOMC + FRED, que "
                        "cubre lo mismo. No se vuelve a avisar en esta corrida.",
                        exc.response.status_code,
                    )
            else:
                logger.warning("Finnhub economic calendar devolvió HTTP %s; se arma el calendario "
                               "con FOMC + FRED",
                               exc.response.status_code if exc.response is not None else "?")
        except Exception:
            logger.warning("Finnhub economic calendar no disponible; se arma el calendario con FOMC + FRED", exc_info=True)

    events = _fomc_dates_fallback(as_of, lookahead_days) + fred_client.get_upcoming_release_dates(
        fred_api_key, as_of, lookahead_days
    )
    return sorted(events, key=lambda e: e["date"])
