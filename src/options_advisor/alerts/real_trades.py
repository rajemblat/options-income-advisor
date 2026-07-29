from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone

from options_advisor.alerts import notifier
from options_advisor.alerts.narrator import build_real_trade_context, narrate_real_trade
from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import FilledOrder, FilledOrderLeg, index_quote_symbol, parse_occ_option_symbol
from options_advisor.config import Settings
from options_advisor.market_context import finnhub_client
from options_advisor.storage import repository as repo
from options_advisor.storage.models import RealTradeAlert
from options_advisor.strategy import backtest
from options_advisor.strategy import candidates as candidate_builder
from options_advisor.strategy import constants as c
from options_advisor.strategy import payoff as payoff_calc

logger = logging.getLogger(__name__)

MIN_SHARES_FOR_COVERED_CALL = 100  # 1 contrato de call cubre 100 acciones

# Ventana de detección angosta (Sección 'rediseño de Operaciones vía /orders', 2026-07-28):
# pedirle a Schwab varios días de órdenes de una sola vez es lento (timeout real observado
# pidiendo 3 días, ~260 órdenes) — se pide bastante más que la cadencia del cron (3 min por
# defecto, ver scheduler/runner.py) para tener margen de sobra si una corrida se retrasa o se
# saltea, sin pagar el costo de una ventana ancha. El dedup por (order_id, occ_symbol) hace que
# pedir de más (solapamiento entre corridas) sea inofensivo — nunca genera una alerta duplicada.
REAL_TRADE_LOOKBACK_MINUTES = 15


def _resolve_strategy_type(option_type: str, share_positions: dict[str, int], underlying_symbol: str, contracts: int) -> str:
    """Put vendido: se asume Cash-Secured Put (el motor no trackea saldo de efectivo/margen,
    misma simplificación que el resto de la app). Call vendida: Covered Call si hay >= 100
    acciones por contrato ya en cartera (misma fuente `share_positions` que ya usa
    `scheduler/jobs.py` para las estrategias cubiertas normales), si no Call desnuda (riesgo NO
    acotado — se refleja en el payoff, no solo en el texto)."""
    if option_type == "put":
        return c.CASH_SECURED_PUT
    owned = share_positions.get(underlying_symbol, 0)
    return c.COVERED_CALL if owned >= MIN_SHARES_FOR_COVERED_CALL * contracts else c.SHORT_CALL_NAKED


def _is_roll(order: FilledOrder) -> bool:
    """Un roll es una orden con AL MENOS una pata OPENING y AL MENOS una pata CLOSING — Schwab
    arma un roll como una única orden combinada (confirmado en vivo con un roll real de SOFI:
    `complexOrderStrategyType: "CALENDAR"`, una pata SELL_TO_OPEN + una BUY_TO_CLOSE en la MISMA
    orden). Esto reemplaza la heurística de la Fase 1 anterior (suprimir cualquier apertura del
    mismo subyacente si algo se cerró en la misma corrida, con falsos negativos documentados) —
    acá la composición de la orden lo confirma con certeza, sin inferir nada entre corridas."""
    effects = {leg.position_effect for leg in order.legs}
    return "OPENING" in effects and "CLOSING" in effects


def _build_and_persist_real_trade_alert(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    settings: Settings,
    today: date,
    order: FilledOrder,
    leg: FilledOrderLeg,
    share_positions: dict[str, int],
    anthropic_api_key: str | None,
    finnhub_api_key: str | None,
) -> dict | None:
    parsed = parse_occ_option_symbol(leg.occ_symbol)
    if parsed is None:
        logger.warning("%s: símbolo OCC no reconocido; se omite la alerta de operación real", leg.occ_symbol)
        return None
    underlying_symbol, expiration, option_type, strike = parsed
    contracts_added = int(round(leg.quantity))
    if contracts_added <= 0:
        return None

    quote_symbol = index_quote_symbol(underlying_symbol)
    try:
        quote = broker.get_quote(quote_symbol)
        dte_upper_bound = max((expiration - today).days, 0) + 5
        chain = broker.get_option_chain(quote_symbol, expiration_range_days=(0, dte_upper_bound))
    except Exception:
        logger.exception(
            "Fallo al pedir cotización/cadena en vivo de %s; se omite la alerta de operación real de %s",
            quote_symbol, leg.occ_symbol,
        )
        return None

    contract = candidate_builder.find_contract(chain, option_type, expiration, strike)
    if contract is None:
        logger.warning(
            "%s: no se encontró el contrato %s $%.2f %s en la cadena en vivo; se omite el cálculo de P&L de la operación real",
            underlying_symbol, expiration, strike, option_type,
        )
        return None

    strategy_type = _resolve_strategy_type(option_type, share_positions, underlying_symbol, contracts_added)
    build = candidate_builder.build_from_contract(strategy_type, contract, contracts_added, entry_price=leg.price)
    payoff = payoff_calc.compute_payoff(build, quote.last_price, today, settings.market.risk_free_rate)

    recent_news = finnhub_client.get_recent_news(underlying_symbol, today, finnhub_api_key)
    next_earnings_date = repo.get_latest_next_earnings_date(conn, underlying_symbol)
    investor_profile = repo.get_investor_profile(conn)
    capital_available = investor_profile.capital_available if investor_profile else None

    context = build_real_trade_context(
        symbol=underlying_symbol,
        strategy_type=strategy_type,
        quantity=contracts_added,
        entry_price=leg.price,
        strikes=build.strikes,
        expiration_date=expiration,
        underlying_price=payoff.underlying_price,
        legs=payoff.legs,
        net_premium=payoff.net_premium,
        max_profit=payoff.max_profit,
        max_loss=payoff.max_loss,
        breakevens=payoff.breakevens,
        probability_of_profit=payoff.probability_of_profit,
        dte=payoff.dte,
        payoff_is_estimate=payoff.is_estimate,
        next_earnings_date=next_earnings_date,
        recent_news=recent_news,
        next_ex_dividend_date=quote.next_ex_dividend_date,
        annualized_return_pct=payoff.annualized_return_pct,
        early_close_projection=payoff.early_close_projection,
        capital_available=capital_available,
    )
    narrative_text, narrative_source = narrate_real_trade(context, settings.llm, anthropic_api_key)

    historical_check = backtest.compute_historical_move_check(broker, quote_symbol, payoff.legs, payoff.underlying_price, payoff.dte)

    trade = RealTradeAlert(
        account_number=order.account_number,
        occ_symbol=leg.occ_symbol,
        symbol=underlying_symbol,
        trade_date=today,
        trade_ts=datetime.now(),
        strategy_type=strategy_type,
        option_type=option_type,
        strike=strike,
        expiration_date=expiration,
        quantity=contracts_added,
        entry_price=leg.price,
        order_id=order.order_id,
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
        early_close_projection=payoff.early_close_projection,
        historical_move_occurrences=historical_check.occurrences if historical_check else None,
        historical_move_total_windows=historical_check.total_windows if historical_check else None,
        narrative_text=narrative_text,
        narrative_source=narrative_source,
    )
    repo.insert_real_trade_alert(conn, trade)
    notifier.notify_real_trade(underlying_symbol, strategy_type, narrative_text)
    return {"symbol": underlying_symbol, "strategy_type": strategy_type, "quantity": contracts_added, "narrative": narrative_text}


def detect_and_alert_real_trades(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    settings: Settings,
    today: date,
    share_positions: dict[str, int],
    anthropic_api_key: str | None,
    finnhub_api_key: str | None,
) -> list[dict]:
    """Detecta operaciones reales nuevas (venta de opciones) en la cuenta Schwab real y genera
    una alerta con el mismo formato completo que las alertas de candidatos (P&L, breakeven,
    POP, cobertura, noticias, comentario del narrador) pero aplicada a la posición YA ABIERTA —
    Pestaña Operaciones, pedido 2026-07-25, rediseñada 2026-07-28 para detectar vía órdenes
    LLENADAS (`broker.get_recent_filled_orders`) en vez de diffear posiciones contra un
    snapshot: cada orden trae el fill EXACTO de cada pata, sin promediar con otras aperturas
    del mismo contrato en momentos distintos (antes: `position.average_price`, un promedio
    blendeado de TODA la posición acumulada — impreciso al ir sumando contratos incrementales).
    Nunca rompe el resto del job: cualquier fallo puntual (símbolo sin chain, sin contrato
    encontrado) se loguea y se sigue con el resto — mismo criterio del resto de Sección 6."""
    since = datetime.now(timezone.utc) - timedelta(minutes=REAL_TRADE_LOOKBACK_MINUTES)
    try:
        orders = broker.get_recent_filled_orders(since)
    except Exception:
        logger.exception("Fallo al traer órdenes llenadas recientes; se omite la detección de operaciones esta corrida")
        return []

    already_alerted = repo.get_alerted_order_leg_keys(conn)

    generated: list[dict] = []
    for order in orders:
        if _is_roll(order):
            logger.info("Orden %s: pata OPENING + CLOSING en la misma orden — tratada como ROLL, sin alerta", order.order_id)
            continue
        for leg in order.legs:
            if leg.instruction != "SELL_TO_OPEN":
                continue  # solo ventas nuevas de prima — comprar opciones queda fuera (motor de INGRESO)
            key = (order.order_id, leg.occ_symbol)
            if key in already_alerted:
                continue
            try:
                result = _build_and_persist_real_trade_alert(
                    broker, conn, settings, today, order, leg, share_positions, anthropic_api_key, finnhub_api_key
                )
                if result:
                    generated.append(result)
            except Exception:
                logger.exception("Fallo al generar alerta de operación real para %s; se sigue con el resto", leg.occ_symbol)

    return generated
