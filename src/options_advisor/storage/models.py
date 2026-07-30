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
    # "Check histórico" (pedido 2026-07-28): de cuántas ventanas de `dte` días en los últimos
    # ~5 años el precio real se movió al menos tanto como necesitaría moverse hoy para llegar a
    # este strike — ver strategy/backtest.py::historical_move_frequency(). None si no se pudo
    # calcular (sin datos suficientes, o el strike ya estaba ITM al momento de generar la
    # alerta). total_windows=0 nunca se persiste como 0 con occurrences no-None — o ambos
    # tienen valor, o ambos quedan None.
    historical_move_occurrences: int | None = None
    historical_move_total_windows: int | None = None
    # "Movimiento similar" (pedido 2026-07-29, refina el check histórico de arriba): en vez de
    # un umbral "o más", busca movimientos de magnitud PARECIDA (±3 puntos porcentuales) en un
    # plazo parecido (±1 semana) — ver strategy/backtest.py::historical_similar_move_frequency().
    # `similar_move_bigger_occurrences`: crashes que superaron la banda de tolerancia por
    # arriba (más grandes que "similar") en el mismo rango de días — mostrado aparte para no
    # esconder el escenario más peligroso detrás de un número que solo mira "parecidos".
    similar_move_occurrences: int | None = None
    similar_move_bigger_occurrences: int | None = None


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
    quantity: int  # contratos de ESTA orden puntual (no el total acumulado de la posición)
    entry_price: float | None = None  # fill EXACTO de esta orden (ver broker/models.py::FilledOrderLeg), no un promedio
    # orderId de Schwab que originó esta alerta — clave de dedup contra reprocesar la misma
    # orden en corridas sucesivas del cron (ventanas de detección se solapan a propósito, ver
    # alerts/real_trades.py). None en filas de antes del rediseño vía /orders (2026-07-28).
    order_id: int | None = None
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
    # "Check histórico" (pedido 2026-07-28) — ver CandidateContract, mismo campo/mismo criterio.
    historical_move_occurrences: int | None = None
    historical_move_total_windows: int | None = None
    # "Movimiento similar" (pedido 2026-07-29, refina el check histórico de arriba): en vez de
    # un umbral "o más", busca movimientos de magnitud PARECIDA (±3 puntos porcentuales) en un
    # plazo parecido (±1 semana) — ver strategy/backtest.py::historical_similar_move_frequency().
    # `similar_move_bigger_occurrences`: crashes que superaron la banda de tolerancia por
    # arriba (más grandes que "similar") en el mismo rango de días — mostrado aparte para no
    # esconder el escenario más peligroso detrás de un número que solo mira "parecidos".
    similar_move_occurrences: int | None = None
    similar_move_bigger_occurrences: int | None = None
    narrative_text: str | None = None
    narrative_source: NarrativeSource | None = None
    # Rolls (pedido 2026-07-30): None = apertura normal (comportamiento de siempre) |
    # "roll_closed" = pata que se CERRÓ como parte de un roll (registro liviano, sin P&L propio
    # — ver alerts/real_trades.py::_build_and_persist_roll_closed_leg) | "roll_opened" = pata
    # NUEVA que la reemplazó (cálculo completo, igual que una apertura normal). Ambas filas de
    # un mismo roll comparten `order_id`.
    leg_role: str | None = None


class InvestorProfile(BaseModel):
    capital_available: float
    loss_tolerance_pct: float
    experience_level: str
    risk_preference: str
    risk_level: str
    conviction_threshold_override: int | None = None
    updated_at: datetime
