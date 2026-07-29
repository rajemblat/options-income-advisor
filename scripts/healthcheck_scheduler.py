"""Healthcheck del scheduler (pedido 2026-07-29, 3ra vez que el scheduler se cuelga "mudo" en 3
días): corre cada pocos minutos vía su PROPIO LaunchAgent (nunca dentro del proceso del
scheduler mismo — si el scheduler se cuelga, un healthcheck corriendo adentro se colgaría con
él, no serviría de nada). Detecta que el proceso está vivo mas no procesando nada (log sin
actividad reciente durante horario de mercado), lo reinicia vía `launchctl kickstart -k`, corre
un catch-up de detección de operaciones reales para cubrir el tiempo perdido, y notifica al
usuario con una notificación nativa de macOS (inmediata, no depende de que Telegram esté
configurado) más Telegram si está disponible.

Uso: python scripts/healthcheck_scheduler.py
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from options_advisor.alerts import notifier  # noqa: E402
from options_advisor.broker import get_broker_client  # noqa: E402
from options_advisor.config import load_settings  # noqa: E402
from options_advisor.scheduler.healthcheck import run_healthcheck  # noqa: E402
from options_advisor.storage import db  # noqa: E402

LAUNCHD_LABEL = "com.robertoajemblat.options-income-advisor.scheduler"
SCHEDULER_SCRIPT_MARKER = "run_scheduler.py"
LOG_PATH = PROJECT_ROOT / "data" / "logs" / "scheduler.err.log"
HEALTHCHECK_LOG_PATH = PROJECT_ROOT / "data" / "logs" / "healthcheck.log"


def _scheduler_pid() -> int | None:
    result = subprocess.run(["pgrep", "-f", SCHEDULER_SCRIPT_MARKER], capture_output=True, text=True)
    pids = [int(p) for p in result.stdout.split()]
    return pids[0] if pids else None


def _restart_scheduler() -> None:
    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"], check=True)


def _notify(message: str) -> None:
    logging.getLogger(__name__).warning(message)
    try:
        # -e con un solo string armado por nosotros (no interpola input externo) — no hay
        # inyección de AppleScript posible acá, `message` siempre lo arma este mismo módulo.
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{message}" with title "Options Income Advisor" subtitle "Scheduler" sound name "Basso"',
            ],
            timeout=5,
            check=False,
        )
    except Exception:
        logging.getLogger(__name__).exception("Fallo al mandar la notificación nativa de macOS")
    notifier.send_text(f"⚠️ {message}")  # no-op silencioso si Telegram no está configurado


def main() -> None:
    logging.basicConfig(
        filename=HEALTHCHECK_LOG_PATH, level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s"
    )
    settings = load_settings()
    broker = get_broker_client(settings)
    conn = db.connect(settings.database.resolved_path())
    run_healthcheck(
        settings=settings,
        broker=broker,
        conn=conn,
        now=datetime.now(timezone.utc),
        log_path=LOG_PATH,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        finnhub_api_key=os.environ.get("FINNHUB_API_KEY"),
        get_scheduler_pid=_scheduler_pid,
        restart_scheduler=_restart_scheduler,
        notify=_notify,
    )


if __name__ == "__main__":
    main()
