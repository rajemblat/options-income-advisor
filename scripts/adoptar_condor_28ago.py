"""Registra en el robot el Iron Condor REAL del 28/08 que él abrió y el usuario cerró a mano.

Qué pasó: el robot mandó la apertura (id=4), a los 34 segundos leyó REJECTED y descartó la fila
como `apertura_no_llenó`. Pero la orden SÍ llenó en Schwab. La posición quedó viva y sin gestión
—sin stop, sin objetivo— hasta que el usuario la cerró a mano a las 11:36.

La ganancia es del robot: él eligió los strikes y mandó la orden. Se registra con motivo `manual`
para que quede claro en el historial que la salida la hizo el usuario, no el motor.

Números (del historial de Schwab del usuario):
    apertura  09:41:06   crédito $1.95   ->  $195.00
    cierre    11:36:02   débito  $0.85   ->   $85.00
    resultado                               +$110.00

Solo escribe la base. No manda órdenes.
"""
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from options_advisor.storage import repository as repo  # noqa: E402

CREDITO_PS = 1.95
DEBITO_PS = 0.85
APERTURA = "2026-08-28T09:41:06"
CIERRE = "2026-08-28T11:36:02"

conn = sqlite3.connect(str(RAIZ / "data" / "app.db"))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")

fila = conn.execute(
    "SELECT * FROM real_condor_positions WHERE entry_date='2026-08-28' ORDER BY id DESC LIMIT 1"
).fetchone()
if fila is None:
    print("No encuentro la fila del condor del 28/08. Nada que hacer.")
    raise SystemExit(1)

pid = fila["id"]
qty = fila["quantity"] or 1
credito = round(CREDITO_PS * 100.0 * qty, 2)
debito = round(DEBITO_PS * 100.0 * qty, 2)
realizado = round(credito - debito, 2)

print(f"Fila id={pid}  {fila['underlying']}  "
      f"SP{fila['short_put_strike']:.0f}/SC{fila['short_call_strike']:.0f}")
print(f"  ANTES : status={fila['status']}  motivo={fila['close_reason']}  P&L={fila['realized_pnl']}")

# 1) la apertura SÍ existió: se registra el fill
repo.mark_real_condor_fill(conn, pid, entry_credit_ps=CREDITO_PS, entry_net_credit=credito,
                           open_schwab_order_id=fila["open_schwab_order_id"],
                           entry_ts=datetime.fromisoformat(APERTURA))
# 2) y el cierre que hizo el usuario
repo.close_real_condor_position(conn, pid, date(2026, 8, 28), close_value=debito,
                                close_reason="manual", realized_pnl=realizado,
                                close_ts=datetime.fromisoformat(CIERRE))

r = conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (pid,)).fetchone()
print(f"  AHORA : status={r['status']}  motivo={r['close_reason']}  "
      f"crédito=${r['entry_net_credit']:,.2f}  cierre=${r['close_value']:,.2f}  "
      f"P&L=${r['realized_pnl']:+,.2f}")
print()
print("Historial del condor REAL:")
tot = 0.0
for x in conn.execute("SELECT id,entry_date,close_reason,realized_pnl FROM real_condor_positions "
                      "WHERE realized_pnl IS NOT NULL ORDER BY id"):
    tot += x["realized_pnl"]
    print(f"   id={x['id']}  {x['entry_date']}  {x['close_reason']:14s}  ${x['realized_pnl']:+,.2f}")
print(f"   TOTAL: ${tot:+,.2f}")
conn.close()
