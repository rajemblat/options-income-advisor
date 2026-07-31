from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal

import pandas_market_calendars as mcal

_NYSE = mcal.get_calendar("NYSE")

MarketSession = Literal["pre-market", "abierto", "after-hours", "cerrado"]

# Pre-market: 4:00am ET, 5h30 antes de la apertura regular (9:30am ET). After-hours: hasta las
# 8:00pm ET, 4h después del cierre regular (4:00pm ET). `market_open`/`market_close` de
# pandas_market_calendars ya vienen en UTC ajustados por horario de verano, así que sumar/restar
# un timedelta fijo sobre esos valores da la hora ET correcta sin manejar DST a mano.
_PRE_MARKET_START_OFFSET = timedelta(hours=5, minutes=30)
_AFTER_HOURS_END_OFFSET = timedelta(hours=4)


def is_market_day(day: date) -> bool:
    schedule = _NYSE.schedule(start_date=day, end_date=day)
    return not schedule.empty


def session_bounds(session_date: date) -> tuple[datetime, datetime] | None:
    """Apertura/cierre REGULAR (UTC, ya ajustado por horario de verano vía
    pandas_market_calendars) de la sesión de `session_date` — usado para pedir barras
    intradía a Schwab con un rango explícito en vez de su parámetro `period` (confirmado en
    vivo 2026-07-31 que `period` de `/pricehistory` no siempre incluye la sesión de HOY, un
    rango `startDate`/`endDate` explícito sí). None si `session_date` no es día hábil."""
    schedule = _NYSE.schedule(start_date=session_date, end_date=session_date)
    if schedule.empty:
        return None
    return (
        schedule.iloc[0]["market_open"].to_pydatetime(),
        schedule.iloc[0]["market_close"].to_pydatetime(),
    )


def latest_trading_session(now: datetime | None = None) -> date:
    """Fecha de la sesión más reciente que ya arrancó respecto a `now` (UTC) — hoy si el
    mercado ya abrió hoy (en curso o cerrado), el día hábil anterior si hoy todavía no abrió
    (antes de pre-market) o no es día hábil. Default del gráfico de velas intradía cuando no
    se pide una sesión explícita."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    schedule = _NYSE.schedule(start_date=now.date() - timedelta(days=10), end_date=now.date())
    last_open = schedule.iloc[-1]["market_open"].to_pydatetime()
    if last_open <= now:
        return schedule.index[-1].date()
    return schedule.index[-2].date()


def market_session(now: datetime | None = None) -> MarketSession:
    """Sesión de mercado actual (para el indicador estilo CNBC en la página principal). `now`
    en UTC (con o sin tzinfo — naive se asume UTC); por defecto la hora actual."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    schedule = _NYSE.schedule(start_date=now.date(), end_date=now.date())
    if schedule.empty:
        return "cerrado"
    market_open = schedule.iloc[0]["market_open"].to_pydatetime()
    market_close = schedule.iloc[0]["market_close"].to_pydatetime()
    if market_open <= now < market_close:
        return "abierto"
    if market_open - _PRE_MARKET_START_OFFSET <= now < market_open:
        return "pre-market"
    if market_close <= now < market_close + _AFTER_HOURS_END_OFFSET:
        return "after-hours"
    return "cerrado"
