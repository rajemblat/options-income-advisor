from __future__ import annotations

import threading
from datetime import date

from options_advisor.storage import db
from options_advisor.storage import repository as repo


def test_concurrent_writes_do_not_corrupt(tmp_path):
    """El dashboard comparte una conexión entre hilos; con _LockedConnection las escrituras
    concurrentes se serializan y la base NO se corrompe (regresión del incidente 'rowid out of
    order' / 'database disk image is malformed', 2026-08)."""
    conn = db.connect(tmp_path / "app.db")
    errors: list[Exception] = []

    def writer(worker: int) -> None:
        try:
            for i in range(40):
                repo.set_robot_flag(conn, f"k{worker}_{i}", str(i))
        except Exception as exc:  # pragma: no cover - solo si algo sale mal
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(w,)) for w in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # las 8*40 banderas quedaron escritas
    n = conn.execute("SELECT COUNT(*) FROM robot_flags").fetchone()[0]
    assert n == 8 * 40
