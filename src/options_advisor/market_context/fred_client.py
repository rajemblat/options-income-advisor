from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"
_TIMEOUT = 10.0

# release_id de FRED (no series_id) para el calendario oficial de publicación de cada dato —
# distinto del valor en sí (get_macro_snapshot). Finnhub `/calendar/economic` requiere el plan
# pago (verificado con la key real: 403), así que estas son la única fuente gratis con fecha
# exacta de "cuándo sale el próximo CPI/empleo/PBI", no solo "cuál fue el último valor".
_RELEASE_LABELS = {
    10: ("Publicación de CPI (inflación)", "high"),
    50: ("Reporte de empleo (Nonfarm Payrolls)", "high"),
    53: ("Publicación de PBI (GDP)", "medium"),
}

# Series de FRED (Federal Reserve Economic Data) usadas como referencia macro — todas ya son
# la cifra "final" publicada por la fuente oficial, sin cálculos propios encima (Sección de
# variables: "otros indicadores económicos relevantes").
SERIES_FED_FUNDS_UPPER = "DFEDTARU"  # límite superior del rango objetivo vigente
SERIES_FED_FUNDS_LOWER = "DFEDTARL"  # límite inferior
SERIES_CPI_YOY = "CPALTT01USM659N"  # inflación interanual (CPI), ya calculada por FRED
SERIES_UNEMPLOYMENT = "UNRATE"  # tasa de desempleo
SERIES_GDP_GROWTH = "A191RL1Q225SBEA"  # crecimiento del PBI real, trimestral anualizado


def _latest_observation(series_id: str, api_key: str | None) -> tuple[float, date] | None:
    """(valor, fecha_del_dato) de la observación más reciente — la fecha es la que FRED asocia
    a esa observación (ej. el mes que mide el CPI publicado), no la fecha en que corrimos el
    job. Usada para el simulador de inflación (Sección 'Perfil y Simulación' 2026-07-26): la
    tasa de inflación se toma siempre de acá, sin edición manual, así que hay que poder mostrar
    de qué fecha es el dato."""
    if not api_key:
        return None
    try:
        response = httpx.get(
            BASE_URL,
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            },
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        observations = response.json().get("observations", [])
        if not observations or observations[0]["value"] == ".":  # FRED usa "." para dato faltante
            return None
        return float(observations[0]["value"]), date.fromisoformat(observations[0]["date"])
    except Exception:
        logger.warning("FRED serie %s no disponible; se omite este dato", series_id, exc_info=True)
        return None


def _latest_value(series_id: str, api_key: str | None) -> float | None:
    observation = _latest_observation(series_id, api_key)
    return observation[0] if observation else None


def get_fed_funds_target_range(api_key: str | None) -> tuple[float, float] | None:
    """(límite_inferior, límite_superior) del rango objetivo de la tasa de fondos federales
    vigente. None si no hay API key o falla la consulta."""
    upper = _latest_value(SERIES_FED_FUNDS_UPPER, api_key)
    lower = _latest_value(SERIES_FED_FUNDS_LOWER, api_key)
    if upper is None or lower is None:
        return None
    return (lower, upper)


def get_macro_snapshot(api_key: str | None) -> dict:
    """CPI interanual, desempleo y crecimiento del PBI más recientes. Cualquier serie no
    disponible queda en None — nunca rompe el llamador; es un dict plano listo para el
    contexto del narrador (Sección 6.2, nunca cifras inventadas).

    `cpi_yoy_date` es la fecha que FRED asocia al dato de CPI (el mes que mide), no la fecha
    de hoy — la usa el simulador de inflación para mostrar de qué dato sale la tasa prellenada,
    sin que el usuario tenga que editarla a mano."""
    cpi = _latest_observation(SERIES_CPI_YOY, api_key)
    return {
        "cpi_yoy_pct": cpi[0] if cpi else None,
        "cpi_yoy_date": cpi[1] if cpi else None,
        "unemployment_rate_pct": _latest_value(SERIES_UNEMPLOYMENT, api_key),
        "gdp_growth_annualized_pct": _latest_value(SERIES_GDP_GROWTH, api_key),
    }


def get_upcoming_release_dates(api_key: str | None, as_of: date, lookahead_days: int = 30) -> list[dict]:
    """Próximas fechas oficiales de publicación (CPI, empleo, PBI) vía FRED `/release/dates`
    — a diferencia de `get_macro_snapshot` (el ÚLTIMO valor ya publicado), esto da CUÁNDO sale
    el PRÓXIMO dato, con fecha exacta del calendario oficial BLS/BEA que FRED espeja. Una
    consulta que falla no tumba las demás — cada release se resuelve independiente."""
    if not api_key:
        return []
    horizon = as_of + timedelta(days=lookahead_days)
    events: list[dict] = []
    for release_id, (label, impact) in _RELEASE_LABELS.items():
        try:
            response = httpx.get(
                RELEASE_DATES_URL,
                params={
                    "release_id": release_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "asc",
                    "include_release_dates_with_no_data": "true",
                    "realtime_start": as_of.isoformat(),
                    "realtime_end": horizon.isoformat(),
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            for row in response.json().get("release_dates", []):
                release_date = row.get("date")
                if release_date and as_of.isoformat() <= release_date <= horizon.isoformat():
                    events.append({"date": release_date, "event": label, "country": "US", "impact": impact})
        except Exception:
            logger.warning("FRED release dates no disponible para release_id=%s; se omite", release_id, exc_info=True)
    return events
