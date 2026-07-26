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
