"""HUELLA DEL DIA: resumen compacto y comparable de lo que hizo el robot hoy.

Sirve para poner lado a lado la Mac y el servidor y ver si se comportan IGUAL. Solo LEE la base.
Corre igual en macOS y en Linux, sin dependencias (stdlib pura).

Uso:  python3 scripts/huella_del_dia.py [YYYY-MM-DD]
"""
import sqlite3
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIA = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()

c = sqlite3.connect(str(RAIZ / "data" / "app.db"))
c.row_factory = sqlite3.Row


def uno(sql, *a):
    try:
        return list(c.execute(sql, a))[0][0]
    except Exception:
        return "?"


print("=" * 62)
print(f"HUELLA DEL DIA  {DIA}")
print("=" * 62)

print("\n-- CONDORS DE PAPEL --")
filas = list(c.execute(
    "SELECT id,entry_ts,short_put_strike,short_call_strike,entry_net_credit,close_reason,realized_pnl "
    "FROM iron_condor_positions WHERE entry_date=? ORDER BY id", (DIA,)))
if not filas:
    print("   (ninguno)")
for r in filas:
    hora = (r["entry_ts"] or "")[11:19]
    pnl = r["realized_pnl"]
    print(f"   {hora}  SP{r['short_put_strike']:.0f}/SC{r['short_call_strike']:.0f}  "
          f"cred ${r['entry_net_credit']:.2f}  {str(r['close_reason'] or 'abierto'):14s} "
          f"P&L {('$%+.2f' % pnl) if pnl is not None else '-'}")
tot = sum((r["realized_pnl"] or 0) for r in filas)
print(f"   TOTAL: {len(filas)} condors  ${tot:+,.2f}")

print("\n-- CONDOR REAL --")
filas = list(c.execute(
    "SELECT id,status,entry_net_credit,close_reason,realized_pnl FROM real_condor_positions "
    "WHERE entry_date=? ORDER BY id", (DIA,)))
if not filas:
    print("   (ninguno)")
for r in filas:
    print(f"   id={r['id']}  {r['status']:8s}  cred ${r['entry_net_credit'] or 0:.2f}  "
          f"{str(r['close_reason'] or '-'):14s}  P&L {r['realized_pnl']}")

print("\n-- NAKED PUTS SIMULADAS ABIERTAS HOY --")
filas = list(c.execute(
    "SELECT symbol,strike,entry_premium FROM simulated_positions WHERE entry_date=? ORDER BY id", (DIA,)))
print("   " + (", ".join(f"{r['symbol']} {r['strike']:g}" for r in filas) if filas else "(ninguna)"))

print("\n-- DECISIONES --")
for r in c.execute("SELECT action,COUNT(*) n FROM robot_decisions WHERE decision_date=? "
                   "GROUP BY action ORDER BY n DESC", (DIA,)):
    print(f"   {r['action']:8s} {r['n']}")

print("\n-- ORDENES REALES (naked) --")
print(f"   armadas: {uno('SELECT COUNT(*) FROM live_order_log WHERE log_date=?', DIA)}   "
      f"enviadas: {uno('SELECT COALESCE(SUM(sent),0) FROM live_order_log WHERE log_date=?', DIA)}")

print("\n-- ESTADO --")
for k in ("all_paused", "puts_paused", "condor_real_paused", "live.kill_switch",
          "live.armed_date", "condor.live_armed_date"):
    v = uno("SELECT value FROM robot_flags WHERE key=?", k)
    print(f"   {k:24s} = {v}")
print(f"   posiciones simuladas abiertas: {uno('SELECT COUNT(*) FROM simulated_positions WHERE status=?', 'open')}")
print("=" * 62)
