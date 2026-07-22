from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Abre (y si hace falta inicializa) la base SQLite en modo WAL, para permitir
    lecturas del dashboard concurrentes con escrituras del scheduler."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # check_same_thread=False: Streamlit corre cada página en su propio thread y
    # get_connection() cachea una única conexión compartida (st.cache_resource);
    # el modo WAL ya habilitado abajo es lo que hace esto seguro para lecturas/escrituras
    # concurrentes, no la falta de este flag.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    return conn
