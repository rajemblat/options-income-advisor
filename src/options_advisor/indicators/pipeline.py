from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import NamedTuple

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import OptionChain, Quote
from options_advisor.config import Settings
from options_advisor.indicators import gex, levels, technical, volatility
from options_advisor.market_context import finnhub_client
from options_advisor.storage import repository as repo
from options_advisor.storage.models import IndicatorSnapshot

PRICE_HISTORY_LOOKBACK_DAYS = 300  # cubre holgadamente SMA200 + ventana de HV proxy
CHAIN_FETCH_RANGE_DAYS = (7, 60)  # cubre todos los vencimientos que strategy/candidates.py puede necesitar
ATM_REFERENCE_RANGE_DAYS = (25, 50)  # ventana estándar ~30-45 DTE para la IV de referencia


class SymbolAnalysis(NamedTuple):
    snapshot: IndicatorSnapshot
    chain: OptionChain
    quote: Quote


def analyze_symbol(
    broker: BrokerClient, conn: sqlite3.Connection, symbol: str, settings: Settings, finnhub_api_key: str | None = None
) -> SymbolAnalysis:
    """Calcula todos los indicadores de Fase 1 para un símbolo y persiste el snapshot.
    Devuelve también la cadena de opciones y el quote, que necesita strategy/candidates.py
    para construir los contratos concretos a sugerir."""
    quote = broker.get_quote(symbol)
    price_history = broker.get_price_history(symbol, lookback_days=PRICE_HISTORY_LOOKBACK_DAYS)
    chain = broker.get_option_chain(symbol, expiration_range_days=CHAIN_FETCH_RANGE_DAYS)

    current_iv = None
    try:
        expiration = chain.nearest_expiration(*ATM_REFERENCE_RANGE_DAYS)
        atm_call = chain.atm_contract("call", expiration=expiration)
        atm_put = chain.atm_contract("put", expiration=expiration)
        current_iv = round((atm_call.implied_volatility + atm_put.implied_volatility) / 2, 4)
    except ValueError:
        pass  # sin contratos en la ventana ATM esperada; iv_rank cae al proxy de HV

    if current_iv is not None:
        repo.insert_iv_snapshot(conn, symbol, quote.as_of, current_iv, source=settings.broker.mode)

    iv_history = repo.get_iv_snapshots(conn, symbol)
    iv_rank_result = volatility.compute_iv_rank(
        iv_snapshot_history=iv_history,
        current_iv=current_iv,
        price_bars=price_history,
        min_sessions_for_real_iv=settings.iv_rank.min_sessions_for_real_iv,
        full_window_sessions=settings.iv_rank.full_window_sessions,
        hv_window_days=settings.iv_rank.hv_window_days,
    )

    cross_signal = technical.detect_ma_cross(price_history, 8, 20) or technical.detect_ma_cross(
        price_history, 50, 200
    )
    supports, resistances = levels.find_support_resistance(price_history, quote.last_price)
    next_earnings_date = finnhub_client.get_next_earnings_date(symbol, quote.as_of, finnhub_api_key)

    snapshot = IndicatorSnapshot(
        symbol=symbol,
        snapshot_date=quote.as_of,
        snapshot_ts=datetime.now(),
        price=quote.last_price,
        iv_atm=current_iv,
        iv_rank=iv_rank_result.iv_rank,
        iv_rank_source=iv_rank_result.source,
        hv_20d=volatility.compute_historical_volatility(price_history, settings.iv_rank.hv_window_days),
        atr_14=technical.compute_atr(price_history),
        rsi_14=technical.compute_rsi(price_history),
        sma_8=technical.compute_sma(price_history, 8),
        sma_20=technical.compute_sma(price_history, 20),
        sma_50=technical.compute_sma(price_history, 50),
        sma_200=technical.compute_sma(price_history, 200),
        ma_cross_signal=cross_signal,
        support_levels=supports,
        resistance_levels=resistances,
        next_earnings_date=next_earnings_date,
        price_std_20=technical.compute_stddev(price_history, 20),
        net_gex=gex.compute_net_gex(chain),
    )
    repo.insert_indicator_snapshot(conn, snapshot)

    return SymbolAnalysis(snapshot=snapshot, chain=chain, quote=quote)
