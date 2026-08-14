"""Entrypoint del proceso de scheduler (polling periódico durante horario de mercado).

Uso: python scripts/run_scheduler.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from options_advisor.broker import get_broker_client  # noqa: E402
from options_advisor.config import configure_logging, load_scan_symbols, load_settings  # noqa: E402
from options_advisor.scheduler import single_instance  # noqa: E402
from options_advisor.scheduler.runner import build_scheduler  # noqa: E402
from options_advisor.storage import db  # noqa: E402


def main() -> None:
    # CANDADO DE PROCESO ÚNICO (usuario 2026-08-14). El robot se puede arrancar por el LaunchAgent
    # (solo, con la Mac) o por "Iniciar Robot.command", y nada impedía que convivieran. Convivieron:
    # 486 decisiones duplicadas el 13/08, 25 el 14/08, dos condors de papel abiertos en el mismo
    # minuto y, al final, la base SQLite corrupta con los dos escribiéndola. Con plata real es peor:
    # los topes diarios se leen de la base, así que dos procesos pueden mandar cada uno su orden.
    try:
        single_instance.acquire(PROJECT_ROOT)
    except single_instance.RobotYaCorriendo as exc:
        print("\n" + "=" * 70)
        print("  NO SE ARRANCA UN SEGUNDO ROBOT")
        print("=" * 70)
        print(exc)
        print("=" * 70 + "\n")
        sys.exit(1)

    configure_logging()
    settings = load_settings()
    symbols = load_scan_symbols(settings.simulator.scan_full_universe)
    broker = get_broker_client(settings)
    conn = db.connect(settings.database.resolved_path())
    # Conexión dedicada para el hilo del Iron Butterfly (executor propio) — así su tick de 1 min no
    # queda atrás del escaneo pesado de puts. Dos conexiones en WAL en el mismo proceso son seguras
    # (SQLite serializa las escrituras con su lock de WAL + busy_timeout).
    butterfly_conn = db.connect(settings.database.resolved_path())
    # Conexión dedicada para el hilo del job rápido del chat (executor propio, cada 15s) — mismo patrón
    # seguro que el butterfly: dos conexiones en WAL en el mismo proceso, SQLite serializa las escrituras.
    chat_conn = db.connect(settings.database.resolved_path())
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    finnhub_api_key = os.environ.get("FINNHUB_API_KEY")
    fred_api_key = os.environ.get("FRED_API_KEY")

    scheduler = build_scheduler(
        broker, conn, symbols, settings, api_key,
        finnhub_api_key=finnhub_api_key, fred_api_key=fred_api_key, butterfly_conn=butterfly_conn,
        chat_conn=chat_conn,
    )
    print(f"Scheduler iniciado (broker.mode={settings.broker.mode}, {len(symbols)} símbolos). Ctrl+C para salir.")
    scheduler.start()


if __name__ == "__main__":
    main()
