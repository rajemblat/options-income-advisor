"""Anotar en el libro un condor real que cerraste VOS en Schwab, y decir por qué el robot no lo cerró.

    # 1) ver qué hay abierto y con qué números
    python scripts/cerrar_condor_a_mano.py

    # 2) anotar el cierre (débito POR ACCIÓN, el "Net Price" de la pantalla de Schwab)
    python scripts/cerrar_condor_a_mano.py --id 12 --debito 0.95 --hora 11:53

Nació el 2026-09-15: el usuario cerró a mano un condor del SPX a $0.95 de débito y preguntó dos
cosas — "poné la ganancia" y "decime por qué no lo cerró solo".

La primera hace falta porque una posición cerrada por fuera del robot desaparece de los totales: el
robot la sigue viendo abierta y la ganancia YA COBRADA no aparece en ningún lado. Es el mismo agujero
que los naked puts cerrados a mano (2026-08-14), y se tapa igual: cargando el precio de salida real.

La segunda hace falta porque el tick que gestiona la posición se va callado cuando no corresponde
cerrar — razonable para no escribir una línea por minuto, inútil cuando querés entender. Así que el
script imprime la última marca que el robot le tomó a la posición y la compara con los dos umbrales.

Lee y escribe SOLO la fila de esa posición. No manda ninguna orden al broker: la orden ya la mandaste
vos.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from options_advisor.config import load_settings  # noqa: E402
from options_advisor.simulator import learning  # noqa: E402
from options_advisor.storage import db  # noqa: E402
from options_advisor.storage import repository as repo  # noqa: E402


def _d(x) -> str:
    return "—" if x is None else f"${x:,.2f}"


def _autopsia(row, cfg) -> None:
    """Por qué el robot NO había cerrado: la última marca contra los dos umbrales.

    `last_unrealized_pnl` es lo que el motor anotó en su último tick. Ojo con una sutileza que
    importa: esa marca se guarda al MID, mientras que las dos reglas de salida se evalúan al precio
    EJECUTABLE, que siempre es peor. O sea que la marca guardada es OPTIMISTA respecto de lo que el
    robot usó para decidir — si la marca ya estaba lejos del objetivo, el número que miró el motor
    estaba todavía más lejos."""
    credito = row["entry_net_credit"] or 0.0
    marca = row["last_unrealized_pnl"]
    cuando = str(row["last_marked_ts"] or "")[11:19] or "nunca"

    edad = None
    if row["entry_ts"]:
        try:
            ref = datetime.fromisoformat(str(row["last_marked_ts"] or row["entry_ts"]))
            edad = (ref - datetime.fromisoformat(row["entry_ts"])).total_seconds() / 60.0
        except (ValueError, TypeError):
            edad = None
    temprano = edad is not None and edad <= cfg.early_window_minutes
    pct = cfg.profit_target_early_pct if temprano else cfg.profit_target_pct
    objetivo = round(pct * credito, 2)

    print("\n   ¿Por qué no cerró solo?")
    print(f"   · Crédito de entrada: {_d(credito)}")
    print(f"   · Última marca del robot: {_d(marca)}  (a las {cuando}, al mid)")
    print(f"   · Objetivo para cerrar: {pct:.0%} del crédito = {_d(objetivo)}"
          + ("  (ventana temprana)" if temprano else ""))
    print(f"   · Stop: {_d(-cfg.stop_loss_dollars)}")
    if marca is None:
        print("   → El robot nunca llegó a marcarla. Eso NO es normal: o la posición no quedó en "
              "estado 'open', o el tick del condor no está corriendo. Mirá el log:")
        print("       journalctl --user -u lokshn-robot --since today | grep -i condor | tail -40")
        return
    if marca >= objetivo:
        print("   → La marca YA estaba en el objetivo. Si no cerró, el problema no es la regla: "
              "es el envío. Mirá el log:")
        print("       journalctl --user -u lokshn-robot --since today | grep -i condor | tail -40")
        return
    falta = round(objetivo - marca, 2)
    print(f"   → No llegó al objetivo: le faltaban {_d(falta)} en la última marca, y eso es AL MID. "
          "Las reglas se miden al precio ejecutable, que es peor todavía, así que el número que "
          "miró el motor estaba aún más lejos.")
    print("   → Tampoco tocó el stop. Es 0DTE: sin objetivo y sin stop, la regla es dejarla vencer.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", type=int, help="id de la posición (se ve en la lista)")
    ap.add_argument("--debito", type=float,
                    help="lo que PAGASTE por acción para cerrar — el Net Price de Schwab (ej. 0.95)")
    ap.add_argument("--hora", type=str, default="",
                    help="hora del fill, HH:MM (por defecto, ahora)")
    args = ap.parse_args()

    settings = load_settings()
    conn = db.connect(PROJECT_ROOT / "data" / "app.db")
    conn.row_factory = sqlite3.Row
    cfg = learning.effective_condor(conn, settings.intraday_condor)

    abiertas = repo.get_open_real_condor_positions(conn)
    if not abiertas:
        print("\nNo hay ningún condor real abierto en el libro.\n")
        return 0

    if args.id is None or args.debito is None:
        print(f"\n=== Condors reales abiertos en el libro · {date.today()} ===\n")
        for row in abiertas:
            qty = row["quantity"] or 1
            print(f"#{row['id']}  {row['underlying']}  "
                  f"{row['long_put_strike']:.0f}/{row['short_put_strike']:.0f} — "
                  f"{row['short_call_strike']:.0f}/{row['long_call_strike']:.0f}  × {qty}  "
                  f"[{row['status']}]")
            print(f"   abierta {str(row['entry_ts'] or '')[:16].replace('T', ' ')} · "
                  f"crédito {_d(row['entry_net_credit'])} · vence {row['expiration_date']}")
            _autopsia(row, cfg)
            print(f"\n   Para anotar el cierre:\n"
                  f"       python scripts/cerrar_condor_a_mano.py --id {row['id']} --debito 0.95\n")
        conn.close()
        return 0

    fila = next((r for r in abiertas if r["id"] == args.id), None)
    if fila is None:
        print(f"\nNo hay un condor abierto con id={args.id}. Corré el script sin argumentos para "
              "ver la lista.\n")
        conn.close()
        return 1

    qty = fila["quantity"] or 1
    credito = fila["entry_net_credit"] or 0.0
    costo = round(float(args.debito) * 100.0 * qty, 2)
    ganancia = round(credito - costo, 2)

    cuando = datetime.now()
    if args.hora:
        try:
            h, m = args.hora.split(":")
            cuando = cuando.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        except (ValueError, TypeError):
            print(f"⚠️  No entendí --hora {args.hora!r}; uso la hora actual.")

    print(f"\n=== Anotando el cierre manual de #{fila['id']} ===\n")
    print(f"   Crédito cobrado al abrir:  {_d(credito)}")
    print(f"   Débito pagado al cerrar:   {_d(costo)}  (${float(args.debito):.2f} × 100 × {qty})")
    print(f"   {'GANANCIA' if ganancia >= 0 else 'PÉRDIDA '}:                  {_d(ganancia)}"
          + (f"   ({ganancia / credito:.0%} del crédito)" if credito else ""))

    _autopsia(fila, cfg)

    # 'manual' y no 'profit_target': el historial tiene que poder distinguir lo que decidió el robot
    # de lo que decidiste vos. Y además la racha de stop-loss del día no se toca con un cierre tuyo.
    repo.close_real_condor_position(
        conn, fila["id"], cuando.date(), costo, "manual", ganancia, close_ts=cuando,
    )
    print(f"\n✅ Anotado: cerrada a las {cuando:%H:%M} con {_d(ganancia)} de resultado realizado.")
    print("   Ya suma en los totales del dashboard (Real Market → condor).\n")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
