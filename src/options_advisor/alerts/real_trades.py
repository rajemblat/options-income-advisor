from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime

from options_advisor.alerts import notifier
from options_advisor.alerts.narrator import build_real_trade_context, narrate_real_trade
from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import AccountPosition, index_quote_symbol
from options_advisor.config import Settings
from options_advisor.market_context import finnhub_client
from options_advisor.storage import repository as repo
from options_advisor.storage.models import PositionSnapshot, RealTradeAlert
from options_advisor.strategy import candidates as candidate_builder
from options_advisor.strategy import constants as c
from options_advisor.strategy import payoff as payoff_calc

logger = logging.getLogger(__name__)

MIN_SHARES_FOR_COVERED_CALL = 100  # 1 contrato de call cubre 100 acciones


def _detect_new_short_trades(
    positions: list[AccountPosition], previous: dict[tuple[str, str], float]
) -> list[tuple[AccountPosition, int]]:
    """(posición, contratos_nuevos_vendidos_esta_corrida) por cada posición de opción cuya
    cantidad corta CRECIÓ desde el snapshot anterior — símbolo nuevo (no estaba antes, previous
    default 0.0) o cantidad más negativa que antes = venta nueva ("sell to open"), diseño
    confirmado por el usuario en BACKLOG.md punto 1 (Pestaña Operaciones). Solo posiciones
    CORTAS (quantity < 0): comprar opciones (protección, debit spreads) no es una operación de
    venta de prima, fuera de este detector — el resto del motor ya es un asesor de INGRESO
    (venta de prima), no de compra de opciones."""
    detected = []
    for position in positions:
        if position.asset_type != "OPTION" or position.quantity >= 0:
            continue
        key = (position.account_number, position.symbol)
        prev_qty = previous.get(key, 0.0)
        if position.quantity < prev_qty:
            contracts_added = int(round(prev_qty - position.quantity))
            if contracts_added > 0:
                detected.append((position, contracts_added))
    return detected


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


def _build_and_persist_real_trade_alert(
    broker: BrokerClient,
    conn: sqlite3.Connection,
    settings: Settings,
    today: date,
    position: AccountPosition,
    contracts_added: int,
    share_positions: dict[str, int],
    anthropic_api_key: str | None,
    finnhub_api_key: str | None,
) -> dict | None:
    underlying_symbol = position.underlying_symbol
    option_type = position.option_type
    strike = position.strike
    expiration = position.expiration
    if not underlying_symbol or not option_type or strike is None or expiration is None:
        logger.warning(
            "Posición de opción %s sin datos OCC parseados; se omite la alerta de operación real", position.symbol
        )
        return None

    quote_symbol = index_quote_symbol(underlying_symbol)
    try:
        quote = broker.get_quote(quote_symbol)
        dte_upper_bound = max((expiration - today).days, 0) + 5
        chain = broker.get_option_chain(quote_symbol, expiration_range_days=(0, dte_upper_bound))
    except Exception:
        logger.exception(
            "Fallo al pedir cotización/cadena en vivo de %s; se omite la alerta de operación real de %s",
            quote_symbol, position.symbol,
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
    build = candidate_builder.build_from_contract(strategy_type, contract, contracts_added)
    payoff = payoff_calc.compute_payoff(build, quote.last_price, today, settings.market.risk_free_rate)

    recent_news = finnhub_client.get_recent_news(underlying_symbol, today, finnhub_api_key)
    next_earnings_date = repo.get_latest_next_earnings_date(conn, underlying_symbol)
    investor_profile = repo.get_investor_profile(conn)
    capital_available = investor_profile.capital_available if investor_profile else None

    context = build_real_trade_context(
        symbol=underlying_symbol,
        strategy_type=strategy_type,
        quantity=contracts_added,
        entry_price=position.average_price,
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

    trade = RealTradeAlert(
        account_number=position.account_number,
        occ_symbol=position.symbol,
        symbol=underlying_symbol,
        trade_date=today,
        trade_ts=datetime.now(),
        strategy_type=strategy_type,
        option_type=option_type,
        strike=strike,
        expiration_date=expiration,
        quantity=contracts_added,
        entry_price=position.average_price,
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
    """Detecta operaciones reales nuevas (venta de opciones) en la cuenta Schwab real desde la
    corrida anterior del scheduler y genera una alerta con el mismo formato completo que las
    alertas de candidatos (P&L, breakeven, POP, cobertura, noticias, comentario del narrador)
    pero aplicada a la posición YA ABIERTA — Pestaña Operaciones del backlog, pedido
    2026-07-25. Se llama una vez por corrida del scheduler (no por símbolo), igual que
    `broker.get_all_share_positions()` en `scheduler/jobs.py`. Nunca rompe el resto del job:
    cualquier fallo puntual (símbolo sin chain, sin contrato encontrado) se loguea y se sigue
    con el resto — mismo criterio del resto de Sección 6."""
    try:
        positions = broker.get_all_positions()
    except Exception:
        logger.exception("Fallo al traer posiciones reales; se omite la detección de operaciones esta corrida")
        return []

    previous = repo.get_position_snapshots(conn)
    previous_underlyings = repo.get_position_snapshot_underlyings(conn)
    new_trades = _detect_new_short_trades(positions, previous)

    # Alcance de Fase 1 (aclarado por el usuario 2026-07-28): la Pestaña Operaciones SOLO debe
    # alertar APERTURAS genuinas — nunca cierres, y tampoco ROLLS (cerrar una opción y abrir
    # otra distinta del MISMO subyacente, típicamente en una sola orden combinada de Schwab).
    # Un roll técnicamente SÍ pasa por _detect_new_short_trades (la pata nueva es un símbolo
    # OCC nunca visto antes, así que "previous.get(key, 0.0)" da 0 y se ve como apertura desde
    # cero) — confirmado en la práctica: un roll real de SOFI generó una alerta indebida la
    # noche del 2026-07-27, antes de esta aclaración de alcance.
    #
    # Heurística de Fase 1 (documentada como tal, con su límite conocido — ver BACKLOG.md):
    # si el MISMO subyacente tiene una posición que estaba corta en la corrida anterior y ya
    # no aparece en absoluto en la corrida actual (cerrada del todo, no solo reducida), CUALQUIER
    # apertura nueva de ESE MISMO subyacente en esta misma corrida se trata como parte de un
    # roll y se suprime (no genera alerta). Límite conocido y aceptado: si el usuario cierra una
    # posición y, sin relación, abre una posición NUEVA e independiente del mismo subyacente
    # dentro de la misma ventana de 3 minutos del cron, también se suprimiría — un falso
    # negativo infrecuente, preferible a alertar rolls por error. Fase 2 (no implementada):
    # usar el endpoint /orders de Schwab para confirmar que el cierre y la apertura vinieron de
    # la MISMA orden combinada, en vez de inferirlo por coincidencia de subyacente/ventana.
    current_short_keys = {(p.account_number, p.symbol) for p in positions if p.asset_type == "OPTION" and p.quantity < 0}
    closed_underlyings_by_account: dict[str, set[str]] = {}
    for key in previous:
        if key in current_short_keys:
            continue  # sigue abierta (entera o parcialmente reducida), no se cerró del todo
        account_number, _occ_symbol = key
        underlying = previous_underlyings.get(key)
        if underlying:
            closed_underlyings_by_account.setdefault(account_number, set()).add(underlying)

    # Reemplaza el snapshot completo con el estado actual de posiciones CORTAS de opciones —
    # también "olvida" posiciones cerradas (ya no aparecen en `positions`), así si se reabre el
    # mismo contrato más adelante se detecta como operación nueva en vez de compararse contra
    # un número viejo más grande (ver storage/repository.py::replace_position_snapshots).
    current_shorts = [
        PositionSnapshot(
            account_number=p.account_number, symbol=p.symbol, quantity=p.quantity,
            snapshot_ts=datetime.now(), underlying_symbol=p.underlying_symbol,
        )
        for p in positions
        if p.asset_type == "OPTION" and p.quantity < 0
    ]
    repo.replace_position_snapshots(conn, current_shorts)

    generated: list[dict] = []
    for position, contracts_added in new_trades:
        if position.underlying_symbol and position.underlying_symbol in closed_underlyings_by_account.get(position.account_number, set()):
            logger.info(
                "%s: subyacente %s tuvo un cierre en esta misma corrida — tratada como probable ROLL, sin alerta (Fase 1)",
                position.symbol, position.underlying_symbol,
            )
            continue
        try:
            result = _build_and_persist_real_trade_alert(
                broker, conn, settings, today, position, contracts_added, share_positions, anthropic_api_key, finnhub_api_key
            )
            if result:
                generated.append(result)
        except Exception:
            logger.exception("Fallo al generar alerta de operación real para %s; se sigue con el resto", position.symbol)

    return generated
