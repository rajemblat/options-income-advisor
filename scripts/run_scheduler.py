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
from options_advisor.config import configure_logging, load_settings, load_symbols  # noqa: E402
from options_advisor.scheduler.runner import build_scheduler  # noqa: E402
from options_advisor.storage import db  # noqa: E402


def main() -> None:
    configure_logging()
    settings = load_settings()
    symbols = load_symbols()
    broker = get_broker_client(settings)
    conn = db.connect(settings.database.resolved_path())
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    scheduler = build_scheduler(broker, conn, symbols, settings, api_key)
    print(f"Scheduler iniciado (broker.mode={settings.broker.mode}, {len(symbols)} símbolos). Ctrl+C para salir.")
    scheduler.start()


if __name__ == "__main__":
    main()
