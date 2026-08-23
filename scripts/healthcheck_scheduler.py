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
import platform
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

LAUNCHD_LABEL = "com.robertoajemblat.options-income-advisor.scheduler"   # macOS
SYSTEMD_UNIT = "lokshn-robot.service"                                    # Linux (servicio de usuario)
SCHEDULER_SCRIPT_MARKER = "run_scheduler.py"
# El healthcheck decide si el robot está colgado mirando la fecha de modificación de este archivo.
# Tiene que ser el log donde el robot escribe SIEMPRE que está sano — o sea el rotado de INFO
# (config/logging.yaml, handler `archivo`), NO el de launchd. Desde el 23/08 la consola quedó en
# WARNING para que scheduler.err.log deje de crecer sin control; un robot sano no escribe nada ahí,
# así que apuntar acá a scheduler.err.log significaría "sin actividad" cada pocos minutos y este
# script lo reiniciaría en bucle con el mercado abierto. El test tests/test_scheduler/
# test_healthcheck_log_path.py ata las dos rutas para que no se separen nunca más.
LOG_PATH = PROJECT_ROOT / "data" / "logs" / "robot.log"
HEALTHCHECK_LOG_PATH = PROJECT_ROOT / "data" / "logs" / "healthcheck.log"


def _scheduler_pid() -> int | None:
    result = subprocess.run(["pgrep", "-f", SCHEDULER_SCRIPT_MARKER], capture_output=True, text=True)
    pids = [int(p) for p in result.stdout.split()]
    return pids[0] if pids else None


def _restart_scheduler() -> None:
    """Reinicia el robot con la herramienta del sistema donde esté corriendo.

    En la Mac lo maneja launchd; en el servidor, systemd. Se elige en tiempo de ejecución en vez de
    tener dos versiones del script: el healthcheck es justamente lo que repara el robot cuando se
    cuelga, y tener que acordarse de editarlo al mudar la máquina es la clase de detalle que se
    olvida y se descubre el día que hace falta.

    `systemctl --user` va SIN sudo a propósito: el robot corre como servicio de usuario (con
    linger activado para que arranque al prender el servidor), así que puede reiniciarse a sí mismo
    sin permisos de administrador."""
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"], check=True)
    else:
        subprocess.run(["systemctl", "--user", "restart", SYSTEMD_UNIT], check=True)


def _notify(message: str) -> None:
    """Avisa por todos los canales disponibles en esta máquina. Ninguno puede tumbar al otro.

    El EMAIL se agregó al mudarse al servidor (2026-08-23) y no es un lujo: en la Mac el aviso
    llegaba como notificación de macOS y alcanzaba porque el usuario estaba delante. En un servidor
    no hay pantalla, y Telegram nunca se configuró — o sea que un robot colgado se habría reparado
    en silencio, sin que nadie se enterara de que se colgó. El email es el único canal que el
    usuario lee de verdad."""
    logging.getLogger(__name__).warning(message)

    # Notificación nativa: solo en macOS. En Linux `osascript` no existe y esto tiraría una
    # excepción con traceback en cada corrida del healthcheck.
    if platform.system() == "Darwin":
        try:
            # -e con un solo string armado por nosotros (no interpola input externo) — no hay
            # inyección de AppleScript posible acá, `message` siempre lo arma este mismo módulo.
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{message}" with title "OptionsUp" subtitle "Scheduler" sound name "Basso"',
                ],
                timeout=5,
                check=False,
            )
        except Exception:
            logging.getLogger(__name__).exception("Fallo al mandar la notificación nativa de macOS")

    try:
        notifier.send_email_robot_real(
            "🔧 Lokshn: el robot se colgó y lo reinicié solo",
            f"{message}\n\n"
            "Lo hizo el healthcheck automáticamente, así que no tenés que hacer nada. Te llega\n"
            "este aviso para que sepas que pasó: si se repite varias veces en el mismo día,\n"
            "algo de fondo anda mal y conviene mirarlo.\n\n"
            "El detalle queda en data/logs/healthcheck.log.\n",
        )
    except Exception:
        logging.getLogger(__name__).exception("Fallo al mandar el email del healthcheck")

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
