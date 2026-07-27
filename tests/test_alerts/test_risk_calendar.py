from __future__ import annotations

from datetime import date

from options_advisor.alerts.risk_calendar import (
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    build_proactive_risk_warnings,
    build_risk_calendar,
    is_high_risk_event_day,
)

TODAY = date(2026, 7, 23)


def test_fomc_event_classified_as_high_risk_from_keyword():
    events = build_risk_calendar(
        upcoming_events=[{"date": "2026-07-29", "event": "Decisión de tasas de la Fed (FOMC)", "impact": None}],
        earnings_by_symbol={},
        today=TODAY,
    )
    assert len(events) == 1
    assert events[0]["risk_level"] == RISK_HIGH


def test_cpi_event_classified_as_high_even_if_finnhub_marks_medium():
    events = build_risk_calendar(
        upcoming_events=[{"date": "2026-08-01", "event": "CPI YoY", "impact": "medium"}],
        earnings_by_symbol={},
        today=TODAY,
    )
    assert events[0]["risk_level"] == RISK_HIGH


def test_generic_medium_impact_event_classified_as_medium():
    events = build_risk_calendar(
        upcoming_events=[{"date": "2026-08-01", "event": "Retail Sales", "impact": "medium"}],
        earnings_by_symbol={},
        today=TODAY,
    )
    assert events[0]["risk_level"] == RISK_MEDIUM


def test_low_impact_event_classified_as_low():
    events = build_risk_calendar(
        upcoming_events=[{"date": "2026-08-01", "event": "Building Permits", "impact": "low"}],
        earnings_by_symbol={},
        today=TODAY,
    )
    assert events[0]["risk_level"] == RISK_LOW


def test_earnings_within_window_classified_as_medium():
    events = build_risk_calendar(
        upcoming_events=[],
        earnings_by_symbol={"AAPL": date(2026, 7, 30)},
        today=TODAY,
    )
    assert len(events) == 1
    assert events[0] == {"date": date(2026, 7, 30), "kind": "earnings", "label": "Earnings de AAPL", "symbol": "AAPL", "risk_level": RISK_MEDIUM}


def test_earnings_outside_lookahead_window_excluded():
    events = build_risk_calendar(
        upcoming_events=[],
        earnings_by_symbol={"AAPL": date(2026, 12, 1)},
        today=TODAY,
        lookahead_days=30,
    )
    assert events == []


def test_earnings_without_known_date_excluded():
    events = build_risk_calendar(upcoming_events=[], earnings_by_symbol={"AAPL": None}, today=TODAY)
    assert events == []


def test_events_sorted_chronologically():
    events = build_risk_calendar(
        upcoming_events=[{"date": "2026-08-05", "event": "FOMC", "impact": "high"}],
        earnings_by_symbol={"AAPL": date(2026, 7, 25)},
        today=TODAY,
    )
    assert [e["date"] for e in events] == [date(2026, 7, 25), date(2026, 8, 5)]


def test_malformed_event_date_skipped():
    events = build_risk_calendar(
        upcoming_events=[{"date": "not-a-date", "event": "broken"}],
        earnings_by_symbol={},
        today=TODAY,
    )
    assert events == []


# --- is_high_risk_event_day (bloqueo de candidatos nuevos en días de CPI/NFP/FOMC) ---


def test_is_high_risk_event_day_true_for_cpi_today():
    events = [{"date": TODAY.isoformat(), "event": "CPI YoY", "impact": "medium"}]
    assert is_high_risk_event_day(events, TODAY) is True


def test_is_high_risk_event_day_false_for_medium_impact_event_today():
    events = [{"date": TODAY.isoformat(), "event": "Retail Sales", "impact": "medium"}]
    assert is_high_risk_event_day(events, TODAY) is False


def test_is_high_risk_event_day_false_when_event_is_not_today():
    events = [{"date": "2026-08-05", "event": "FOMC", "impact": "high"}]
    assert is_high_risk_event_day(events, TODAY) is False


def test_is_high_risk_event_day_false_without_events():
    assert is_high_risk_event_day([], TODAY) is False


def test_is_high_risk_event_day_ignores_malformed_dates():
    assert is_high_risk_event_day([{"date": "not-a-date", "event": "CPI"}], TODAY) is False


# --- build_proactive_risk_warnings ---


def test_proactive_warning_fires_two_days_before_high_risk_event():
    events = [{"date": (TODAY.replace(day=TODAY.day + 2)).isoformat(), "event": "Nonfarm Payrolls", "impact": "high"}]
    warnings = build_proactive_risk_warnings(events, TODAY)
    assert len(warnings) == 1
    assert warnings[0]["days_until"] == 2
    assert warnings[0]["label"] == "Nonfarm Payrolls"


def test_proactive_warning_fires_one_day_before_high_risk_event():
    events = [{"date": (TODAY.replace(day=TODAY.day + 1)).isoformat(), "event": "FOMC", "impact": "high"}]
    warnings = build_proactive_risk_warnings(events, TODAY)
    assert warnings[0]["days_until"] == 1


def test_proactive_warning_ignores_events_outside_the_1_or_2_day_window():
    events = [{"date": (TODAY.replace(day=TODAY.day + 5)).isoformat(), "event": "FOMC", "impact": "high"}]
    assert build_proactive_risk_warnings(events, TODAY) == []


def test_proactive_warning_ignores_non_high_risk_events_even_in_window():
    events = [{"date": (TODAY.replace(day=TODAY.day + 1)).isoformat(), "event": "Retail Sales", "impact": "medium"}]
    assert build_proactive_risk_warnings(events, TODAY) == []
