"""¿Por qué el condor real no cierra? — la cuenta del cierre, en voz alta.

    python scripts/por_que_no_cierra_condor.py

El hermano de `por_que_no_abre_condor.py`, y nació del mismo problema al revés: el 2026-09-15 el
usuario preguntó "¿por qué no ha cerrado el SPX iron?" y en el log no había nada que responder. El
tick que gestiona la posición hace su cuenta cada minuto y se va callado si no corresponde cerrar —
razonable para no escribir una línea por minuto, inútil cuando estás mirando la pantalla.

Este script rehace EXACTAMENTE la cuenta de `_manage_open_position` + `should_close_condor` y muestra
los números: a cuánto está la posición al mid, a cuánto está de verdad si salís YA, cuánto falta para
el objetivo y cuánto para el stop.

Los dos números importan y casi nunca coinciden. El objetivo y el stop se miden al precio EJECUTABLE
(recomprar los cortos al ask, vender las alas al bid), no al mid — es la lección del 04/09, cuando el
objetivo disparó con el mid en +$35 y salir dejó +$25.

Lee, no toca: no manda órdenes ni escribe en la base. Se puede correr con el robot operando.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from options_advisor.broker import get_broker_client  # noqa: E402
from options_advisor.config import load_settings  # noqa: E402
from options_advisor.execution import live_condor_engine as lce  # noqa: E402
from options_advisor.scheduler.market_calendar import market_session  # noqa: E402
from options_advisor.simulator import iron_condor, learning  # noqa: E402
from options_advisor.storage import db  # noqa: E402
from options_advisor.storage import repository as repo  # noqa: E402


def _d(x) -> str:
    return "—" if x is None else f"${x:,.2f}"


def main() -> int:
    settings = load_settings()
    hoy = date.today()
    conn = db.connect(PROJECT_ROOT / "data" / "app.db")
    conn.row_factory = sqlite3.Row
    cfg = learning.effective_condor(conn, settings.intraday_condor)

    print(f"\n=== ¿Por qué no cierra el condor real? · {hoy} {datetime.now():%H:%M} ===\n")
    print(f"Reglas de hoy: objetivo {cfg.profit_target_pct:.0%} del crédito "
          f"({cfg.profit_target_early_pct:.0%} en los primeros {cfg.early_window_minutes:.0f} min) · "
          f"stop {_d(cfg.stop_loss_dollars)} · 0DTE (al vencer se liquida sola).\n")

    abiertas = repo.get_open_real_condor_positions(conn)
    if not abiertas:
        print("No hay ningún condor real abierto. Nada que cerrar.\n")
        return 0

    sesion = market_session()
    if sesion != "abierto":
        print(f"⚠️  El mercado está «{sesion}». El tick de gestión solo corre con el mercado "
              "abierto, así que ahora mismo no se está evaluando nada.\n")

    broker = get_broker_client()
    salida = 0

    for row in abiertas:
        qty = row["quantity"] or 1
        credito = row["entry_net_credit"] or 0.0
        print(f"── #{row['id']}  {row['underlying']}  "
              f"{row['long_put_strike']:.0f}/{row['short_put_strike']:.0f} — "
              f"{row['short_call_strike']:.0f}/{row['long_call_strike']:.0f}  × {qty}")
        print(f"   Estado: {row['status']} · vence {row['expiration_date']} · "
              f"crédito cobrado {_d(credito)}")

        if row["status"] != "open":
            print("   🟡 Todavía no está ABIERTA (la orden de apertura no llenó). El motor no la "
                  "gestiona hasta que llene.\n")
            continue

        edad = None
        if row["entry_ts"]:
            try:
                edad = (datetime.now() - datetime.fromisoformat(row["entry_ts"])).total_seconds() / 60.0
            except (ValueError, TypeError):
                edad = None
        if edad is not None:
            print(f"   Abierta hace {edad:.0f} minutos ({str(row['entry_ts'])[11:16]}).")

        oid_vivo, precio_vivo = repo.get_real_condor_close_working(row)
        if oid_vivo:
            print(f"   🟡 YA HAY UNA RECOMPRA PUESTA en Schwab: orden {oid_vivo} a "
                  f"{_d(precio_vivo)} de débito. El motor no evalúa reglas nuevas mientras esa "
                  "orden siga viva — la reajusta hacia el mid o espera el fill.\n")
            continue

        try:
            chain = broker.get_option_chain(row["underlying"], expiration_range_days=lce.CHAIN_FETCH_RANGE_DAYS)
        except Exception as exc:  # noqa: BLE001
            print(f"   🔴 No se pudo traer la cadena de {row['underlying']}: {exc}")
            print("      Sin cadena el motor NO cierra a ciegas — es deliberado. Si esto se repite, "
                  "el problema es la conexión con Schwab, no la regla.\n")
            salida = 1
            continue

        strikes = (row["short_put_strike"], row["short_call_strike"],
                   row["long_put_strike"], row["long_call_strike"])
        mid_pc = iron_condor.condor_close_value(chain, *strikes)
        try:
            exit_pc = iron_condor.condor_exit_value(chain, *strikes)
        except Exception:  # noqa: BLE001
            exit_pc = None

        if mid_pc is None:
            print("   🔴 La cadena no trae las 4 patas ahora mismo (hueco de datos). El motor no "
                  "cierra a ciegas: espera al tick siguiente.\n")
            continue

        pnl_mid = round(credito - round(mid_pc * qty, 2), 2)
        pnl_salida = None if exit_pc is None else round(credito - round(exit_pc * qty, 2), 2)
        temprano = edad is not None and edad <= cfg.early_window_minutes
        objetivo_pct = cfg.profit_target_early_pct if temprano else cfg.profit_target_pct
        objetivo = round(objetivo_pct * credito, 2)

        print(f"   Al mid marca      {_d(pnl_mid)}")
        print(f"   Salir YA de verdad {_d(pnl_salida)}   ← ESTA es la que manda")
        if pnl_salida is None:
            print("      (no se pudo calcular el precio ejecutable; el motor cae al mid)")
        print(f"   Objetivo: {objetivo_pct:.0%} del crédito = {_d(objetivo)}"
              + ("  (ventana temprana)" if temprano else ""))
        print(f"   Stop:     {_d(-cfg.stop_loss_dollars)}")

        manda = pnl_mid if pnl_salida is None else pnl_salida
        if manda >= objetivo:
            print("   🟢 YA CORRESPONDE CERRAR por objetivo — si no cerró, el tick todavía no "
                  "corrió o falló el envío. Mirá el log del robot.")
        elif cfg.stop_loss_dollars > 0 and manda <= -cfg.stop_loss_dollars:
            print("   🟢 YA CORRESPONDE CERRAR por stop — si no cerró, mirá el log del robot.")
        else:
            falta_obj = round(objetivo - manda, 2)
            falta_stop = round(manda + cfg.stop_loss_dollars, 2)
            print(f"   🔵 NO corresponde cerrar todavía: le faltan {_d(falta_obj)} para el objetivo "
                  f"y {_d(falta_stop)} para el stop.")
            print("      Es 0DTE: si no toca ninguno de los dos, se liquida sola al vencimiento.")
        print()

    conn.close()
    return salida


if __name__ == "__main__":
    raise SystemExit(main())
