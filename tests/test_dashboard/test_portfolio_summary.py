from __future__ import annotations

import math

from options_advisor.dashboard.portfolio_summary import summarize_portfolio


def test_empty_rows_returns_zeroed_summary():
    summary = summarize_portfolio([])
    assert summary["total_alerts"] == 0
    assert summary["by_strategy"] == {}
    assert summary["directional"] == {"bullish": 0, "bearish": 0, "neutral": 0, "unknown": 0}
    assert summary["net_delta"] is None
    assert summary["bounded_risk_total"] is None
    assert summary["unbounded_risk_count"] == 0


def test_counts_by_strategy():
    rows = [
        {"strategy_type": "cash_secured_put", "delta": 0.3, "max_loss": 500.0},
        {"strategy_type": "cash_secured_put", "delta": 0.2, "max_loss": 400.0},
        {"strategy_type": "iron_condor", "delta": 0.0, "max_loss": 300.0},
    ]
    summary = summarize_portfolio(rows)
    assert summary["by_strategy"] == {"cash_secured_put": 2, "iron_condor": 1}
    assert summary["total_alerts"] == 3


def test_directional_classification_uses_neutral_band():
    rows = [
        {"strategy_type": "a", "delta": 0.10, "max_loss": 100.0},   # bullish
        {"strategy_type": "b", "delta": -0.10, "max_loss": 100.0},  # bearish
        {"strategy_type": "c", "delta": 0.03, "max_loss": 100.0},   # neutral, dentro de la banda
        {"strategy_type": "d", "delta": -0.05, "max_loss": 100.0},  # neutral, límite exacto de la banda
    ]
    summary = summarize_portfolio(rows)
    assert summary["directional"] == {"bullish": 1, "bearish": 1, "neutral": 2, "unknown": 0}
    assert math.isclose(summary["net_delta"], 0.10 - 0.10 + 0.03 - 0.05)


def test_unknown_delta_counted_separately():
    rows = [{"strategy_type": "a", "delta": None, "max_loss": 100.0}]
    summary = summarize_portfolio(rows)
    assert summary["directional"]["unknown"] == 1
    assert summary["net_delta"] is None


def test_unbounded_risk_excluded_from_bounded_total():
    rows = [
        {"strategy_type": "short_put_naked", "delta": 0.4, "max_loss": float("inf")},
        {"strategy_type": "cash_secured_put", "delta": 0.3, "max_loss": 500.0},
    ]
    summary = summarize_portfolio(rows)
    assert summary["bounded_risk_total"] == 500.0
    assert summary["unbounded_risk_count"] == 1


def test_risk_unknown_when_max_loss_missing_for_all():
    rows = [{"strategy_type": "a", "delta": 0.1, "max_loss": None}]
    summary = summarize_portfolio(rows)
    assert summary["bounded_risk_total"] is None
    assert summary["unbounded_risk_count"] == 0
