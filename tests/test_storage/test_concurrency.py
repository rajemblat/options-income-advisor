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


def test_un_integrityerror_no_deja_la_base_trabada(tmp_path):
    """Regresion (auditoria 2026-08-22): sin rollback, un choque contra el indice unico deja la
    transaccion ABIERTA y ningun otro proceso puede volver a escribir.

    El indice unico de real_trade_alerts existe a proposito para resolver la carrera entre el
    dashboard y el scheduler cuando detectan la misma operacion (incidente del 2026-07-29). O sea
    que se dispara justo cuando hay dos procesos trabajando — y la conexion del dashboard queda
    cacheada por Streamlit, asi que el bloqueo duraba lo que durara la sesion. El robot seguia
    "andando", logueando, sin persistir ni una decision ni un cierre ni un P&L."""
    import sqlite3
    from datetime import date, datetime

    from options_advisor.storage import db
    from options_advisor.storage import repository as repo
    from options_advisor.storage.models import RealTradeAlert

    ruta = tmp_path / "app.db"
    a = db.connect(str(ruta))
    trade = RealTradeAlert(
        account_number="X", occ_symbol="AAL   260918P00013000", symbol="AAL",
        trade_date=date(2026, 8, 21), trade_ts=datetime(2026, 8, 21, 11, 0),
        strategy_type="short_put_naked", option_type="put", strike=13.0,
        expiration_date=date(2026, 9, 18), quantity=1, order_id=12345,
    )
    assert repo.insert_real_trade_alert(a, trade) is not None
    assert repo.insert_real_trade_alert(a, trade) is None, "la segunda choca contra el UNIQUE"
    assert not a.in_transaction, "el rollback tiene que cerrar la transaccion abortada"

    # La prueba de fuego: otra conexion (otro proceso, en la vida real) puede escribir.
    b = sqlite3.connect(str(ruta), timeout=2)
    b.execute("INSERT INTO robot_flags (key, value, updated_at) VALUES ('x', '1', 'now')")
    b.commit()
    b.close()
