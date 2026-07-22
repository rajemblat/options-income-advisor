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


def test_low_iv_rank_returns_calendar_and_diagonal():
    candidates = select_candidate_strategies(iv_rank=20, risk_level="moderado")
    assert set(candidates) == {c.CALENDAR_PUT_SPREAD, c.DIAGONAL_PUT_SPREAD}


def test_covered_call_only_appears_with_open_assigned_position():
    without_position = select_candidate_strategies(iv_rank=70, risk_level="moderado", has_open_assigned_position=False)
    with_position = select_candidate_strategies(iv_rank=70, risk_level="moderado", has_open_assigned_position=True)
    assert c.COVERED_CALL not in without_position
    assert c.COVERED_CALL in with_position


def test_iv_rank_exactly_at_threshold_counts_as_high():
    candidates = select_candidate_strategies(iv_rank=50, risk_level="conservador")
    assert c.CASH_SECURED_PUT in candidates
