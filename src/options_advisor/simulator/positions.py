from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from options_advisor.broker.models import OptionChain, OptionContract
from options_advisor.config import SimulatorSettings
from options_advisor.simulator import rules
from options_advisor.storage import repository as repo

CONTRACT_MULTIPLIER = 100
_STRIKE_TOL = 0.01  # match de strike con tolerancia (evita fallos por igualdad exacta de float)


def evaluate_put_close(
    entry_premium: float,
    current_value: float,
    strike: float,
    underlying_price: float,
    dte: int,
    age_days: int,
    settings: SimulatorSettings,
    *,
    important_news: bool = False,
) -> tuple[bool, str | None]:
    """Decisión PURA de cierre de un put vendido — las MISMAS reglas del simulador, sin tocar la base.
    Reusada por `mark_position` (simulador) Y por el cierre REAL (usuario 2026-08-10: "que cierre con las
    mismas reglas del simulador"). Triggers ACTIVOS: stop-loss (múltiplo de prima), noticia importante
    cerca de vencimiento, objetivo de ganancia escalonado, o DTE mínimo. El vencimiento se maneja aparte
    (el que llama), porque una opción vencida ya no se recompra. Devuelve (cerrar, motivo)."""
    premium_collected = entry_premium * CONTRACT_MULTIPLIER
    unrealized_per_contract = (entry_premium - current_value) * CONTRACT_MULTIPLIER
    pnl_pct_of_premium = (unrealized_per_contract / premium_collected) if premium_collected > 0 else 0.0
    coverage_now = rules.put_coverage_pct(underlying_price, strike)
    target, force_news = rules.tiered_profit_target(coverage_now, dte, age_days, important_news, settings)
    # PISO de 30% al cierre automático POR GANANCIA (usuario 2026-08-11: "que no se cierre automático nada
    # menos del 30%; si quiero, cierro antes por el chat"). El objetivo de ganancia nunca baja del base
    # (30%): anula tanto la toma temprana de semana 1 (18%) como el "cualquier ganancia" cerca del strike.
    # NO afecta stop-loss (corta pérdidas), DTE mínimo (cierre por tiempo) ni noticia (protección).
    target = max(target, settings.profit_target_pct)
    stop_hit = settings.stop_loss_multiple > 0 and current_value >= entry_premium * (1 + settings.stop_loss_multiple)
    dte_close = settings.close_at_dte > 0 and dte <= settings.close_at_dte
    hit_target = pnl_pct_of_premium >= target
    if stop_hit or force_news or hit_target or dte_close:
        reason = ("stop_loss" if stop_hit else "news_close" if force_news
                  else "profit_target" if hit_target else "dte_close")
        return True, reason
    return False, None


@dataclass
class SizingResult:
    quantity: int
    collateral: float


def _tier_contracts(underlying_price: float, settings: SimulatorSettings) -> int:
    """Contratos por posición según el precio de la acción (usuario 2026-08-03): más contratos en
    acciones baratas. `<=low` → cheap (inclusive: "hasta $50 → 4"), (low, high) → mid, `>=high` →
    expensive (usuario 2026-08-07: "$400 o más → 1")."""
    if underlying_price <= settings.price_tier_low:
        return settings.contracts_cheap
    if underlying_price < settings.price_tier_high:
        return settings.contracts_mid
    return settings.contracts_expensive


def size_position(
    strike: float, underlying_price: float, premium: float, cash_available: float, account_equity: float, settings: SimulatorSettings
) -> SizingResult | None:
    """Cuántos contratos abrir. El capital por contrato es el margen NAKED (chico) o la garantía
    cash-secured, según `margin_mode`. Con `use_price_tier_sizing`, la cantidad la fija el tramo de
    precio de la acción (y solo la limita el cash disponible). Si no, usa el tope por % del EQUITY
    (`max_position_pct`). `None` si no entra ni 1 contrato."""
    per_contract = rules.per_contract_cost(underlying_price, strike, premium, settings)
    if per_contract <= 0:
        return None
    if settings.use_price_tier_sizing:
        quantity = _tier_contracts(underlying_price, settings)
    else:
        if account_equity <= 0:
            return None
        max_collateral = account_equity * settings.max_position_pct
        quantity = int(max_collateral // per_contract)
    if quantity < 1:
        return None
    collateral = quantity * per_contract
    if collateral > cash_available:
        quantity = int(cash_available // per_contract)
        if quantity < 1:
            return None
        collateral = quantity * per_contract
    return SizingResult(quantity=quantity, collateral=collateral)


def _open_fill(contract: OptionContract, settings: SimulatorSettings | None) -> float:
    """Prima recibida al VENDER para abrir, negociando con orden límite HACIA el ask
    (vender lo más caro posible): mid + fill_edge_pct*(ask - mid). edge=0 → mid, edge=1 → ask."""
    mid = contract.mid_price
    if settings is None:
        return mid
    return round(mid + settings.fill_edge_pct * (contract.ask - mid), 4)


def _close_fill(contract: OptionContract, settings: SimulatorSettings | None) -> float:
    """Precio pagado al COMPRAR para cerrar, negociando con orden límite HACIA el bid
    (comprar lo más barato posible): mid - fill_edge_pct*(mid - bid). edge=0 → mid, edge=1 → bid."""
    mid = contract.mid_price
    if settings is None:
        return mid
    return round(mid - settings.fill_edge_pct * (mid - contract.bid), 4)


def open_position(
    conn: sqlite3.Connection,
    symbol: str,
    contract: OptionContract,
    quantity: int,
    collateral: float,
    entry_date: date,
    settings: SimulatorSettings | None = None,
) -> int:
    """Abre la posición y ajusta el cash: reserva la garantía cash-secured (strike*100*qty),
    acredita la prima cobrada (al bid si hay fills bid/ask) y descuenta comisiones."""
    premium = _open_fill(contract, settings)
    commission = (settings.commission_per_contract * quantity) if settings else 0.0
    position_id = repo.insert_simulated_position(
        conn,
        symbol=symbol,
        strategy_type="cash_secured_put",
        strike=contract.strike,
        expiration_date=contract.expiration,
        quantity=quantity,
        entry_date=entry_date,
        entry_premium=premium,
        collateral=collateral,
        entry_ts=datetime.now(),
    )
    account = repo.get_simulated_account(conn)
    new_cash = account["cash"] - collateral + premium * CONTRACT_MULTIPLIER * quantity - commission
    repo.update_simulated_account_cash(conn, new_cash)
    return position_id


def _find_put(chain: OptionChain | None, strike: float, expiration: date) -> OptionContract | None:
    """Busca el MISMO put en la cadena con tolerancia en el strike (no igualdad exacta de float)."""
    if chain is None:
        return None
    for ct in chain.contracts:
        if ct.option_type == "put" and ct.expiration == expiration and abs(ct.strike - strike) < _STRIKE_TOL:
            return ct
    return None


def current_contract_value(chain: OptionChain | None, strike: float, expiration: date, underlying_price: float) -> float | None:
    """Precio para marcar la posición hoy: el mid del MISMO put si sigue en la cadena en vivo;
    `None` si no aparece y todavía NO venció (hueco de datos — el que llama decide no marcar en
    vez de asumir intrínseco 0 y auto-cerrar por 'ganancia' falsa). Al vencimiento el que llama
    liquida a intrínseco."""
    ct = _find_put(chain, strike, expiration)
    if ct is not None:
        return ct.mid_price
    return None


def _close_position(
    conn: sqlite3.Connection,
    position_row: sqlite3.Row,
    close_value: float,
    close_date: date,
    reason: str,
    settings: SimulatorSettings | None = None,
) -> float:
    entry_premium = position_row["entry_premium"]
    quantity = position_row["quantity"]
    commission = (settings.commission_per_contract * quantity) if settings else 0.0
    # El P&L realizado descuenta comisión de IDA Y VUELTA (abrir + cerrar), no solo el cierre
    # (usuario 2026-08-07: "50 centavos al abrir y 50 al cerrar"). Así el realizado coincide con el
    # impacto total en el cash (la de apertura ya se descontó del cash al abrir; la de cierre acá).
    realized_pnl = round((entry_premium - close_value) * CONTRACT_MULTIPLIER * quantity - commission * 2, 2)
    repo.close_simulated_position(conn, position_row["id"], close_date, close_value, reason, realized_pnl, close_ts=datetime.now())
    account = repo.get_simulated_account(conn)
    new_cash = account["cash"] + position_row["collateral"] - close_value * CONTRACT_MULTIPLIER * quantity - commission
    repo.update_simulated_account_cash(conn, new_cash)
    return realized_pnl


def mark_position(
    conn: sqlite3.Connection,
    position_row: sqlite3.Row,
    chain: OptionChain | None,
    underlying_price: float,
    as_of: date,
    settings: SimulatorSettings,
    *,
    important_news: bool = False,
) -> dict:
    """Marca a mercado UNA posición y la cierra si corresponde. Cierres posibles: vencimiento,
    stop-loss (múltiplo de prima), salida escalonada de ganancia (30/40/50% según distancia al
    strike y cercanía a vencimiento), noticia importante cerca de vencimiento, o DTE mínimo. Si
    el contrato no está en la cadena y NO venció, NO cierra (hueco de datos) — evita el
    auto-cierre espurio al 100% que detectó la auditoría."""
    strike = position_row["strike"]
    expiration = date.fromisoformat(position_row["expiration_date"])
    quantity = position_row["quantity"]
    entry_premium = position_row["entry_premium"]

    expired = expiration <= as_of
    marked_value = current_contract_value(chain, strike, expiration, underlying_price)

    if marked_value is None and not expired:
        # Hueco de datos: el put no aparece hoy en la cadena pero no venció. No marcar ni cerrar.
        last = position_row["last_unrealized_pnl"] or 0.0
        return {"closed": False, "reason": None, "unrealized_pnl": last, "current_value": None, "skipped": True}

    current_value = marked_value if marked_value is not None else max(strike - underlying_price, 0.0)
    unrealized_pnl = round((entry_premium - current_value) * CONTRACT_MULTIPLIER * quantity, 2)
    premium_collected = entry_premium * CONTRACT_MULTIPLIER * quantity
    pnl_pct_of_premium = unrealized_pnl / premium_collected if premium_collected > 0 else 0.0

    dte = (expiration - as_of).days
    try:
        age_days = (as_of - date.fromisoformat(position_row["entry_date"])).days
    except (ValueError, TypeError, KeyError):
        age_days = 0
    _do_close, _active_reason = evaluate_put_close(
        entry_premium, current_value, strike, underlying_price, dte, age_days, settings, important_news=important_news)

    if expired or _do_close:
        reason = "expired" if expired else _active_reason
        # Recompra para cerrar: negociando hacia el bid (orden límite) si el contrato está en la
        # cadena; si no, al valor marcado; al vencimiento liquida a intrínseco.
        close_value = current_value
        if not expired:
            ct = _find_put(chain, strike, expiration)
            if ct is not None:
                close_value = _close_fill(ct, settings)
        realized_pnl = _close_position(conn, position_row, close_value, as_of, reason, settings)
        return {"closed": True, "reason": reason, "unrealized_pnl": realized_pnl, "current_value": close_value}

    repo.mark_simulated_position(conn, position_row["id"], as_of, unrealized_pnl)
    return {"closed": False, "reason": None, "unrealized_pnl": unrealized_pnl, "current_value": current_value}
