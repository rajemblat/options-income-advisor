from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas_market_calendars as mcal
import pytest

from options_advisor.scheduler.market_calendar import latest_trading_session, market_session, session_bounds

_NYSE = mcal.get_calendar("NYSE")


def _next_trading_day_schedule(start: date):
    """Primer día hábil de NYSE a partir de `start` (inclusive) — evita hardcodear una fecha
    que podría caer en feriado y romper el test con el tiempo."""
    d = start
    for _ in range(10):
        schedule = _NYSE.schedule(start_date=d, end_date=d)
        if not schedule.empty:
            return schedule.iloc[0]
        d += timedelta(days=1)
    raise AssertionError("No se encontró un día hábil de NYSE en el rango probado")


@pytest.fixture
def schedule_row():
    return _next_trading_day_schedule(date(2026, 7, 27))


def test_market_session_open_during_regular_hours(schedule_row):
    market_open = schedule_row["market_open"].to_pydatetime()
    now = market_open + timedelta(minutes=30)
    assert market_session(now) == "abierto"


def test_market_session_open_right_at_open(schedule_row):
    market_open = schedule_row["market_open"].to_pydatetime()
    assert market_session(market_open) == "abierto"


def test_market_session_pre_market_before_open(schedule_row):
    market_open = schedule_row["market_open"].to_pydatetime()
    now = market_open - timedelta(minutes=30)
    assert market_session(now) == "pre-market"


def test_market_session_after_hours_after_close(schedule_row):
    market_close = schedule_row["market_close"].to_pydatetime()
    now = market_close + timedelta(minutes=30)
    assert market_session(now) == "after-hours"


def test_market_session_closed_at_market_close(schedule_row):
    market_close = schedule_row["market_close"].to_pydatetime()
    assert market_session(market_close) == "after-hours"  # límite inclusive del lado after-hours


def test_market_session_closed_middle_of_night(schedule_row):
    market_open = schedule_row["market_open"].to_pydatetime()
    now = market_open - timedelta(hours=8)
    assert market_session(now) == "cerrado"


def test_market_session_closed_long_after_after_hours(schedule_row):
    market_close = schedule_row["market_close"].to_pydatetime()
    now = market_close + timedelta(hours=6)
    assert market_session(now) == "cerrado"


def test_market_session_closed_on_weekend():
    saturday = datetime(2026, 7, 25, 15, 0, tzinfo=timezone.utc)
    assert market_session(saturday) == "cerrado"


def test_market_session_naive_datetime_assumed_utc(schedule_row):
    market_open = schedule_row["market_open"].to_pydatetime()
    now_naive = market_open.replace(tzinfo=None) + timedelta(minutes=5)
    assert market_session(now_naive) == "abierto"


def test_market_session_defaults_to_current_time_when_now_omitted():
    assert market_session() in ("pre-market", "abierto", "after-hours", "cerrado")


# -- session_bounds / latest_trading_session (gráfico de velas intradía, 2026-07-31) -------


def test_session_bounds_matches_nyse_schedule(schedule_row):
    session_date = schedule_row.name.date()
    bounds = session_bounds(session_date)
    assert bounds == (
        schedule_row["market_open"].to_pydatetime(),
        schedule_row["market_close"].to_pydatetime(),
    )


def test_session_bounds_none_on_weekend():
    assert session_bounds(date(2026, 8, 1)) is None  # sábado


def test_latest_trading_session_is_today_when_already_open(schedule_row):
    session_date = schedule_row.name.date()
    market_open = schedule_row["market_open"].to_pydatetime()
    assert latest_trading_session(market_open + timedelta(minutes=1)) == session_date


def test_latest_trading_session_is_today_after_close(schedule_row):
    session_date = schedule_row.name.date()
    market_close = schedule_row["market_close"].to_pydatetime()
    assert latest_trading_session(market_close + timedelta(hours=2)) == session_date


def test_latest_trading_session_rolls_back_before_todays_open(schedule_row):
    market_open = schedule_row["market_open"].to_pydatetime()
    previous_session = latest_trading_session(market_open - timedelta(minutes=1))
    assert previous_session < schedule_row.name.date()


def test_latest_trading_session_skips_weekend():
    saturday = datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)
    result = latest_trading_session(saturday)
    assert result.weekday() < 5
    assert result <= date(2026, 7, 31)
