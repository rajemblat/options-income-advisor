from __future__ import annotations

import sqlite3
from datetime import date

from options_advisor.broker.models import OptionChain
from options_advisor.simulator.positions import current_contract_value

# Simulador de Trading Automático (pedido 2026-08-02): transforma filas crudas de
# simulated_positions/simulated_equity_history en filas planas para las tablas/gráfico de
# pages/12_simulador.py — lógica pura, sin Streamlit, mismo patrón que
# dashboard/scanner_table.py.

_CLOSE_REASON_LABELS = {"profit_target": "Objetivo 30%", "expired": "Vencimiento"}


def build_open_position_rows(rows: list[sqlite3.Row], live_data: dict[str, tuple[float | None, OptionChain | None]]) -> list[dict]:
    """`live_data`: symbol -> (precio actual del subyacente, cadena de opciones en vivo), ya
    pedidos por la página con los helpers cacheados de components.py. P&L EN VIVO cuando hay
    precio disponible — si no (símbolo sin quote hoy), cae al último P&L marcado por el
    scheduler (`last_unrealized_pnl`). Nunca escribe nada ni cierra posiciones: eso es
    exclusivo de `simulator/engine.py` corriendo en el scheduler, no de mirar el dashboard."""
    result = []
    for row in rows:
        symbol = row["symbol"]
        underlying_price, chain = live_data.get(symbol, (None, None))
        strike = row["strike"]
        expiration = date.fromisoformat(row["expiration_date"])
        quantity = row["quantity"]
        entry_premium = row["entry_premium"]
        premium_collected = entry_premium * 100 * quantity

        if underlying_price is not None:
            current_value = current_contract_value(chain, strike, expiration, underlying_price)
            unrealized_pnl = round((entry_premium - current_value) * 100 * quantity, 2)
        else:
            current_value = None
            unrealized_pnl = row["last_unrealized_pnl"]

        pct_of_premium = round(unrealized_pnl / premium_collected * 100, 1) if unrealized_pnl is not None and premium_collected > 0 else None

        result.append(
            {
                "Symbol": symbol,
                "Strike": strike,
                "Vencimiento": row["expiration_date"],
                "Cantidad": quantity,
                "Fecha apertura": row["entry_date"],
                "Prima cobrada": entry_premium,
                "Valor actual": current_value,
                "P&L no realizado": unrealized_pnl,
                "% s/prima": pct_of_premium,
            }
        )
    return result


def build_closed_position_rows(rows: list[sqlite3.Row]) -> list[dict]:
    result = []
    for row in rows:
        result.append(
            {
                "Symbol": row["symbol"],
                "Strike": row["strike"],
                "Vencimiento": row["expiration_date"],
                "Cantidad": row["quantity"],
                "Fecha apertura": row["entry_date"],
                "Fecha cierre": row["close_date"],
                "Prima cobrada": row["entry_premium"],
                "Prima de cierre": row["close_premium"],
                "P&L realizado": row["realized_pnl"],
                "Motivo": _CLOSE_REASON_LABELS.get(row["close_reason"], row["close_reason"]),
            }
        )
    return result


def build_equity_curve_rows(rows: list[sqlite3.Row]) -> list[dict]:
    return [
        {"Fecha": r["snapshot_date"], "Equity": r["equity"], "Cash": r["cash"], "P&L no realizado": r["unrealized_pnl"]}
        for r in rows
    ]
