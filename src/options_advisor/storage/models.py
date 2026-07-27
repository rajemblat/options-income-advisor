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
    next_earnings_date: date | None = None
    price_std_20: float | None = None
    net_gex: float | None = None
    next_ex_dividend_date: date | None = None


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
    legs: list[dict] = []
    net_premium: float | None = None
    max_profit: float | None = None
    max_loss: float | None = None
    breakevens: list[float] = []
    probability_of_profit: float | None = None
    dte: int | None = None
    underlying_price: float | None = None
    payoff_is_estimate: bool = False
    annualized_return_pct: float | None = None
    early_close_projection: list[dict] = []


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


class MacroSnapshot(BaseModel):
    snapshot_date: date
    fed_funds_lower: float | None = None
    fed_funds_upper: float | None = None
    cpi_yoy_pct: float | None = None
    cpi_yoy_date: date | None = None  # fecha del dato de FRED (ej. el mes que mide el CPI), no snapshot_date
    unemployment_rate_pct: float | None = None
    gdp_growth_annualized_pct: float | None = None
    fed_meeting_date: date | None = None
    fed_hike_probability: float | None = None
    fed_hold_probability: float | None = None
    fed_cut_probability: float | None = None
    upcoming_events: list[dict] = []


class NewsItem(BaseModel):
    symbol: str
    published_at: datetime | None = None
    headline: str
    source: str | None = None
    url: str
    summary: str | None = None
    fetched_date: date


class Notification(BaseModel):
    kind: str
    title: str
    body: str
    created_at: datetime


class PositionSnapshot(BaseModel):
    """Última cantidad conocida de una posición CORTA de opciones por cuenta (Sección
    'Operaciones' — réplica automática de operaciones reales, pedido 2026-07-25). Sobreescrita
    por completo cada corrida del scheduler (ver repository.replace_position_snapshots): solo
    hace falta el estado de la corrida anterior para diffear contra la actual y detectar ventas
    nuevas, no un historial completo."""

    account_number: str
    symbol: str  # OCC de la opción
    quantity: float  # negativo = corto; se guardan solo posiciones cortas, ver real_trades.py
    snapshot_ts: datetime


class RealTradeAlert(BaseModel):
    """Una operación real de venta de opciones detectada en la cuenta Schwab (no una sugerencia
    — `candidate_contracts`/`alerts` son para eso). Mismos campos de P&L/riesgo que
    CandidateContract, sin conviction_score/scoring_breakdown/risk_profile/threshold: nada se
    puntuó, la operación ya se ejecutó."""

    account_number: str
    occ_symbol: str
    symbol: str  # subyacente
    trade_date: date
    trade_ts: datetime
    strategy_type: str
    option_type: str  # "put" | "call"
    strike: float
    expiration_date: date
    quantity: int  # contratos nuevos detectados en esta operación (no el total de la posición)
    entry_price: float | None = None  # precio promedio de la posición completa, tal como lo da Schwab
    legs: list[dict] = []
    net_premium: float | None = None
    max_profit: float | None = None
    max_loss: float | None = None
    breakevens: list[float] = []
    probability_of_profit: float | None = None
    dte: int | None = None
    underlying_price: float | None = None
    payoff_is_estimate: bool = False
    annualized_return_pct: float | None = None
    early_close_projection: list[dict] = []
    narrative_text: str | None = None
    narrative_source: NarrativeSource | None = None


class InvestorProfile(BaseModel):
    capital_available: float
    loss_tolerance_pct: float
    experience_level: str
    risk_preference: str
    risk_level: str
    conviction_threshold_override: int | None = None
    updated_at: datetime
