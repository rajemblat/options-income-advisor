from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

IvRankSource = Literal["implied_volatility", "historical_volatility_proxy"]
NarrativeSource = Literal["claude", "fallback_template"]
GreeksSource = Literal["broker", "calculated"]


class IndicatorSnapshot(BaseModel):
    symbol: str
    snapshot_date: date
    snapshot_ts: datetime
    price: float
    iv_atm: float | None = None
    iv_rank: float | None = None
    iv_rank_source: IvRankSource
    hv_20d: float | None = None
    atr_14: float | None = None
    rsi_14: float | None = None
    sma_8: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    ma_cross_signal: str | None = None
    support_levels: list[float] = []
    resistance_levels: list[float] = []
    raw_indicators_json: str | None = None


class CandidateContract(BaseModel):
    symbol: str
    snapshot_date: date
    strategy_type: str
    expiration_date: date
    strikes: dict
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None
    greeks_source: GreeksSource
    conviction_score: int
    scoring_breakdown: dict


class Alert(BaseModel):
    symbol: str
    alert_date: date
    alert_ts: datetime
    candidate_contract_id: int | None
    conviction_score: int
    risk_profile: str
    threshold_applied: int
    was_notified: bool
    narrative_text: str | None
    narrative_source: NarrativeSource | None
    dedup_key: str


class InvestorProfile(BaseModel):
    capital_available: float
    loss_tolerance_pct: float
    experience_level: str
    risk_preference: str
    risk_level: str
    conviction_threshold_override: int | None = None
    updated_at: datetime
