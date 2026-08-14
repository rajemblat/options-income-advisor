from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from options_advisor.broker.models import OptionChain
from options_advisor.simulator.positions import current_contract_value


def _fmt_exec_time(ts: str | None, fallback_date: str | None) -> str:
    """Formatea el momento de ejecución como el broker (ej. '8/3/26 15:41:55'). Usa el timestamp
    exacto si existe; si no (posiciones viejas sin hora), cae a la fecha."""
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            return f"{dt.month}/{dt.day}/{dt.strftime('%y')} {dt.strftime('%H:%M:%S')}"
        except (ValueError, TypeError):
            pass
    return fallback_date or ""


def _broker_order_row(row: sqlite3.Row, is_open_leg: bool) -> dict:
    """Una fila de orden estilo broker. `is_open_leg=True` = apertura (SELL / TO OPEN / CREDITO,
    rojo); False = cierre (BUY / TO CLOSE / DEBITO, verde)."""
    qty = row["quantity"]
    if is_open_leg:
        price = row["entry_premium"]
        exec_time = _fmt_exec_time(row["entry_ts"], row["entry_date"])
        side, effect, cd, signed_qty = "SELL", "TO OPEN", "CREDITO", -qty
    else:
        price = row["close_premium"]
        exec_time = _fmt_exec_time(row["close_ts"], row["close_date"])
        side, effect, cd, signed_qty = "BUY", "TO CLOSE", "DEBITO", qty
    return {
        "Exec Time": exec_time,
        "Spread": "SINGLE",
        "Side": side,
        "Qty": signed_qty,
        "Pos Effect": effect,
        "Symbol": row["symbol"],
        "Exp": row["expiration_date"],
        "Strike": row["strike"],
        "Type": "PUT",
        "Price": price,
        "Net Price": price,
        "C/D": cd,
        "Order Type": "LMT",
        "Order ID": row["id"] if is_open_leg else f"{row['id']}C",
    }


def _find_put_in_chain(chain: OptionChain | None, strike: float, expiration: date):
    if chain is None:
        return None
    for c in chain.contracts:
        if c.option_type == "put" and c.expiration == expiration and abs(c.strike - strike) < 0.01:
            return c
    return None


def _unpack_live(value) -> tuple[float | None, float | None, OptionChain | None]:
    """Acepta live_data como (precio, %día, cadena) — o el formato viejo (precio, cadena) por
    compatibilidad — y devuelve siempre (precio_subyacente, %cambio_día, cadena)."""
    if value is None:
        return None, None, None
    if len(value) == 3:
        return value  # (precio, %día, cadena)
    price, chain = value
    return price, None, chain


def build_broker_open_position_rows(
    open_rows: list[sqlite3.Row], live_data: dict[str, tuple], as_of: date
) -> list[dict]:
    """Posiciones ABIERTAS con P/L EN VIVO, estilo monitor del broker: Qty (negativa = short),
    Precio ahora + % día del SUBYACENTE (pedido usuario 2026-08-04), Bid del contrato, Days (DTE),
    Trade Price (prima de entrada), Mark (mid actual), P/L %, P/L Open ($) y BP Effect (garantía).
    P/L Open de un put vendido = (entrada − mark)*100*qty: positivo cuando la opción perdió valor
    (ganás vos). Si no hay dato en vivo, cae al último marcado por el scheduler."""
    rows = []
    for row in open_rows:
        symbol = row["symbol"]
        underlying, change_pct, chain = _unpack_live(live_data.get(symbol))
        strike = row["strike"]
        qty = row["quantity"]
        entry = row["entry_premium"]
        expiration = date.fromisoformat(row["expiration_date"])
        dte = (expiration - as_of).days
        ct = _find_put_in_chain(chain, strike, expiration)
        mark = ct.mid_price if ct is not None else None
        bid = ct.bid if ct is not None else None
        if mark is not None:
            pl_open = round((entry - mark) * 100 * qty, 2)
            premium_collected = entry * 100 * qty
            pl_pct = round(pl_open / premium_collected * 100, 2) if premium_collected > 0 else None
        else:
            pl_open = row["last_unrealized_pnl"]
            pl_pct = None
        rows.append({
            "Symbol": symbol,
            "Precio ahora": underlying,
            "% día": change_pct,
            "Qty": -qty,
            "Bid": bid,
            "Days": dte,
            "Trade Price": entry,
            "Mark": mark,
            "P/L %": pl_pct,
            "P/L Open": pl_open,
            "BP Effect": row["collateral"],
            "Estado": "🟢 Ganando" if (pl_open or 0) > 0 else ("🔴 Perdiendo" if (pl_open or 0) < 0 else "—"),
        })
    return rows


def build_broker_order_rows(
    open_rows: list[sqlite3.Row],
    closed_rows: list[sqlite3.Row],
    live_quotes: dict[str, tuple[float | None, float | None]] | None = None,
) -> list[dict]:
    """Todas las órdenes del robot (aperturas y cierres) en formato broker, más nuevas primero.
    Cada posición abierta produce su orden de apertura; cada cerrada, la de apertura y la de cierre.
    `live_quotes` (opcional): symbol -> (precio_actual, %día) del subyacente, para mostrar el precio
    ahora al lado de cada orden (pedido usuario 2026-08-04)."""
    live_quotes = live_quotes or {}
    orders: list[dict] = []
    for row in list(closed_rows) + list(open_rows):
        for leg in ([True, False] if (row["status"] == "closed" and row["close_premium"] is not None) else [True]):
            order = _broker_order_row(row, is_open_leg=leg)
            price_now, change_pct = live_quotes.get(row["symbol"], (None, None))
            order["Precio ahora"] = price_now
            order["% día"] = change_pct
            orders.append(order)
    orders.sort(key=lambda o: o["Exec Time"], reverse=True)
    return orders


def build_decision_report_rows(open_rows: list[sqlite3.Row], open_decisions: list[sqlite3.Row]) -> list[dict]:
    """Reporte del "¿por qué abrió cada put?" (pedido usuario 2026-08-04): junta cada posición
    ABIERTA con el contexto de SU decisión de apertura (delta, IV rank, cobertura, anualizado,
    theta, margen naked, evento) y arma una fila legible con el razonamiento. La materia prima está
    en robot_decisions.context_json de la decisión action='open' de ese símbolo."""
    ctx_by_symbol: dict[str, dict] = {}
    for d in open_decisions:  # más nuevas primero → la primera que veo de cada símbolo es la vigente
        if d["action"] != "open":
            continue
        try:
            ctx = json.loads(d["context_json"]) if d["context_json"] else {}
        except (ValueError, TypeError):
            ctx = {}
        if ctx.get("strategy") == "iron_butterfly":
            continue
        ctx_by_symbol.setdefault(d["symbol"], ctx)

    def _pct(v):
        return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "—"

    rows = []
    for row in open_rows:
        c = ctx_by_symbol.get(row["symbol"], {})
        delta = c.get("chosen_delta")
        ivr = c.get("iv_rank")
        ann = c.get("chosen_annualized_return")
        cov = c.get("chosen_coverage_pct")
        volatile = c.get("volatile")
        has_event = c.get("has_event")
        dte = c.get("chosen_dte")
        target_delta = c.get("delta_target")
        # Razonamiento en prosa: los factores que motivaron la entrada.
        motivos = []
        if isinstance(ivr, (int, float)):
            motivos.append(f"IV Rank {ivr:.0f} ({'alta' if ivr >= 50 else 'media/baja'})")
        if isinstance(delta, (int, float)):
            tgt = f" (objetivo {target_delta:.2f})" if isinstance(target_delta, (int, float)) else ""
            motivos.append(f"delta {delta:.2f}{tgt}")
        if isinstance(ann, (int, float)):
            motivos.append(f"anualizado {ann * 100:.0f}%")
        if isinstance(cov, (int, float)):
            motivos.append(f"cobertura {cov * 100:.1f}%")
        motivos.append("volátil" if volatile else "no volátil")
        if has_event:
            motivos.append("evento Fed/earnings cerca (+cobertura)")
        rows.append({
            "Symbol": row["symbol"],
            "Put": f"${row['strike']:.0f}",
            "DTE": dte if dte is not None else (date.fromisoformat(row["expiration_date"]) - date.today()).days,
            "Contratos": row["quantity"],
            "Prima": row["entry_premium"],
            "Delta": delta,
            "IV Rank": ivr,
            # Cobertura y Anualizado se guardan ya en % (×100) porque el formato "%%" de Streamlit
            # NO multiplica solo — muestra el número crudo con un signo % al lado.
            "Cobertura": round(cov * 100, 2) if isinstance(cov, (int, float)) else None,
            "Anualizado": round(ann * 100, 1) if isinstance(ann, (int, float)) else None,
            "Margen/contrato": c.get("chosen_margin"),
            "Por qué la abrió": " · ".join(motivos),
        })
    return rows

# Simulador de Trading Automático (pedido 2026-08-02): transforma filas crudas de
# simulated_positions/simulated_equity_history en filas planas para las tablas/gráfico de
# pages/12_simulador.py — lógica pura, sin Streamlit, mismo patrón que
# dashboard/scanner_table.py.

_CLOSE_REASON_LABELS = {
    "profit_target": "Objetivo de ganancia",
    "expired": "Vencimiento",
    "stop_loss": "Stop-loss",
    "news_close": "Cierre por noticia",
    "dte_close": "Cierre por DTE",
}


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

        current_value = current_contract_value(chain, strike, expiration, underlying_price) if underlying_price is not None else None
        if current_value is not None:
            unrealized_pnl = round((entry_premium - current_value) * 100 * quantity, 2)
        else:
            # Sin precio hoy o contrato ausente de la cadena (hueco de datos): cae al último P&L
            # marcado por el scheduler en vez de romper con None (o asumir intrínseco).
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
