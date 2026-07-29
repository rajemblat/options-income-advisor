from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from options_advisor.alerts import dedup, narrator, notifier
from options_advisor.broker.base import BrokerClient
from options_advisor.config import Settings
from options_advisor.indicators.pipeline import SymbolAnalysis
from options_advisor.market_context import finnhub_client
from options_advisor.storage import repository as repo
from options_advisor.storage.models import Alert, CandidateContract
from options_advisor.strategy import backtest
from options_advisor.strategy import candidates as candidate_builder
from options_advisor.strategy import payoff as payoff_calc
from options_advisor.strategy import scoring
from options_advisor.strategy.selector import select_candidate_strategies

logger = logging.getLogger(__name__)

_SMA_FIELD_BY_PERIOD = {8: "sma_8", 20: "sma_20", 50: "sma_50", 200: "sma_200"}


def _resolve_support_sma_values(snap, periods: list[int]) -> list[float | None]:
    """Traduce los períodos configurados por perfil (settings.strategy.support_sma_periods,
    ej. [8, 20]) a los valores concretos ya calculados en el snapshot del símbolo — None si
    ese período no está entre los que el motor calcula (Sección 'perfil de riesgo' refinado,
    2026-07-24)."""
    return [getattr(snap, _SMA_FIELD_BY_PERIOD[p], None) for p in periods if p in _SMA_FIELD_BY_PERIOD]


def _resolve_risk_profile(conn: sqlite3.Connection, settings: Settings) -> tuple[str, int]:
    """El perfil guardado en la DB (editable desde el dashboard) tiene prioridad sobre el
    default de settings.yaml; conviction_threshold_override, si está seteado, pisa el umbral
    por perfil de riesgo (Sección 6.1)."""
    profile = repo.get_investor_profile(conn)
    if profile is None:
        risk_level = settings.investor_profile.risk_level
        return risk_level, settings.conviction_thresholds.for_risk_level(risk_level)
    threshold = profile.conviction_threshold_override or settings.conviction_thresholds.for_risk_level(profile.risk_level)
    return profile.risk_level, threshold


def process_symbol_alerts(
    conn: sqlite3.Connection,
    analysis: SymbolAnalysis,
    settings: Settings,
    has_open_assigned_position: bool = False,
    anthropic_api_key: str | None = None,
    finnhub_api_key: str | None = None,
    risk_level: str | None = None,
    recent_news: list[dict] | None = None,
    block_new_candidates: bool = False,
    broker: BrokerClient | None = None,
) -> list[dict]:
    """Corre selector → candidatos → scoring → filtro de umbral → dedup → narrador → persistencia
    para un símbolo ya analizado (Sección 6 de la hoja de ruta). Devuelve las alertas nuevas
    efectivamente generadas en esta corrida (lista vacía si no hubo ninguna).

    risk_level: si se pasa explícito (jobs.py lo hace una vez por cada uno de los 3 perfiles
    fijos por corrida), pisa el perfil activo guardado en la DB y usa el umbral default de ese
    perfil — conviction_threshold_override no aplica acá porque es un override de UN perfil
    activo, no tiene sentido aplicado a los 3 a la vez. Si se omite (callers de un solo perfil,
    tests), se resuelve como antes desde investor_profile/settings.

    recent_news: si se pasa (jobs.py lo trae una sola vez por símbolo, no por perfil, ya que
    las noticias no dependen del perfil de riesgo), se reusa en vez de pedirlo de nuevo a
    Finnhub — evita triplicar esa llamada al evaluar los 3 perfiles.

    block_new_candidates: True en días de riesgo alto (CPI/NFP/FOMC, ver
    `scheduler/jobs.py::_run_full_analysis` y `settings.strategy.block_new_candidates_on_high_risk_days`)
    — no genera NINGÚN candidato nuevo ese día, sea cual sea el perfil de riesgo. Solo bloquea
    sugerencias nuevas, nunca alertas ya persistidas ni posiciones ya abiertas.

    broker: si se pasa (jobs.py lo hace siempre), habilita el "check histórico" (pedido
    2026-07-28, ver strategy/backtest.py) — pide 5 años de precio real y calcula cuántas
    ventanas de `dte` días tocaron un movimiento como el que esta alerta necesita. None (tests
    que no lo pasan) simplemente lo omite, mismo criterio que el resto de datos opcionales."""
    snap = analysis.snapshot
    if block_new_candidates:
        logger.info("%s: bloqueado por día de riesgo alto (CPI/NFP/FOMC) — no se generan candidatos nuevos", snap.symbol)
        return []
    if risk_level is not None:
        threshold = settings.conviction_thresholds.for_risk_level(risk_level)
    else:
        risk_level, threshold = _resolve_risk_profile(conn, settings)

    profile = repo.get_investor_profile(conn)
    capital_available = profile.capital_available if profile else settings.investor_profile.capital_available

    if snap.iv_rank is None:
        logger.info("%s: IV Rank no disponible todavía, sin candidatos posibles", snap.symbol)
        return []

    target_short_delta = settings.strategy.target_short_delta.for_risk_level(risk_level)
    iv_rank_high_threshold = settings.strategy.iv_rank_high_threshold.for_risk_level(risk_level)
    min_coverage_pct = settings.strategy.min_coverage_pct.for_risk_level(risk_level)
    support_sma_values = _resolve_support_sma_values(snap, settings.strategy.support_sma_periods.for_risk_level(risk_level))

    strategy_types = select_candidate_strategies(
        snap.iv_rank,
        risk_level,
        ma_cross_signal=snap.ma_cross_signal,
        rsi=snap.rsi_14,
        has_open_assigned_position=has_open_assigned_position,
        enabled_strategies=frozenset(settings.strategy.enabled),
        iv_rank_high_threshold=iv_rank_high_threshold,
    )
    generated: list[dict] = []
    if recent_news is None:
        recent_news = finnhub_client.get_recent_news(snap.symbol, snap.snapshot_date, finnhub_api_key) if strategy_types else []

    for strategy_type in strategy_types:
        build = candidate_builder.build_candidate(
            strategy_type, analysis.chain, target_short_delta, min_coverage_pct=min_coverage_pct, support_sma_values=support_sma_values
        )
        if build is None:
            continue

        score, breakdown = scoring.compute_conviction_score(
            strategy_type=strategy_type,
            strikes=build.strikes,
            iv_rank=snap.iv_rank,
            iv_rank_source=snap.iv_rank_source,
            rsi=snap.rsi_14,
            supports=snap.support_levels,
            resistances=snap.resistance_levels,
        )
        if score < threshold:
            continue  # filtro de exclusión: no alcanza el umbral mínimo del perfil (Sección 6.3)

        dedup_key = dedup.build_dedup_key(snap.symbol, strategy_type, build.expiration_date, build.strikes, snap.snapshot_date, risk_level)
        if repo.alert_exists(conn, dedup_key):
            continue  # mismo candidato ya alertado hoy

        try:
            payoff = payoff_calc.compute_payoff(build, analysis.quote.last_price, snap.snapshot_date, settings.market.risk_free_rate)
        except Exception:
            logger.exception("Fallo al calcular payoff de %s/%s; se omite este candidato", snap.symbol, strategy_type)
            continue

        historical_check = (
            backtest.compute_historical_move_check(broker, snap.symbol, payoff.legs, payoff.underlying_price, payoff.dte)
            if broker is not None
            else None
        )

        candidate_id = repo.insert_candidate_contract(
            conn,
            CandidateContract(
                symbol=snap.symbol,
                snapshot_date=snap.snapshot_date,
                strategy_type=strategy_type,
                expiration_date=build.expiration_date,
                strikes=build.strikes,
                delta=build.net_greeks.get("delta"),
                gamma=build.net_greeks.get("gamma"),
                theta=build.net_greeks.get("theta"),
                vega=build.net_greeks.get("vega"),
                rho=build.net_greeks.get("rho"),
                greeks_source=build.greeks_source,
                conviction_score=score,
                scoring_breakdown=breakdown,
                legs=payoff.legs,
                net_premium=payoff.net_premium,
                max_profit=payoff.max_profit,
                max_loss=payoff.max_loss,
                breakevens=payoff.breakevens,
                probability_of_profit=payoff.probability_of_profit,
                dte=payoff.dte,
                underlying_price=payoff.underlying_price,
                payoff_is_estimate=payoff.is_estimate,
                annualized_return_pct=payoff.annualized_return_pct,
                historical_move_occurrences=historical_check.occurrences if historical_check else None,
                historical_move_total_windows=historical_check.total_windows if historical_check else None,
                early_close_projection=payoff.early_close_projection,
            ),
        )

        context = narrator.build_narration_context(
            symbol=snap.symbol,
            strategy_type=strategy_type,
            conviction_score=score,
            breakdown=breakdown,
            iv_rank=snap.iv_rank,
            iv_rank_source=snap.iv_rank_source,
            rsi=snap.rsi_14,
            supports=snap.support_levels,
            resistances=snap.resistance_levels,
            strikes=build.strikes,
            expiration_date=build.expiration_date,
            underlying_price=payoff.underlying_price,
            legs=payoff.legs,
            net_premium=payoff.net_premium,
            max_profit=payoff.max_profit,
            max_loss=payoff.max_loss,
            breakevens=payoff.breakevens,
            probability_of_profit=payoff.probability_of_profit,
            dte=payoff.dte,
            payoff_is_estimate=payoff.is_estimate,
            next_earnings_date=snap.next_earnings_date,
            recent_news=recent_news,
            next_ex_dividend_date=analysis.quote.next_ex_dividend_date,
            annualized_return_pct=payoff.annualized_return_pct,
            early_close_projection=payoff.early_close_projection,
            capital_available=capital_available,
        )
        narrative_text, narrative_source = narrator.narrate_alert(context, settings.llm, anthropic_api_key)

        alert_id = repo.insert_alert(
            conn,
            Alert(
                symbol=snap.symbol,
                alert_date=snap.snapshot_date,
                alert_ts=datetime.now(),
                candidate_contract_id=candidate_id,
                conviction_score=score,
                risk_profile=risk_level,
                threshold_applied=threshold,
                was_notified=True,
                narrative_text=narrative_text,
                narrative_source=narrative_source,
                dedup_key=dedup_key,
            ),
        )
        if alert_id is not None:
            notifier.notify(snap.symbol, strategy_type, score, narrative_text)
            generated.append(
                {
                    "symbol": snap.symbol,
                    "strategy_type": strategy_type,
                    "score": score,
                    "strikes": build.strikes,
                    "narrative": narrative_text,
                }
            )

    return generated
