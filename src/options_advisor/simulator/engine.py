from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import OptionChain, PriceBar
from options_advisor.config import Settings
from options_advisor.simulator import entry_rules, positions
from options_advisor.storage import repository as repo
from options_advisor.storage.models import IndicatorSnapshot

logger = logging.getLogger(__name__)

# Rango de vencimientos a pedir al marcar posiciones abiertas — a diferencia de
# `indicators/pipeline.py::CHAIN_FETCH_RANGE_DAYS` (7-60, pensado para elegir candidatos
# NUEVOS), acá arranca en 1 día porque una posición ya abierta puede estar a días de vencer y
# necesitamos que siga apareciendo en la cadena mientras exista.
MARK_CHAIN_FETCH_RANGE_DAYS = (1, 60)


def ensure_account(conn: sqlite3.Connection, settings: Settings) -> None:
    if repo.get_simulated_account(conn) is None:
        repo.init_simulated_account(conn, settings.simulator.initial_capital, datetime.now())


def _account_equity(conn: sqlite3.Connection) -> float:
    """Cash disponible + garantía comprometida en posiciones abiertas + P&L no realizado del
    último marcado — base del sizing (Sección 'tamaño de posición', confirmado con el usuario
    2026-08-02: % de EQUITY total, no de cash libre, para que el tamaño no se achique solo
    porque hay varias posiciones abiertas comiendo cash)."""
    account = repo.get_simulated_account(conn)
    open_positions = repo.get_open_simulated_positions(conn)
    committed = sum(p["collateral"] for p in open_positions)
    unrealized = sum(p["last_unrealized_pnl"] or 0.0 for p in open_positions)
    return account["cash"] + committed + unrealized


def process_symbol_entry(
    conn: sqlite3.Connection,
    symbol: str,
    snapshot: IndicatorSnapshot,
    chain: OptionChain,
    price_history: list[PriceBar],
    settings: Settings,
) -> None:
    """Evalúa los 8 criterios de entrada (simulator/entry_rules.py) para `symbol` con los datos
    que `indicators/pipeline.py::analyze_symbol` ya calculó hoy, y abre una posición Naked Put
    simulada si pasan y hay cash/garantía suficiente. Nunca abre una segunda posición sobre el
    mismo símbolo mientras ya tenga una abierta."""
    if not settings.simulator.enabled:
        return
    ensure_account(conn, settings)
    if repo.has_open_simulated_position(conn, symbol):
        return

    result = entry_rules.evaluate_entry(symbol, snapshot, chain, price_history, settings.simulator)
    if not result.passed:
        logger.debug("Simulador: %s no calificó hoy — %s", symbol, "; ".join(result.reasons))
        return

    account = repo.get_simulated_account(conn)
    equity = _account_equity(conn)
    sizing = positions.size_position(result.contract.strike, account["cash"], equity, settings.simulator)
    if sizing is None:
        logger.info("Simulador: %s calificó pero no hay cash/garantía suficiente para abrir una posición", symbol)
        return

    positions.open_position(conn, symbol, result.contract, sizing.quantity, sizing.collateral, snapshot.snapshot_date)
    logger.info(
        "Simulador: posición ABIERTA %s put $%.2f venc %s x%d contrato(s), prima %.2f",
        symbol, result.contract.strike, result.contract.expiration, sizing.quantity, result.premium,
    )


def mark_and_close_positions(conn: sqlite3.Connection, broker: BrokerClient, settings: Settings, as_of: date) -> None:
    """Corre el mark-to-market diario de TODAS las posiciones simuladas abiertas y registra la
    curva de equity del día — separado de `process_symbol_entry` porque un símbolo con una
    posición simulada abierta puede no ser parte de la watchlist evaluada hoy para entradas
    nuevas, y porque necesita correr una sola vez por día (no una vez por símbolo analizado)."""
    if not settings.simulator.enabled:
        return
    ensure_account(conn, settings)
    open_positions = repo.get_open_simulated_positions(conn)

    by_symbol: dict[str, list[sqlite3.Row]] = {}
    for row in open_positions:
        by_symbol.setdefault(row["symbol"], []).append(row)

    total_committed = 0.0
    total_unrealized = 0.0
    for symbol, rows in by_symbol.items():
        try:
            quote = broker.get_quote(symbol)
            chain = broker.get_option_chain(symbol, expiration_range_days=MARK_CHAIN_FETCH_RANGE_DAYS)
        except Exception:
            logger.warning("Simulador: fallo al pedir precio/cadena de %s para marcar posiciones; se reintenta mañana", symbol, exc_info=True)
            total_committed += sum(r["collateral"] for r in rows)
            total_unrealized += sum(r["last_unrealized_pnl"] or 0.0 for r in rows)
            continue

        for row in rows:
            outcome = positions.mark_position(conn, row, chain, quote.last_price, as_of, settings.simulator)
            if outcome["closed"]:
                logger.info(
                    "Simulador: posición CERRADA %s put $%.2f venc %s — motivo=%s, P&L=%.2f",
                    symbol, row["strike"], row["expiration_date"], outcome["reason"], outcome["unrealized_pnl"],
                )
            else:
                total_committed += row["collateral"]
                total_unrealized += outcome["unrealized_pnl"]

    account = repo.get_simulated_account(conn)
    equity = round(account["cash"] + total_committed + total_unrealized, 2)
    repo.upsert_simulated_equity_snapshot(conn, as_of, account["cash"], round(total_committed, 2), round(total_unrealized, 2), equity)
