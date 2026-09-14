"""¿Cuánta plata llegó a tener comprometida la estrategia de naked puts en REAL?

    python scripts/exposicion_naked.py

Pregunta del usuario el 2026-09-14. No es curiosidad: es la pregunta de dimensionamiento. Saber
cuánto llegó a haber comprometido a la vez —y no cuánto se operó en total— es lo que dice si los
topes están puestos donde corresponde para el tamaño real de la cuenta.

Mide DOS cosas que se confunden fácil y quieren decir cosas muy distintas:

  COLATERAL  lo que el broker traba mientras la posición está viva. Es la plata que no podés usar
             para otra cosa. Es lo que realmente limita cuántas operaciones podés tener abiertas.

  NOCIONAL   lo que costaría si TODAS te asignaran (strike × 100 × contratos). No es plata trabada
             —casi nunca pasa— pero es el tamaño de la apuesta si el mercado se da vuelta contra
             todo a la vez. Siempre es mucho más grande que el colateral, y por eso conviene mirarlo:
             el colateral te dice qué te cuesta hoy, el nocional qué podrías llegar a deber.

El máximo se calcula barriendo la línea de tiempo (cada apertura suma, cada cierre resta), no
sumando todo lo del día: lo que importa es cuánto hubo VIVO al mismo tiempo.

Solo lee.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from options_advisor.config import load_settings  # noqa: E402
from options_advisor.storage import db  # noqa: E402

# Estados en que la orden murió sin llegar a comprometer nada.
MUERTAS = ("REJECTED", "CANCELED", "EXPIRED", "error")


def main() -> int:
    conn = db.connect(load_settings().database.resolved_path())
    conn.row_factory = sqlite3.Row

    filas = conn.execute(
        f"""
        SELECT id, symbol, log_ts, strike, collateral, closed, close_ts, close_reason,
               COALESCE(filled_contracts, final_contracts, 1) AS contratos
        FROM live_order_log
        WHERE action = 'SELL_TO_OPEN' AND dry_run = 0 AND sent = 1
          AND (order_status IS NULL OR order_status NOT IN ({','.join('?' * len(MUERTAS))}))
        ORDER BY log_ts
        """,
        MUERTAS,
    ).fetchall()

    if not filas:
        print("Todavía no hay operaciones reales de naked put registradas.")
        return 0

    # Línea de tiempo: cada apertura suma, cada cierre resta. Una posición sin cierre sigue viva.
    eventos = []
    for f in filas:
        col = float(f["collateral"] or 0.0)
        noc = float(f["strike"] or 0) * 100.0 * int(f["contratos"] or 1)
        eventos.append((f["log_ts"][:19], +col, +noc, +1))
        if f["closed"] and f["close_ts"]:
            eventos.append((f["close_ts"][:19], -col, -noc, -1))
    eventos.sort(key=lambda e: (e[0], e[3]))   # a igual instante, los cierres primero

    col = noc = 0.0
    n = 0
    pico_col = (0.0, "", 0)
    pico_noc = (0.0, "", 0)
    pico_n = (0, "")
    for ts, dc, dn, dcount in eventos:
        col += dc
        noc += dn
        n += dcount
        if col > pico_col[0]:
            pico_col = (col, ts, n)
        if noc > pico_noc[0]:
            pico_noc = (noc, ts, n)
        if n > pico_n[0]:
            pico_n = (n, ts)

    print(f"\n╔═ EXPOSICIÓN DE LOS NAKED PUTS EN REAL ═╗\n")
    print(f"{len(filas)} operaciones · del {filas[0]['log_ts'][:10]} al {filas[-1]['log_ts'][:10]}\n")

    print("— Lo máximo que llegó a haber VIVO al mismo tiempo —")
    print(f"  Colateral trabado : ${pico_col[0]:>10,.0f}   el {pico_col[1][:16]}  "
          f"({pico_col[2]} posiciones)")
    print(f"  Nocional          : ${pico_noc[0]:>10,.0f}   el {pico_noc[1][:16]}")
    print(f"  Posiciones a la vez: {pico_n[0]:>9}   el {pico_n[1][:16]}")

    print("\n— Ahora mismo —")
    print(f"  Colateral trabado : ${col:>10,.0f}")
    print(f"  Nocional          : ${noc:>10,.0f}")
    print(f"  Posiciones vivas  : {n:>10}")

    print("\n— Los cinco tickets más grandes (colateral de uno solo) —")
    for f in sorted(filas, key=lambda r: -(r["collateral"] or 0))[:5]:
        noc_f = float(f["strike"] or 0) * 100.0 * int(f["contratos"] or 1)
        estado = "cerrada" if f["closed"] else "VIVA"
        print(f"  {f['log_ts'][:10]}  {f['symbol']:<6} {f['contratos']}x ${f['strike']:>7,.0f}  "
              f"colateral ${f['collateral'] or 0:>7,.0f}  nocional ${noc_f:>9,.0f}  {estado}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
