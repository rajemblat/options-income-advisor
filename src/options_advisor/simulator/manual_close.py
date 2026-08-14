"""Cierre MANUAL de posiciones del SIMULADOR (paper), a pedido del usuario desde el chat de Moshe
(usuario 2026-08-11: 'cerrá los iron del simulador, la ganancia fue rápida'). Cierra AL INSTANTE a su
valor actual (recomprando a mid), con motivo 'manual', reusando la MISMA valuación que el simulador usa
en su cierre automático. No pasa por reglas: es una decisión del usuario para lockear la ganancia (o
cortar) cuando quiera. Soporta puts (cash-secured) e iron condors. Nunca rompe: devuelve (ok, pnl, nota).
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from options_advisor.simulator import iron_condor
from options_advisor.simulator.positions import _close_fill, _close_position, _find_put, current_contract_value
from options_advisor.storage import repository as repo

logger = logging.getLogger(__name__)

_CHAIN_RANGE = (0, 90)


def close_simulator_position(conn, broker, settings, as_of: date, kind: str, *,
                             symbol: str | None = None, strike: float | None = None,
                             position_id: int | None = None) -> tuple[bool, float | None, str]:
    """Cierra una posición del simulador a su valor actual. `kind`: 'put' | 'condor'. Se identifica por
    `position_id` o por `symbol`+`strike`. Devuelve (ok, pnl_realizado, nota)."""
    kind = (kind or "").strip().lower()
    try:
        if kind in ("put", "csp", "cash_secured_put"):
            return _close_put(conn, broker, settings, as_of, symbol=symbol, strike=strike, position_id=position_id)
        if kind in ("condor", "iron_condor", "iron"):
            return _close_condor(conn, broker, settings, as_of, symbol=symbol, strike=strike, position_id=position_id)
        return (False, None, f"tipo de posición no soportado: {kind}")
    except Exception as e:
        logger.exception("Cierre manual simulador: fallo cerrando %s", kind)
        return (False, None, f"error al cerrar: {e}")


def _close_put(conn, broker, settings, as_of, *, symbol, strike, position_id):
    rows = repo.get_open_simulated_positions(conn)
    row = None
    if position_id is not None:
        row = next((r for r in rows if r["id"] == int(position_id)), None)
    if row is None and symbol is not None:
        row = next((r for r in rows
                    if str(r["symbol"]).upper() == str(symbol).upper()
                    and (strike is None or abs(float(r["strike"]) - float(strike)) < 1e-6)), None)
    if row is None:
        return (False, None, "no encontré esa posición de put abierta en el simulador")

    sym = row["symbol"]
    strike_v = float(row["strike"])
    expiration = date.fromisoformat(row["expiration_date"])
    try:
        chain = broker.get_option_chain(sym, expiration_range_days=_CHAIN_RANGE)
        quote = broker.get_quote(sym)
    except Exception:
        return (False, None, f"no pude pedir la cadena/precio de {sym} ahora, reintentá en un momento")

    spot = quote.last_price
    marked = current_contract_value(chain, strike_v, expiration, spot)
    expired = expiration <= as_of
    if marked is None and not expired:
        return (False, None, f"no encontré el contrato de {sym} {strike_v:g} en la cadena, reintentá")
    close_value = marked if marked is not None else max(strike_v - spot, 0.0)
    ct = _find_put(chain, strike_v, expiration)
    if ct is not None and not expired:
        close_value = _close_fill(ct, settings.simulator)
    realized = _close_position(conn, row, close_value, as_of, "manual", settings.simulator)
    return (True, realized, f"{sym} put {strike_v:g} cerrado — P&L ${realized:+,.2f}")


def _close_condor(conn, broker, settings, as_of, *, symbol, strike, position_id):
    rows = repo.get_open_condor_positions(conn)
    row = None
    if position_id is not None:
        row = next((r for r in rows if r["id"] == int(position_id)), None)
    if row is None and symbol is not None:
        row = next((r for r in rows
                    if str(r["underlying"]).upper().lstrip("$") == str(symbol).upper().lstrip("$")
                    and (strike is None or abs(float(r["short_put_strike"]) - float(strike)) < 1e-6)), None)
    if row is None and len(rows) == 1:
        row = rows[0]  # un solo condor abierto: lo tomamos
    if row is None:
        return (False, None, "no encontré ese iron condor abierto en el simulador")

    under = row["underlying"]
    expiration = date.fromisoformat(row["expiration_date"])
    expired = expiration < as_of
    try:
        chain = broker.get_option_chain(under, expiration_range_days=_CHAIN_RANGE)
        quote = broker.get_quote(under)
    except Exception:
        return (False, None, f"no pude pedir la cadena/precio de {under} ahora, reintentá en un momento")

    close_value = iron_condor.condor_close_value(
        chain, row["short_put_strike"], row["short_call_strike"], row["long_put_strike"], row["long_call_strike"])
    if close_value is None:
        if not expired:
            return (False, None, f"no encontré todas las patas del condor de {under} en la cadena, reintentá")
        close_value = iron_condor.condor_intrinsic_close_value(
            quote.last_price, row["short_put_strike"], row["short_call_strike"],
            row["long_put_strike"], row["long_call_strike"])
    comm = getattr(settings.simulator, "commission_per_contract", 0.0) or 0.0
    realized = round(iron_condor.condor_unrealized(row["entry_net_credit"], close_value) - comm * 4 * 2, 2)
    repo.close_condor_position(conn, row["id"], as_of, close_value, "manual", realized, close_ts=datetime.now())
    return (True, realized, f"Iron Condor {under} cerrado — P&L ${realized:+,.2f}")


def close_all_simulator(conn, broker, settings, as_of: date) -> list[tuple[bool, float | None, str]]:
    """Cierra TODAS las posiciones abiertas del simulador (puts + condors) a valor actual. Devuelve la
    lista de resultados por posición."""
    results = []
    for r in repo.get_open_simulated_positions(conn):
        results.append(close_simulator_position(conn, broker, settings, as_of, "put", position_id=r["id"]))
    for r in repo.get_open_condor_positions(conn):
        results.append(close_simulator_position(conn, broker, settings, as_of, "condor", position_id=r["id"]))
    return results
