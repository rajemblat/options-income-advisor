"""PARA el martilleo de ordenes de cierre de un Iron Condor REAL que YA NO EXISTE en el broker.

Contexto (2026-08-24): el condor real id=2 cerro en Schwab, pero el robot nunca registro el fill
(la orden llenó justo mientras salia el cancel). Como su fila sigue en 'open', cada tick vuelve a
mandar la recompra y Schwab la rechaza con "oversold/overbought position".

Esto SOLO corrige la fila en la base. NO manda ordenes, NO toca reglas, NO toca settings.yaml.

Uso:
    .venv/bin/python scripts/parar_condor_fantasma.py            -> cierra con P&L DESCONOCIDO (None)
    .venv/bin/python scripts/parar_condor_fantasma.py 1.20       -> registra debito $1.20 por accion
                                                                    y calcula el P&L real

Con P&L None la operacion NO cuenta en el win rate ni en el aprendizaje: preferimos un hueco honesto
antes que un numero inventado. Si despues conseguis el precio real, se vuelve a correr con el numero.
"""
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from options_advisor.storage import repository as repo  # noqa: E402

DB = RAIZ / "data" / "app.db"

debito_ps = None
if len(sys.argv) > 1:
    try:
        debito_ps = float(sys.argv[1])
    except ValueError:
        print(f"ERROR: '{sys.argv[1]}' no es un numero. Pasá el débito por acción, ej: 1.20")
        raise SystemExit(1)

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")

filas = conn.execute(
    "SELECT * FROM real_condor_positions WHERE status = 'open' ORDER BY id"
).fetchall()

# Si no quedan abiertos pero SI hay filas que cerramos antes con P&L desconocido, y ahora nos pasan
# el precio real, completamos esas. Es el segundo paso del flujo: primero se para el martilleo sin
# inventar un numero, despues se pone el numero verdadero cuando aparece en el historial de Schwab.
if not filas and debito_ps is not None:
    filas = conn.execute(
        "SELECT * FROM real_condor_positions "
        "WHERE close_reason = 'cerrado_en_el_broker' AND realized_pnl IS NULL ORDER BY id"
    ).fetchall()
    if filas:
        print("No hay condors abiertos, pero hay cierres con P&L desconocido. Completo el precio real.")
        print()

if not filas:
    print("No hay ningun condor REAL en estado 'open'. No hay nada que parar.")
    print("(Si querias completar un P&L, pasá el débito: ... parar_condor_fantasma.py 1.25)")
    raise SystemExit(0)

print(f"Condors REALES marcados como abiertos: {len(filas)}")
print("=" * 70)

for r in filas:
    qty = r["quantity"] or 1
    credito = r["entry_net_credit"] or 0.0
    print(f"  id={r['id']}  {r['underlying']}  abierto {r['entry_ts']}")
    print(f"     vende put {r['short_put_strike']:.0f} / call {r['short_call_strike']:.0f}"
          f"   alas {r['long_put_strike']:.0f}/{r['long_call_strike']:.0f}")
    print(f"     credito cobrado: ${credito:,.2f}   ultimo P&L no realizado: ${r['last_unrealized_pnl'] or 0:,.2f}")

    if debito_ps is not None:
        debito_total = round(debito_ps * 100.0 * qty, 2)
        realizado = round(credito - debito_total, 2)
        motivo = "profit_target"
        print(f"     -> se registra CIERRE: débito ${debito_total:,.2f}  =>  P&L ${realizado:+,.2f}")
    else:
        debito_total = None
        realizado = None
        motivo = "cerrado_en_el_broker"
        print("     -> se registra CIERRE con P&L DESCONOCIDO (no cuenta en el win rate)")

    repo.close_real_condor_position(
        conn, r["id"], date.today(),
        close_value=debito_total, close_reason=motivo,
        realized_pnl=realizado, close_ts=datetime.now(),
    )
    print("=" * 70)

print()
print("LISTO. El robot deja de mandar ordenes de cierre en el proximo tick (menos de 1 minuto).")
print()
print("Como quedo:")
for r in conn.execute("SELECT id, status, close_reason, realized_pnl FROM real_condor_positions ORDER BY id"):
    pnl = "desconocido" if r["realized_pnl"] is None else f"${r['realized_pnl']:+,.2f}"
    print(f"  id={r['id']}  {r['status']:8s}  {str(r['close_reason'] or '-'):22s}  P&L {pnl}")
conn.close()
