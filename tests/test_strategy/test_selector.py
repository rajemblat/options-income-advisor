from __future__ import annotations

from options_advisor.strategy import constants as c
from options_advisor.strategy.selector import select_candidate_strategies


def test_iv_rank_none_returns_no_candidates():
    assert select_candidate_strategies(iv_rank=None, risk_level="moderado") == []


def test_high_iv_rank_conservador_gets_defined_risk_only():
    candidates = select_candidate_strategies(iv_rank=70, risk_level="conservador")
    assert c.CASH_SECURED_PUT in candidates
    assert c.BULL_PUT_SPREAD in candidates
    assert c.IRON_CONDOR in candidates
    assert c.SHORT_PUT_NAKED not in candidates  # riesgo indefinido, no habilitado para conservador


def test_high_iv_rank_agresivo_gets_naked_put():
    candidates = select_candidate_strategies(iv_rank=70, risk_level="agresivo")
    assert c.SHORT_PUT_NAKED in candidates


def test_low_iv_rank_neutral_bias_returns_calendar_diagonal_and_debit_spreads():
    candidates = select_candidate_strategies(iv_rank=20, risk_level="moderado")
    assert set(candidates) == {
        c.CALENDAR_PUT_SPREAD,
        c.CALENDAR_CALL_SPREAD,
        c.DIAGONAL_PUT_SPREAD,
        c.DIAGONAL_CALL_SPREAD,
        c.BULL_CALL_SPREAD,
        c.BEAR_PUT_SPREAD,
        c.CALL_RATIO_BACKSPREAD,
    }


def test_covered_call_and_collar_only_appear_with_open_assigned_position():
    without_position = select_candidate_strategies(iv_rank=70, risk_level="moderado", has_open_assigned_position=False)
    with_position = select_candidate_strategies(iv_rank=70, risk_level="moderado", has_open_assigned_position=True)
    assert c.COVERED_CALL not in without_position
    assert c.COLLAR not in without_position
    assert c.COVERED_CALL in with_position
    assert c.COLLAR in with_position


def test_iv_rank_exactly_at_threshold_counts_as_high():
    candidates = select_candidate_strategies(iv_rank=50, risk_level="conservador")
    assert c.CASH_SECURED_PUT in candidates


def test_golden_cross_bias_excludes_bearish_strategies():
    candidates = select_candidate_strategies(iv_rank=70, risk_level="agresivo", ma_cross_signal="golden_cross_8_20")
    assert c.CASH_SECURED_PUT in candidates
    assert c.BULL_PUT_SPREAD in candidates
    assert c.SHORT_CALL_NAKED not in candidates
    assert c.BEAR_CALL_SPREAD not in candidates
    assert c.IRON_CONDOR not in candidates  # solo aparece con sesgo neutral


def test_death_cross_bias_excludes_bullish_strategies():
    candidates = select_candidate_strategies(iv_rank=70, risk_level="agresivo", ma_cross_signal="death_cross_50_200")
    assert c.SHORT_CALL_NAKED in candidates
    assert c.BEAR_CALL_SPREAD in candidates
    assert c.CASH_SECURED_PUT not in candidates
    assert c.BULL_PUT_SPREAD not in candidates


def test_overbought_rsi_without_ma_cross_leans_bearish():
    candidates = select_candidate_strategies(iv_rank=70, risk_level="agresivo", rsi=75.0)
    assert c.BEAR_CALL_SPREAD in candidates
    assert c.BULL_PUT_SPREAD not in candidates


def test_ma_cross_takes_priority_over_rsi():
    # RSI sobrecomprado sugeriría bajista, pero un golden cross manda primero.
    candidates = select_candidate_strategies(iv_rank=70, risk_level="agresivo", ma_cross_signal="golden_cross_8_20", rsi=75.0)
    assert c.BULL_PUT_SPREAD in candidates
    assert c.BEAR_CALL_SPREAD not in candidates


def test_ratio_spreads_only_for_agresivo():
    moderado = select_candidate_strategies(iv_rank=70, risk_level="moderado")
    agresivo = select_candidate_strategies(iv_rank=70, risk_level="agresivo")
    assert c.CALL_RATIO_SPREAD not in moderado
    assert c.PUT_RATIO_SPREAD not in moderado
    assert c.CALL_RATIO_SPREAD in agresivo
    assert c.PUT_RATIO_SPREAD in agresivo


def test_enabled_strategies_none_means_no_extra_restriction():
    without_filter = select_candidate_strategies(iv_rank=70, risk_level="agresivo")
    with_none_filter = select_candidate_strategies(iv_rank=70, risk_level="agresivo", enabled_strategies=None)
    assert without_filter == with_none_filter


def test_enabled_strategies_restricts_to_mvp_scope():
    mvp_scope = frozenset({c.CASH_SECURED_PUT, c.SHORT_PUT_NAKED, c.COVERED_CALL, c.COLLAR, c.IRON_CONDOR})
    candidates = select_candidate_strategies(
        iv_rank=70, risk_level="agresivo", has_open_assigned_position=True, enabled_strategies=mvp_scope
    )
    assert set(candidates) <= mvp_scope
    assert c.SHORT_CALL_NAKED not in candidates  # habilitado por perfil, pero fuera del scope MVP
    assert c.BULL_PUT_SPREAD not in candidates


def test_enabled_strategies_can_exclude_everything():
    candidates = select_candidate_strategies(iv_rank=70, risk_level="agresivo", enabled_strategies=frozenset())
    assert candidates == []


def test_iv_rank_high_threshold_default_matches_module_constant():
    with_default = select_candidate_strategies(iv_rank=55, risk_level="agresivo")
    with_explicit = select_candidate_strategies(iv_rank=55, risk_level="agresivo", iv_rank_high_threshold=c.IV_RANK_HIGH_THRESHOLD)
    assert with_default == with_explicit


def test_lower_iv_rank_high_threshold_unlocks_premium_selling_sooner():
    """Perfil agresivo (umbral más bajo, ej. 40) debería ofrecer venta de prima con un IV Rank
    que perfil conservador (umbral más alto, ej. 60) todavía considera "bajo"."""
    iv_rank = 45  # entre 40 y 60
    conservative = select_candidate_strategies(iv_rank=iv_rank, risk_level="moderado", iv_rank_high_threshold=60)
    aggressive = select_candidate_strategies(iv_rank=iv_rank, risk_level="moderado", iv_rank_high_threshold=40)
    assert c.CASH_SECURED_PUT not in conservative  # 45 < 60: régimen de IV bajo, no vende prima
    assert c.CASH_SECURED_PUT in aggressive  # 45 >= 40: régimen de IV alto, sí vende prima
