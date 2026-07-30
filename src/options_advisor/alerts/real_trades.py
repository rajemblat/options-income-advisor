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


def _classify_opening_legs(legs: list[FilledOrderLeg]) -> tuple[str, list[FilledOrderLeg]] | None:
    """Clasifica las patas de APERTURA de una orden según su composición REAL — no una elegida
    por el motor — para reconocer estrategias de varias patas armadas en una sola orden
    combinada de Schwab (Iron Condor, credit spreads). Bug real 2026-07-29: un Iron Condor de 4
    patas (AMD) se detectaba como Cash-Secured Put de 1 sola pata porque el caller procesaba
    cada pata VENDIDA por separado, ignorando las patas COMPRADAS que definen el riesgo
    acotado. None si la composición no matchea ninguna de las estrategias reconocidas acá — el
    caller degrada al camino de 1 pata vendida por vez (comportamiento anterior, sigue correcto
    para posiciones genuinamente desnudas). Alcance deliberadamente acotado a estrategias de
    INGRESO (crédito neto: la pata vendida cobra más que lo que cuesta la comprada) — un debit
    spread no es una venta de prima, queda fuera de este detector igual que antes."""
    opening = [leg for leg in legs if leg.position_effect == "OPENING"]
    parsed: dict[str, tuple] = {}
    for leg in opening:
        p = parse_occ_option_symbol(leg.occ_symbol)
        if p is None:
            return None
        parsed[leg.occ_symbol] = p

    sells = [leg for leg in opening if leg.instruction == "SELL_TO_OPEN"]
    buys = [leg for leg in opening if leg.instruction == "BUY_TO_OPEN"]
    if len(sells) + len(buys) != len(opening):
        return None  # alguna pata con otra instrucción — no forzar una clasificación a medias

    sell_puts = [leg for leg in sells if parsed[leg.occ_symbol][2] == "put"]
    sell_calls = [leg for leg in sells if parsed[leg.occ_symbol][2] == "call"]
    buy_puts = [leg for leg in buys if parsed[leg.occ_symbol][2] == "put"]
    buy_calls = [leg for leg in buys if parsed[leg.occ_symbol][2] == "call"]

    if len(opening) == 4 and len(sell_puts) == 1 and len(buy_puts) == 1 and len(sell_calls) == 1 and len(buy_calls) == 1:
        return c.IRON_CONDOR, [sell_puts[0], buy_puts[0], sell_calls[0], buy_calls[0]]

    if len(opening) == 2 and len(sell_puts) == 1 and len(buy_puts) == 1:
        sell_leg, buy_leg = sell_puts[0], buy_puts[0]
        return (c.BULL_PUT_SPREAD, [sell_leg, buy_leg]) if sell_leg.price > buy_leg.price else None

    if len(opening) == 2 and len(sell_calls) == 1 and len(buy_calls) == 1:
        sell_leg, buy_leg = sell_calls[0], buy_calls[0]
        return (c.BEAR_CALL_SPREAD, [sell_leg, buy_leg]) if sell_leg.price > buy_leg.price else None

    return None


def _strikes_dict(strategy_type: str, legs: list[FilledOrderLeg], parsed: dict[str, tuple]) -> dict:
    """Mismas convenciones de nombre que `strategy/candidates.py::_build_iron_condor`/
    `_build_vertical_spread` — informativo (solo lo lee el narrador), sin esquema estricto."""
    if strategy_type == c.IRON_CONDOR:
        sell_put, buy_put, sell_call, buy_call = legs
        return {
            "put_short_strike": parsed[sell_put.occ_symbol][3],
            "put_long_strike": parsed[buy_put.occ_symbol][3],
            "call_short_strike": parsed[sell_call.occ_symbol][3],
            "call_long_strike": parsed[buy_call.occ_symbol][3],
        }
    if strategy_type in (c.BULL_PUT_SPREAD, c.BEAR_CALL_SPREAD):
        sell_leg, buy_leg = legs
        return {"short_strike": parsed[sell_leg.occ_symbol][3], "long_strike": parsed[buy_leg.occ_symbol][3]}
    return {}


def _build_and_persist_roll_closed_leg(conn: sqlite3.Connection, order: FilledOrder, leg: FilledOrderLeg, today: date) -> dict | None:
    """Registro LIVIANO de la pata que se CERRÓ como parte de un roll (pedido 2026-07-30, ver
    `_is_roll`) — a propósito sin cotización en vivo, sin cadena de opciones, sin cálculo de
    P&L: ya no es una posición activa, es historial de qué se cerró y a qué precio. Alcanza con
    lo que ya trae la propia orden. `entry_price` en este registro es el precio REAL de CIERRE
    (no de entrada — el nombre del campo se reusa tal cual está en `RealTradeAlert`, el
    `leg_role="roll_closed"` es lo que le avisa a la UI que lo interprete distinto)."""
    parsed = parse_occ_option_symbol(leg.occ_symbol)
    if parsed is None:
        logger.warning("%s: símbolo OCC no reconocido; se omite el registro de pata cerrada del roll", leg.occ_symbol)
        return None
    underlying_symbol, expiration, option_type, strike = parsed
    quantity = int(round(leg.quantity))
    if quantity <= 0:
        return None
    side = "sell" if leg.instruction == "SELL_TO_CLOSE" else "buy"
    trade = RealTradeAlert(
        account_number=order.account_number,
        occ_symbol=leg.occ_symbol,
        symbol=underlying_symbol,
        trade_date=today,
        trade_ts=datetime.now(),
        strategy_type=c.ROLL_CLOSED_LEG,
        option_type=option_type,
        strike=strike,
        expiration_date=expiration,
        quantity=quantity,
        entry_price=leg.price,
        order_id=order.order_id,
        legs=[
            {
                "side": side,
                "quantity": quantity,
                "option_type": option_type,
                "strike": strike,
                "expiration": expiration.isoformat(),
                "premium": leg.price,
            }
        ],
        leg_role="roll_closed",
    )
    if repo.insert_real_trade_alert(conn, trade) is None:
        logger.info(
            "%s (orden %s) ya fue registrada por otra corrida concurrente; se omite duplicado", leg.occ_symbol, order.order_id
        )
        return None
    return {"symbol": underlying_symbol, "strategy_type": c.ROLL_CLOSED_LEG, "quantity": quantity, "narrative": None}


def _build_and_persist_real_trade_alert(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    settings: Settings,
    today: date,
    order: FilledOrder,
    legs: list[FilledOrderLeg],
    strategy_type_override: str | None,
    share_positions: dict[str, int],
    anthropic_api_key: str | None,
    finnhub_api_key: str | None,
    leg_role: str | None = None,
) -> dict | None:
    """`legs`: 1 pata (posición desnuda, `strategy_type_override=None` — se resuelve por
    option_type/acciones en cartera como siempre) o varias patas YA CLASIFICADAS por
    `_classify_opening_legs` (Iron Condor, credit spread — `strategy_type_override` fijo, no se
    re-resuelve). La primera pata de la lista es la "principal" (siempre la vendida ancla —
    mismo criterio que `compute_coverage`/`compute_historical_checks`): sus datos van en las
    columnas singulares de `real_trade_alerts` (occ_symbol/option_type/strike/entry_price), el
    resto de la composición completa vive en `legs_json` (ver `payoff.legs`, ya genérico a N
    patas). `leg_role="roll_opened"` (pedido 2026-07-30) cuando esta apertura es el lado NUEVO
    de un roll — mismo cálculo completo que una apertura común, solo cambia cómo la UI la
    agrupa/colorea junto a la pata cerrada correspondiente (mismo `order_id`)."""
    parsed: dict[str, tuple] = {}
    for leg in legs:
        p = parse_occ_option_symbol(leg.occ_symbol)
        if p is None:
            logger.warning("%s: símbolo OCC no reconocido; se omite la alerta de operación real", leg.occ_symbol)
            return None
        parsed[leg.occ_symbol] = p

    primary_leg = legs[0]
    underlying_symbol, expiration, primary_option_type, primary_strike = parsed[primary_leg.occ_symbol]
    contracts_added = int(round(primary_leg.quantity))
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
            quote_symbol, primary_leg.occ_symbol,
        )
        return None

    build_legs: list[tuple[str, object, int, float | None]] = []
    for leg in legs:
        _, leg_expiration, leg_option_type, leg_strike = parsed[leg.occ_symbol]
        contract = candidate_builder.find_contract(chain, leg_option_type, leg_expiration, leg_strike)
        if contract is None:
            logger.warning(
                "%s: no se encontró el contrato %s $%.2f %s en la cadena en vivo; se omite el cálculo de P&L de la operación real",
                underlying_symbol, leg_expiration, leg_strike, leg_option_type,
            )
            return None
        side = "sell" if leg.instruction == "SELL_TO_OPEN" else "buy"
        build_legs.append((side, contract, int(round(leg.quantity)), leg.price))

    if strategy_type_override is not None:
        strategy_type = strategy_type_override
        build = candidate_builder.build_from_real_legs(strategy_type, _strikes_dict(strategy_type, legs, parsed), build_legs)
    else:
        strategy_type = _resolve_strategy_type(primary_option_type, share_positions, underlying_symbol, contracts_added)
        build = candidate_builder.build_from_contract(strategy_type, build_legs[0][1], contracts_added, entry_price=primary_leg.price)
    payoff = payoff_calc.compute_payoff(build, quote.last_price, today, settings.market.risk_free_rate)

    recent_news = finnhub_client.get_recent_news(underlying_symbol, today, finnhub_api_key)
    next_earnings_date = repo.get_latest_next_earnings_date(conn, underlying_symbol)
    investor_profile = repo.get_investor_profile(conn)
    capital_available = investor_profile.capital_available if investor_profile else None

    context = build_real_trade_context(
        symbol=underlying_symbol,
        strategy_type=strategy_type,
        quantity=contracts_added,
        entry_price=primary_leg.price,
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

    historical_check, similar_check = backtest.compute_historical_checks(broker, quote_symbol, payoff.legs, payoff.underlying_price, payoff.dte)

    trade = RealTradeAlert(
        account_number=order.account_number,
        occ_symbol=primary_leg.occ_symbol,
        symbol=underlying_symbol,
        trade_date=today,
        trade_ts=datetime.now(),
        strategy_type=strategy_type,
        option_type=primary_option_type,
        strike=primary_strike,
        expiration_date=expiration,
        quantity=contracts_added,
        entry_price=primary_leg.price,
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
        similar_move_occurrences=similar_check.similar_occurrences if similar_check else None,
        similar_move_bigger_occurrences=similar_check.bigger_occurrences if similar_check else None,
        narrative_text=narrative_text,
        narrative_source=narrative_source,
        leg_role=leg_role,
    )
    if repo.insert_real_trade_alert(conn, trade) is None:
        # Carrera real entre 2 procesos de detección (incidente 2026-07-29, ver
        # repository.py::insert_real_trade_alert) — otra corrida concurrente ya insertó esta
        # misma (order_id, occ_symbol) primero. No es un error: se omite en silencio, sin
        # notificar de nuevo la misma operación.
        logger.info(
            "%s (orden %s) ya fue detectada por otra corrida concurrente; se omite duplicado", primary_leg.occ_symbol, order.order_id
        )
        return None
    notifier.notify_real_trade(underlying_symbol, strategy_type, narrative_text)
    return {"symbol": underlying_symbol, "strategy_type": strategy_type, "quantity": contracts_added, "narrative": narrative_text}


def _process_opening_legs(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    settings: Settings,
    today: date,
    order: FilledOrder,
    share_positions: dict[str, int],
    anthropic_api_key: str | None,
    finnhub_api_key: str | None,
    already_alerted: set[tuple[int, str]],
    leg_role: str | None,
) -> list[dict]:
    """Arma la(s) alerta(s) de apertura de una orden — apertura común (`leg_role=None`) o el
    lado NUEVO de un roll (`leg_role="roll_opened"`, pedido 2026-07-30): mismo cálculo completo
    en ambos casos, la única diferencia es cómo la UI la agrupa/colorea después. Primero intenta
    reconocer la composición completa (Iron Condor, credit spread); si no matchea, degrada a 1
    alerta por cada pata vendida suelta (comportamiento de siempre)."""
    generated: list[dict] = []
    classified = _classify_opening_legs(order.legs)
    if classified is not None:
        strategy_type, group_legs = classified
        primary_leg = group_legs[0]
        key = (order.order_id, primary_leg.occ_symbol)
        if key not in already_alerted:
            try:
                result = _build_and_persist_real_trade_alert(
                    broker, conn, settings, today, order, group_legs, strategy_type,
                    share_positions, anthropic_api_key, finnhub_api_key, leg_role=leg_role,
                )
                if result:
                    generated.append(result)
            except Exception:
                logger.exception("Fallo al generar alerta de operación real multi-pata para la orden %s; se sigue con el resto", order.order_id)
        return generated

    for leg in order.legs:
        if leg.instruction != "SELL_TO_OPEN":
            continue  # solo ventas nuevas de prima — comprar opciones queda fuera (motor de INGRESO)
        key = (order.order_id, leg.occ_symbol)
        if key in already_alerted:
            continue
        try:
            result = _build_and_persist_real_trade_alert(
                broker, conn, settings, today, order, [leg], None,
                share_positions, anthropic_api_key, finnhub_api_key, leg_role=leg_role,
            )
            if result:
                generated.append(result)
        except Exception:
            logger.exception("Fallo al generar alerta de operación real para %s; se sigue con el resto", leg.occ_symbol)
    return generated


def detect_and_alert_real_trades(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    settings: Settings,
    today: date,
    share_positions: dict[str, int],
    anthropic_api_key: str | None,
    finnhub_api_key: str | None,
    lookback_minutes: int | None = None,
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
    encontrado) se loguea y se sigue con el resto — mismo criterio del resto de Sección 6.

    Rolls (pedido 2026-07-30, cambio de alcance sobre la Fase 1 — antes se saltaban del todo):
    una orden con pata(s) OPENING + pata(s) CLOSING (`_is_roll`) ahora genera DOS tipos de
    registro que comparten `order_id` — la(s) pata(s) CERRADAS como registro liviano
    (`_build_and_persist_roll_closed_leg`, sin P&L propio) y la(s) pata(s) NUEVAS con el mismo
    cálculo completo que una apertura común (`_process_opening_legs(..., leg_role="roll_opened")`).
    La UI (`dashboard/components.py`) las agrupa visualmente por `order_id` para mostrar "lo que
    cerraste" y "lo que abriste en su lugar" juntos.

    `lookback_minutes` opcional (default `REAL_TRADE_LOOKBACK_MINUTES`) — usado por
    `scheduler/healthcheck.py` para pedir una ventana más ancha en el catch-up inmediato
    después de reparar un scheduler colgado (incidente 2026-07-29), cubriendo todo el tiempo
    que estuvo mudo en vez de solo los últimos 15 minutos."""
    since = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes or REAL_TRADE_LOOKBACK_MINUTES)
    try:
        orders = broker.get_recent_filled_orders(since)
    except Exception:
        logger.exception("Fallo al traer órdenes llenadas recientes; se omite la detección de operaciones esta corrida")
        return []

    already_alerted = repo.get_alerted_order_leg_keys(conn)

    generated: list[dict] = []
    for order in orders:
        if _is_roll(order):
            for leg in order.legs:
                if leg.position_effect != "CLOSING":
                    continue
                key = (order.order_id, leg.occ_symbol)
                if key in already_alerted:
                    continue
                try:
                    result = _build_and_persist_roll_closed_leg(conn, order, leg, today)
                    if result:
                        generated.append(result)
                except Exception:
                    logger.exception("Fallo al registrar la pata cerrada del roll para %s; se sigue con el resto", leg.occ_symbol)

            generated.extend(_process_opening_legs(
                broker, conn, settings, today, order, share_positions, anthropic_api_key, finnhub_api_key,
                already_alerted, leg_role="roll_opened",
            ))
            continue

        generated.extend(_process_opening_legs(
            broker, conn, settings, today, order, share_positions, anthropic_api_key, finnhub_api_key,
            already_alerted, leg_role=None,
        ))

    return generated
