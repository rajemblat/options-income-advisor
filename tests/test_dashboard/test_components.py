from __future__ import annotations

from datetime import date

from options_advisor.broker.models import Mover
from options_advisor.dashboard.components import (
    CRITICAL,
    GOOD,
    WARNING,
    _capital_at_risk_caveat_html,
    _historical_move_caveat_html,
    _similar_move_caveat_html,
    classify_volatility_level,
    filter_by_date_range,
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


# --- _historical_move_caveat_html ("check histórico", pedido 2026-07-28) ---


def test_historical_move_caveat_empty_without_data():
    assert _historical_move_caveat_html(None, None, 45) == ""


def test_historical_move_caveat_empty_without_total_windows():
    assert _historical_move_caveat_html(0, 0, 45) == ""  # total_windows=0 no debería persistirse, pero por las dudas


def test_historical_move_caveat_zero_occurrences_shows_green_check():
    html = _historical_move_caveat_html(0, 1250, 45)
    assert "Nunca tocó este nivel" in html
    assert GOOD in html


def test_historical_move_caveat_nonzero_occurrences_shows_simple_count():
    html = _historical_move_caveat_html(8, 1250, 45)
    assert "el precio tocó este nivel 8 veces" in html
    assert "ventanas" not in html
    assert "%" not in html
    assert WARNING in html


def test_historical_move_caveat_singular_for_one_occurrence():
    html = _historical_move_caveat_html(1, 1250, 45)
    assert "el precio tocó este nivel 1 vez" in html
    assert "1 veces" not in html


def test_historical_move_caveat_always_clarifies_its_historical_not_a_guarantee():
    """Aclaración explícita pedida por el usuario — tiene que aparecer en AMBOS casos (0
    ocurrencias y con ocurrencias), no solo en uno."""
    assert "no garantiza el futuro" in _historical_move_caveat_html(0, 1250, 45)
    assert "no garantiza el futuro" in _historical_move_caveat_html(8, 1250, 45)
    assert "histórico" in _historical_move_caveat_html(0, 1250, 45)
    assert "histórico" in _historical_move_caveat_html(8, 1250, 45)


# --- _similar_move_caveat_html (refinamiento del check histórico, pedido 2026-07-29: banda de
# tolerancia de plazo/magnitud, mostrado APARTE del badge de arriba, no en su reemplazo) ---


def test_similar_move_caveat_empty_without_data():
    assert _similar_move_caveat_html(None, None) == ""


def test_similar_move_caveat_zero_shows_green_check():
    html = _similar_move_caveat_html(0, 0)
    assert GOOD in html
    assert "pasó 0 veces" in html


def test_similar_move_caveat_shows_similar_count():
    html = _similar_move_caveat_html(4, 0)
    assert "pasó 4 veces" in html
    assert WARNING in html
    assert "AÚN MÁS GRANDE" not in html  # sin crashes mayores, no debe mencionarlos


def test_similar_move_caveat_singular_for_one_similar_occurrence():
    html = _similar_move_caveat_html(1, 0)
    assert "pasó 1 vez" in html
    assert "1 veces" not in html


def test_similar_move_caveat_mentions_bigger_crashes_when_present():
    """El escenario más peligroso (crashes más grandes que la banda "similar") no debe quedar
    escondido — pedido explícito del usuario tras la aclaración de que la banda de tolerancia
    deja afuera los crashes grandes."""
    html = _similar_move_caveat_html(2, 3)
    assert "pasó 2 veces" in html
    assert "hubo 3 veces" in html
    assert "AÚN MÁS GRANDE" in html


def test_similar_move_caveat_singular_for_one_bigger_occurrence():
    html = _similar_move_caveat_html(0, 1)
    assert "hubo 1 vez" in html
    assert "1 veces una caída" not in html


def test_similar_move_caveat_always_clarifies_its_historical_not_a_guarantee():
    assert "no garantiza el futuro" in _similar_move_caveat_html(0, 0)
    assert "no garantiza el futuro" in _similar_move_caveat_html(4, 2)
    assert "histórico" in _similar_move_caveat_html(0, 0)
    assert "histórico" in _similar_move_caveat_html(4, 2)


def test_similar_move_caveat_mentions_tolerance_bands():
    html = _similar_move_caveat_html(1, 0)
    assert "±3 puntos" in html
    assert "±7 días" in html


# --- filter_by_date_range (filtro de rango compartido por Operaciones y Alertas, 2026-07-29) ---


def _trade(trade_date: str, symbol: str = "TST") -> dict:
    return {"trade_date": trade_date, "symbol": symbol}


TODAY = date(2026, 7, 29)


def test_filter_by_date_range_todo_returns_everything_unfiltered():
    trades = [_trade("2026-07-29"), _trade("2026-01-01"), _trade("2020-05-05")]
    assert filter_by_date_range(trades, "Todo", TODAY) == trades


def test_filter_by_date_range_hoy_excludes_previous_days():
    trades = [_trade("2026-07-29"), _trade("2026-07-28")]
    result = filter_by_date_range(trades, "Hoy", TODAY)
    assert result == [_trade("2026-07-29")]


def test_filter_by_date_range_ultima_semana_boundary_is_inclusive():
    """7 días exactos atrás debe quedar INCLUIDO (cutoff = today - 7, comparación >=)."""
    trades = [_trade("2026-07-22"), _trade("2026-07-21")]
    result = filter_by_date_range(trades, "Última semana", TODAY)
    assert result == [_trade("2026-07-22")]


def test_filter_by_date_range_ultimos_15_dias():
    trades = [_trade("2026-07-14"), _trade("2026-07-13")]
    result = filter_by_date_range(trades, "Últimos 15 días", TODAY)
    assert result == [_trade("2026-07-14")]


def test_filter_by_date_range_ultimo_mes():
    trades = [_trade("2026-06-29"), _trade("2026-06-28")]
    result = filter_by_date_range(trades, "Último mes", TODAY)
    assert result == [_trade("2026-06-29")]


def test_filter_by_date_range_empty_input_returns_empty():
    assert filter_by_date_range([], "Hoy", TODAY) == []


def test_filter_by_date_range_preserves_original_order():
    """No debe reordenar — `trades` ya viene ordenado DESC por trade_ts desde el repo."""
    trades = [_trade("2026-07-29", "B"), _trade("2026-07-29", "A"), _trade("2026-07-28", "C")]
    result = filter_by_date_range(trades, "Hoy", TODAY)
    assert [t["symbol"] for t in result] == ["B", "A"]


def test_filter_by_date_range_supports_a_different_date_field():
    """Pestaña Alertas reusa esta misma función pasando `date_field="alert_date"` en vez del
    default `"trade_date"` de Operaciones — ver 1_alertas.py."""
    alerts = [{"alert_date": "2026-07-29", "symbol": "A"}, {"alert_date": "2026-07-28", "symbol": "B"}]
    result = filter_by_date_range(alerts, "Hoy", TODAY, date_field="alert_date")
    assert result == [{"alert_date": "2026-07-29", "symbol": "A"}]
