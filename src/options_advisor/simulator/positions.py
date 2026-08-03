from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from options_advisor.broker.models import OptionChain, OptionContract
from options_advisor.config import SimulatorSettings
from options_advisor.storage import repository as repo
from options_advisor.strategy.candidates import find_contract

CONTRACT_MULTIPLIER = 100


@dataclass
class SizingResult:
    quantity: int
    collateral: float


def size_position(strike: float, cash_available: float, account_equity: float, settings: SimulatorSettings) -> SizingResult | None:
    """Cuántos contratos abrir sin comprometer más de `max_position_pct` del EQUITY total de la
    cuenta simulada (no del cash libre — mantiene el tamaño relativo estable aunque el cash
    fluctúe con posiciones ya abiertas, el mismo criterio que un % de cartera fijo). `None` si
    ni 1 contrato entra dentro del límite, o si no alcanza el cash realmente disponible."""
    per_contract = strike * CONTRACT_MULTIPLIER
    if per_contract <= 0 or account_equity <= 0:
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


def open_position(
    conn: sqlite3.Connection, symbol: str, contract: OptionContract, quantity: int, collateral: float, entry_date: date
) -> int:
    """Abre la posición (fila 'open' en simulated_positions) y ajusta el cash de la cuenta:
    se reserva la garantía cash-secured (strike*100*qty) y se acredita la prima cobrada."""
    premium = contract.mid_price
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
    )
    account = repo.get_simulated_account(conn)
    new_cash = account["cash"] - collateral + premium * CONTRACT_MULTIPLIER * quantity
    repo.update_simulated_account_cash(conn, new_cash)
    return position_id


def _current_contract_value(chain: OptionChain | None, strike: float, expiration: date, underlying_price: float) -> float:
    """Precio para marcar la posición hoy: el mid de la MISMA opción si sigue en la cadena en
    vivo, o valor intrínseco si ya no aparece (vencimiento pasado/próximo fuera de la ventana
    pedida, o strike delistado) — mismo criterio de "degradar a intrínseco" que ya usa el resto
    del motor cuando falta un dato de mercado en vivo (ver strategy/candidates.py::find_contract)."""
    if chain is not None:
        ct = find_contract(chain, "put", expiration, strike)
        if ct is not None:
            return ct.mid_price
    return max(strike - underlying_price, 0.0)


def _close_position(conn: sqlite3.Connection, position_row: sqlite3.Row, close_value: float, close_date: date, reason: str) -> float:
    entry_premium = position_row["entry_premium"]
    quantity = position_row["quantity"]
    realized_pnl = round((entry_premium - close_value) * CONTRACT_MULTIPLIER * quantity, 2)
    repo.close_simulated_position(conn, position_row["id"], close_date, close_value, reason, realized_pnl)
    account = repo.get_simulated_account(conn)
    new_cash = account["cash"] + position_row["collateral"] - close_value * CONTRACT_MULTIPLIER * quantity
    repo.update_simulated_account_cash(conn, new_cash)
    return realized_pnl


def mark_position(
    conn: sqlite3.Connection,
    position_row: sqlite3.Row,
    chain: OptionChain | None,
    underlying_price: float,
    as_of: date,
    settings: SimulatorSettings,
) -> dict:
    """Marca a mercado UNA posición abierta hoy y la cierra automáticamente si corresponde:
    llegó al `profit_target_pct` de ganancia sobre la prima cobrada, o venció. Devuelve un
    resumen del resultado — usado por `simulator/engine.py` para acumular la curva de equity
    diaria y loguear cierres."""
    strike = position_row["strike"]
    expiration = date.fromisoformat(position_row["expiration_date"])
    quantity = position_row["quantity"]
    entry_premium = position_row["entry_premium"]

    current_value = _current_contract_value(chain, strike, expiration, underlying_price)
    unrealized_pnl = round((entry_premium - current_value) * CONTRACT_MULTIPLIER * quantity, 2)
    premium_collected = entry_premium * CONTRACT_MULTIPLIER * quantity
    pnl_pct_of_premium = unrealized_pnl / premium_collected if premium_collected > 0 else 0.0

    expired = expiration <= as_of
    hit_target = pnl_pct_of_premium >= settings.profit_target_pct
    if expired or hit_target:
        reason = "expired" if expired else "profit_target"
        realized_pnl = _close_position(conn, position_row, current_value, as_of, reason)
        return {"closed": True, "reason": reason, "unrealized_pnl": realized_pnl, "current_value": current_value}

    repo.mark_simulated_position(conn, position_row["id"], as_of, unrealized_pnl)
    return {"closed": False, "reason": None, "unrealized_pnl": unrealized_pnl, "current_value": current_value}
