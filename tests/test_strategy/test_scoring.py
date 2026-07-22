from __future__ import annotations

from options_advisor.strategy import constants as c
from options_advisor.strategy.scoring import compute_conviction_score


def test_score_is_deterministic():
    args = dict(
        strategy_type=c.CASH_SECURED_PUT,
        strikes={"short_strike": 195.0},
        iv_rank=68.0,
        iv_rank_source="implied_volatility",
        rsi=55.0,
        supports=[195.0],
        resistances=[210.0],
    )
    score1, _ = compute_conviction_score(**args)
    score2, _ = compute_conviction_score(**args)
    assert score1 == score2


def test_higher_iv_rank_increases_score_for_csp():
    low, _ = compute_conviction_score(
        c.CASH_SECURED_PUT, {"short_strike": 195.0}, iv_rank=20, iv_rank_source="implied_volatility",
        rsi=55, supports=[195.0], resistances=[]
    )
    high, _ = compute_conviction_score(
        c.CASH_SECURED_PUT, {"short_strike": 195.0}, iv_rank=90, iv_rank_source="implied_volatility",
        rsi=55, supports=[195.0], resistances=[]
    )
    assert high > low


def test_lower_iv_rank_increases_score_for_calendar_spread():
    low_iv, _ = compute_conviction_score(
        c.CALENDAR_PUT_SPREAD, {"near_strike": 195.0}, iv_rank=10, iv_rank_source="implied_volatility",
        rsi=55, supports=[195.0], resistances=[]
    )
    high_iv, _ = compute_conviction_score(
        c.CALENDAR_PUT_SPREAD, {"near_strike": 195.0}, iv_rank=80, iv_rank_source="implied_volatility",
        rsi=55, supports=[195.0], resistances=[]
    )
    assert low_iv > high_iv  # calendar spreads se benefician de IV Rank bajo (prima barata)


def test_oversold_rsi_penalizes_put_selling_score():
    normal, _ = compute_conviction_score(
        c.CASH_SECURED_PUT, {"short_strike": 195.0}, iv_rank=70, iv_rank_source="implied_volatility",
        rsi=55, supports=[195.0], resistances=[]
    )
    panic, _ = compute_conviction_score(
        c.CASH_SECURED_PUT, {"short_strike": 195.0}, iv_rank=70, iv_rank_source="implied_volatility",
        rsi=10, supports=[195.0], resistances=[]
    )
    assert normal > panic  # Sección 3.3: evitar vender puts en pánico de venta sin agotamiento


def test_strike_near_support_scores_higher_than_far_from_support():
    near, _ = compute_conviction_score(
        c.CASH_SECURED_PUT, {"short_strike": 195.0}, iv_rank=70, iv_rank_source="implied_volatility",
        rsi=55, supports=[195.0], resistances=[]
    )
    far, _ = compute_conviction_score(
        c.CASH_SECURED_PUT, {"short_strike": 195.0}, iv_rank=70, iv_rank_source="implied_volatility",
        rsi=55, supports=[150.0], resistances=[]
    )
    assert near > far


def test_hv_proxy_scores_lower_than_real_iv_all_else_equal():
    real_iv, _ = compute_conviction_score(
        c.CASH_SECURED_PUT, {"short_strike": 195.0}, iv_rank=70, iv_rank_source="implied_volatility",
        rsi=55, supports=[195.0], resistances=[]
    )
    proxy, _ = compute_conviction_score(
        c.CASH_SECURED_PUT, {"short_strike": 195.0}, iv_rank=70, iv_rank_source="historical_volatility_proxy",
        rsi=55, supports=[195.0], resistances=[]
    )
    assert real_iv > proxy


def test_score_is_bounded_0_to_100():
    score, _ = compute_conviction_score(
        c.CASH_SECURED_PUT, {"short_strike": 195.0}, iv_rank=100, iv_rank_source="implied_volatility",
        rsi=55, supports=[195.0], resistances=[]
    )
    assert 0 <= score <= 100
