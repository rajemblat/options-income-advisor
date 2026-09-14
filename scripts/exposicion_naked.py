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
from options_advisor.storage import repository as repo  # noqa: E402

# Estados en que la orden murió sin llegar a comprometer nada.
MUERTAS = ("REJECTED", "CANCELED", "EXPIRED", "error")


def main() -> int:
    conn = db.connect(load_settings().database.resolved_path())
    conn.row_factory = sqlite3.Row

    # La MISMA función que pinta el cartel rojo del dashboard (repository.exposicion_naked). Una
    # sola cuenta: si el script y la pantalla calcularan cada uno lo suyo, tarde o temprano darían
    # números distintos y no habría forma de saber cuál creer.
    exp = repo.exposicion_naked(conn)
    if not exp["operaciones"]:
        print("Todavía no hay operaciones reales de naked put registradas.")
        return 0

    print("\n╔═ EXPOSICIÓN DE LOS NAKED PUTS EN REAL ═╗\n")
    print(f"{exp['operaciones']} operaciones reales registradas\n")
    print("— Lo máximo que llegó a haber VIVO al mismo tiempo —")
    print(f"  Nocional  : ${exp['maximo']:>10,.0f}   el {exp['maximo_fecha']}  "
          f"({exp['maximo_posiciones']} posiciones)")
    print("    (nocional = strike × 100 × contratos: lo que costaría comprar las acciones si te")
    print("     asignaran todo junto. No es el colateral que el broker traba, que es mucho menor.)")
    print("\n— Ahora mismo —")
    print(f"  Nocional  : ${exp['ahora']:>10,.0f}   ({exp['ahora_posiciones']} posiciones)")

    filas = conn.execute(
        f"""
        SELECT symbol, log_ts, strike, collateral, closed,
               COALESCE(filled_contracts, final_contracts, 1) AS contratos
        FROM live_order_log
        WHERE action = 'SELL_TO_OPEN' AND dry_run = 0 AND sent = 1
          AND (order_status IS NULL OR order_status NOT IN ({','.join('?' * len(MUERTAS))}))
        """,
        MUERTAS,
    ).fetchall()
    print("\n— Los cinco tickets más grandes, por nocional de uno solo —")
    def _noc(r):
        return float(r["strike"] or 0) * 100.0 * int(r["contratos"] or 1)
    for f in sorted(filas, key=lambda r: -_noc(r))[:5]:
        estado = "cerrada" if f["closed"] else "VIVA"
        print(f"  {f['log_ts'][:10]}  {f['symbol']:<6} {f['contratos']}x ${f['strike']:>7,.0f}  "
              f"nocional ${_noc(f):>9,.0f}  colateral ${f['collateral'] or 0:>7,.0f}  {estado}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
