"""Respaldo consistente de data/app.db, con verificacion y rotacion. Ver scripts/backup_db.sh."""
from __future__ import annotations

import gzip
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ORIGEN = RAIZ / "data" / "app.db"
DESTINO = RAIZ / "data" / "backups"
RETENCION_DIAS = 30


def main() -> int:
    if not ORIGEN.exists():
        print(f"ERROR: no encuentro {ORIGEN}", file=sys.stderr)
        return 1
    DESTINO.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y-%m-%d_%H%M")
    crudo = DESTINO / f"app_{sello}.db"

    # .backup de SQLite: foto consistente aunque el robot este escribiendo (modo WAL).
    origen = sqlite3.connect(f"file:{ORIGEN}?mode=ro", uri=True)
    copia = sqlite3.connect(crudo)
    try:
        origen.backup(copia)
    finally:
        copia.close()
        origen.close()

    # Verificacion ANTES de comprimir: un backup que no se puede abrir no es un backup.
    chequeo = sqlite3.connect(crudo)
    try:
        estado = chequeo.execute("PRAGMA integrity_check").fetchone()[0]
        ordenes = chequeo.execute("SELECT COUNT(*) FROM live_order_log").fetchone()[0]
    finally:
        chequeo.close()
    if estado != "ok":
        print(f"ERROR: el respaldo quedo corrupto ({estado})", file=sys.stderr)
        return 1

    with open(crudo, "rb") as f_in, gzip.open(f"{crudo}.gz", "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    crudo.unlink()

    limite = time.time() - RETENCION_DIAS * 86400
    borrados = 0
    for viejo in DESTINO.glob("app_*.db.gz"):
        if viejo.stat().st_mtime < limite:
            viejo.unlink()
            borrados += 1

    mb = (DESTINO / f"app_{sello}.db.gz").stat().st_size / 1048576
    cuantos = len(list(DESTINO.glob("app_*.db.gz")))
    print(f"{datetime.now():%F %T} OK: app_{sello}.db.gz ({mb:.1f} MB) · integridad ok · "
          f"{ordenes} ordenes reales · {cuantos} respaldos guardados"
          + (f" · {borrados} vencidos borrados" if borrados else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
