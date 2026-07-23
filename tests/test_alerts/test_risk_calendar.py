from __future__ import annotations

from datetime import date

from options_advisor.alerts.risk_calendar import RISK_HIGH, RISK_LOW, RISK_MEDIUM, build_risk_calendar

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
