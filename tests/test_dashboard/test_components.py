from __future__ import annotations

from options_advisor.broker.models import Mover
from options_advisor.dashboard.components import (
    CRITICAL,
    GOOD,
    WARNING,
    _capital_at_risk_caveat_html,
    classify_volatility_level,
    split_gainers_losers,
)


def _mover(symbol: str, change_pct: float) -> Mover:
    direction = "up" if change_pct >= 0 else "down"
    return Mover(symbol=symbol, description=symbol, last_price=100.0, change_pct=change_pct, direction=direction, total_volume=1000)


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


# --- classify_volatility_level (semáforo de volatilidad basado en VIX) ---


def test_classify_volatility_low_below_15():
    label, color = classify_volatility_level(12.3)
    assert label == "Volatilidad baja"
    assert color == GOOD


def test_classify_volatility_normal_between_15_and_25():
    label, color = classify_volatility_level(18.5)
    assert label == "Volatilidad normal"
    assert color == WARNING


def test_classify_volatility_high_above_25():
    label, color = classify_volatility_level(31.0)
    assert label == "Volatilidad alta"
    assert color == CRITICAL


def test_classify_volatility_boundary_exactly_15_is_normal_not_low():
    label, _ = classify_volatility_level(15.0)
    assert label == "Volatilidad normal"


def test_classify_volatility_boundary_exactly_25_is_alta_not_normal():
    label, _ = classify_volatility_level(25.0)
    assert label == "Volatilidad alta"


# --- split_gainers_losers (bug real 2026-07-28: mismas empresas en Ganadoras Y Perdedoras) ---


def test_split_gainers_losers_separates_by_sign():
    movers = [_mover("NVDA", -0.46), _mover("MDT", 2.68), _mover("GLW", -15.28), _mover("PYPL", 0.55)]
    gainers, losers = split_gainers_losers(movers)
    assert [m.symbol for m in gainers] == ["MDT", "PYPL"]
    assert [m.symbol for m in losers] == ["GLW", "NVDA"]


def test_split_gainers_losers_no_symbol_appears_in_both():
    movers = [_mover("A", 1.0), _mover("B", -1.0), _mover("C", 5.0), _mover("D", -5.0)]
    gainers, losers = split_gainers_losers(movers)
    assert set(m.symbol for m in gainers).isdisjoint(m.symbol for m in losers)


def test_split_gainers_losers_gainers_sorted_descending():
    movers = [_mover("A", 1.0), _mover("B", 5.0), _mover("C", 2.5)]
    gainers, _ = split_gainers_losers(movers)
    assert [m.symbol for m in gainers] == ["B", "C", "A"]


def test_split_gainers_losers_losers_sorted_most_negative_first():
    movers = [_mover("A", -1.0), _mover("B", -5.0), _mover("C", -2.5)]
    _, losers = split_gainers_losers(movers)
    assert [m.symbol for m in losers] == ["B", "C", "A"]


def test_split_gainers_losers_zero_change_excluded_from_both():
    movers = [_mover("FLAT", 0.0), _mover("UP", 1.0), _mover("DOWN", -1.0)]
    gainers, losers = split_gainers_losers(movers)
    assert "FLAT" not in [m.symbol for m in gainers]
    assert "FLAT" not in [m.symbol for m in losers]


def test_split_gainers_losers_empty_input():
    assert split_gainers_losers([]) == ([], [])
