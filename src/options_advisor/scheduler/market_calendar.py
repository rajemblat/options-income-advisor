from __future__ import annotations

from datetime import date

import pandas_market_calendars as mcal

_NYSE = mcal.get_calendar("NYSE")


def is_market_day(day: date) -> bool:
    schedule = _NYSE.schedule(start_date=day, end_date=day)
    return not schedule.empty
