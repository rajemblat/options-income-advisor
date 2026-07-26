from __future__ import annotations

from options_advisor.dashboard.components import _capital_at_risk_caveat_html


def test_capital_at_risk_critical_when_max_loss_exceeds_capital():
    html = _capital_at_risk_caveat_html(590_000.0, 50_000.0)
    assert "Esta posición sola arriesga $590,000.00" in html
    assert "1180%" in html
    assert "Muy por encima de tu tamaño de cuenta" in html


def test_capital_at_risk_warning_above_quarter_of_capital():
    html = _capital_at_risk_caveat_html(15_000.0, 50_000.0)
    assert "Riesgo real" in html
    assert "30%" in html


def test_capital_at_risk_empty_when_small_relative_to_capital():
    assert _capital_at_risk_caveat_html(500.0, 50_000.0) == ""


def test_capital_at_risk_empty_for_unbounded_loss():
    assert _capital_at_risk_caveat_html(float("inf"), 50_000.0) == ""


def test_capital_at_risk_empty_without_capital_available():
    assert _capital_at_risk_caveat_html(590_000.0, None) == ""
    assert _capital_at_risk_caveat_html(590_000.0, 0.0) == ""


def test_capital_at_risk_empty_without_max_loss():
    assert _capital_at_risk_caveat_html(None, 50_000.0) == ""
