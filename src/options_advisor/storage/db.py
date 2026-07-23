from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# `CREATE TABLE IF NOT EXISTS` no agrega columnas nuevas a una tabla ya existente en un
# data/app.db previo — sin ORM/migraciones formales (Sección 5, herramienta de un solo
# usuario), este es el mecanismo mínimo para que las columnas agregadas después de la
# creación inicial de cada tabla aparezcan también en bases creadas antes de que existieran.
_NEW_COLUMNS_BY_TABLE = {
    "candidate_contracts": {
        "legs_json": "TEXT",
        "net_premium": "REAL",
        "max_profit": "REAL",
        "max_loss": "REAL",
        "breakevens_json": "TEXT",
        "probability_of_profit": "REAL",
        "dte": "INTEGER",
        "underlying_price": "REAL",
        "payoff_is_estimate": "INTEGER",
    },
    "indicator_snapshots": {
        "next_earnings_date": "TEXT",
        "price_std_20": "REAL",
        "net_gex": "REAL",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, new_columns in _NEW_COLUMNS_BY_TABLE.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, col_type in new_columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    conn.commit()


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
    _migrate(conn)
    return conn
