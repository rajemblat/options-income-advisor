from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class _LockedConnection:
    """Envuelve una `sqlite3.Connection` y serializa cada `execute`/`commit` con un candado.

    El dashboard de Streamlit cachea UNA sola conexión (`@st.cache_resource`) y la comparte entre
    varios hilos (cada rerun de página corre en su propio thread, con `check_same_thread=False`).
    Si dos hilos escriben a la vez sobre ese MISMO objeto conexión, SQLite puede corromper el
    archivo ("database disk image is malformed" / "rowid out of order") — el `busy_timeout` NO
    protege de esto porque es la misma conexión, no dos procesos. Este candado hace que las
    escrituras del dashboard se hagan de a una. (El scheduler es un proceso aparte de un solo hilo,
    así que para él el candado es inocuo.) Todo lo que no está proxeado se delega a la conexión
    real vía `__getattr__` (row_factory, close, etc.)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_lock", threading.RLock())

    def execute(self, *args, **kwargs):
        with self._lock:
            return self._conn.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with self._lock:
            return self._conn.executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with self._lock:
            return self._conn.executescript(*args, **kwargs)

    def commit(self):
        with self._lock:
            return self._conn.commit()

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)

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
        "annualized_return_pct": "REAL",
        "early_close_projection_json": "TEXT",
        "historical_move_occurrences": "INTEGER",
        "historical_move_total_windows": "INTEGER",
        "similar_move_occurrences": "INTEGER",
        "similar_move_bigger_occurrences": "INTEGER",
    },
    "indicator_snapshots": {
        "next_earnings_date": "TEXT",
        "price_std_20": "REAL",
        "net_gex": "REAL",
        "next_ex_dividend_date": "TEXT",
    },
    "macro_snapshot": {
        "cpi_yoy_date": "TEXT",
    },
    "simulated_positions": {
        "entry_ts": "TEXT",
        "close_ts": "TEXT",
    },
    "robot_decisions": {
        "user_feedback": "TEXT",
        "position_id": "INTEGER",
        "user_note": "TEXT",
        "feedback_at": "TEXT",
        # Voto por parámetro (usuario 2026-08-06: "poder votar bien/normal/mal cada casillero").
        # JSON {param_key: "good"|"normal"|"bad"} — el aprendizaje lo cruza con los pesos del cerebro.
        "param_feedback_json": "TEXT",
    },
    "live_order_log": {
        "bid": "REAL",
        "ask": "REAL",
        "user_feedback": "TEXT",
        "user_note": "TEXT",
        "feedback_at": "TEXT",
        "schwab_order_id": "TEXT",
        "order_status": "TEXT",
        "fill_price": "REAL",
        "filled_contracts": "INTEGER",
        "final_limit_price": "REAL",
        "replacements": "INTEGER",
        "sent_ts": "TEXT",
        "send_error": "TEXT",
        "closed": "INTEGER",
        "close_ts": "TEXT",
        "close_fill_price": "REAL",
        "close_reason": "TEXT",
        "realized_pnl": "REAL",
        "close_schwab_order_id": "TEXT",
        "open_context_json": "TEXT",
        # Piso duro de precio al vender (usuario 2026-08-11): las órdenes viejas quedan en NULL (sin piso).
        "price_floor": "REAL",
        # DEFAULT 1 a propósito: las órdenes que YA existían en la base se marcan como 'email ya mandado'
        # para no re-mandarles email al agregar la columna. Las nuevas (insert_live_order_log) arrancan en 0.
        "open_email_sent": "INTEGER DEFAULT 1",
        # Cierre de posiciones reales (usuario 2026-08-14, tras NU/SPCX/NVDA: la regla disparaba el cierre
        # pero la recompra no llenaba y el robot dejaba de intentar EN SILENCIO).
        "close_attempts": "INTEGER DEFAULT 0",     # cuántas veces intentó cerrarla sin lograrlo
        "last_close_error": "TEXT",               # por qué falló el último intento (motivo de Schwab incluido)
        "close_fail_email_sent": "INTEGER DEFAULT 0",
        # 1 = el P&L de esta operación es una ESTIMACIÓN (se cerró fuera del robot y no se encontró el
        # precio de salida exacto en Schwab; se usó el valor de mercado del momento). Antes esas
        # operaciones quedaban con realized_pnl NULL y su ganancia no entraba en ningún total.
        "pnl_is_estimate": "INTEGER DEFAULT 0",
    },
    # Asesor AI (usuario 2026-08-11): la sugerencia puede ser abrir o CERRAR una posición ('close'),
    # un cierre manual pedido por chat aunque no toque la regla. Columna nueva sobre la tabla ya creada.
    "ai_suggested_orders": {
        "action": "TEXT DEFAULT 'open'",
        "target": "TEXT DEFAULT 'real'",
        "sim_kind": "TEXT",
        "position_id": "INTEGER",
        # Piso duro de precio al vender pedido por el usuario ('no bajes de 3.00', usuario 2026-08-11).
        "min_price": "REAL",
    },
    "real_trade_alerts": {
        "order_id": "INTEGER",
        "historical_move_occurrences": "INTEGER",
        "historical_move_total_windows": "INTEGER",
        "similar_move_occurrences": "INTEGER",
        "similar_move_bigger_occurrences": "INTEGER",
        "leg_role": "TEXT",
        # Griegas netas de la posición combinada (usuario 2026-08-12): filas viejas quedan en NULL.
        "net_delta": "REAL",
        "net_gamma": "REAL",
        "net_theta": "REAL",
        "net_vega": "REAL",
        "net_rho": "REAL",
        "greeks_source": "TEXT",
    },
    # Cierre manual por posición del condor REAL (usuario 2026-08-14). Las filas que ya existían quedan
    # en NULL = sin cierre manual pedido, que es exactamente el default correcto.
    "real_condor_positions": {
        "manual_close_requested": "TEXT",
    },
}

# Tablas de un diseño anterior, sin reemplazo — `position_snapshots` quedó obsoleta con el
# rediseño de detección de operaciones reales vía /orders (2026-07-28, ver
# alerts/real_trades.py): el diff de posiciones/promedio blendeado se reemplazó por completo
# por el fill exacto de cada orden, así que ya no hace falta guardar snapshots entre corridas.
_TABLES_TO_DROP = ["position_snapshots"]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, new_columns in _NEW_COLUMNS_BY_TABLE.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, col_type in new_columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    for table in _TABLES_TO_DROP:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    # Después (no en schema.sql): un índice sobre una columna recién agregada por ALTER TABLE
    # arriba fallaría si corriera ANTES de la migración contra una base ya existente sin esa
    # columna todavía (bug real encontrado 2026-07-28 armando este mismo índice).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_real_trade_alerts_order_id ON real_trade_alerts(order_id)")
    # Incidente real 2026-07-29: dos procesos de detección corriendo a la vez (el scheduler
    # recién reiniciado + una corrida manual) leyeron el mismo set de "ya alertadas" ANTES de
    # que cualquiera insertara, y ambos insertaron la misma orden — el chequeo en Python
    # (`repository.py::get_alerted_order_leg_keys`) no alcanza para prevenir una carrera entre
    # procesos, hace falta un índice UNIQUE a nivel de base (protegido también en
    # `repository.py::insert_real_trade_alert`, que atrapa el IntegrityError). NULLs no chocan
    # entre sí en SQLite, así que las filas de antes del rediseño vía /orders (order_id NULL)
    # quedan afuera de esta restricción sin problema. Al igual que el índice de arriba, va acá
    # (no en schema.sql) porque antes de crearlo hace falta limpiar los duplicados que ya
    # puedan existir en una base existente, o el CREATE UNIQUE INDEX fallaría — se conserva la
    # fila más vieja (MIN(id)) de cada (order_id, occ_symbol) duplicado y se borra el resto (no
    # hay forma de saber cuál insert "ganó" la carrera, elección arbitraria pero estable: el
    # contenido de ambas filas es idéntico).
    conn.execute(
        """
        DELETE FROM real_trade_alerts
        WHERE order_id IS NOT NULL
          AND id NOT IN (
              SELECT MIN(id) FROM real_trade_alerts WHERE order_id IS NOT NULL GROUP BY order_id, occ_symbol
          )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_real_trade_alerts_order_leg ON real_trade_alerts(order_id, occ_symbol)")
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
    # busy_timeout: si otro proceso/thread está escribiendo, ESPERAR hasta 5s a que libere en vez
    # de fallar de una con "database is locked" (o peor, contribuir a la corrupción que vimos con
    # dos escritores simultáneos, 2026-08). synchronous=NORMAL es el combo recomendado con WAL:
    # menos fsync, sin riesgo de corrupción (a lo sumo se pierde la última transacción ante un
    # corte de luz — aceptable en paper trading).
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    _migrate(conn)
    # Serializa las escrituras concurrentes del dashboard (varios hilos, misma conexión) para no
    # corromper el archivo — ver _LockedConnection.
    return _LockedConnection(conn)
