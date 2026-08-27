"""REANUDA todo: limpia el interruptor maestro (all_paused) y las pausas por estrategia.

Es lo mismo que apretar el boton de reanudar en el Simulador, pero desde la terminal — para cuando
hay apuro y no se quiere buscar el boton (usuario 2026-08-26: el condor abre solo hasta las 10:00 ET).

NO manda ordenes. NO cambia reglas ni settings.yaml. Solo saca los frenos para que el robot pueda
volver a ABRIR posiciones. Las autorizaciones del dia (START) son aparte y no las toca.
"""
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from options_advisor.storage import repository as repo  # noqa: E402

conn = sqlite3.connect(str(RAIZ / "data" / "app.db"))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")

CLAVES = ("all_paused", "puts_paused", "condor_paused", "condor_real_paused", "butterfly_paused")

print("ANTES:")
for k in CLAVES:
    print(f"   {k:22s} = {repo.get_robot_flag(conn, k, '0')}")

repo.resume_all(conn)

print("\nDESPUES:")
for k in CLAVES:
    v = repo.get_robot_flag(conn, k, "0")
    print(f"   {k:22s} = {v}   {'<-- REANUDADO' if v == '0' else '<-- OJO, sigue frenado'}")

print("\nLISTO. El robot puede volver a abrir posiciones en el proximo tick (menos de 1 minuto).")
print("Recorda: el START del dia es aparte. Verificalo en el dashboard.")
conn.close()
