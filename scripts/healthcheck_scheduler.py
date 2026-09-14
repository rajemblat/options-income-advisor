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

from options_advisor.alerts import freno_de_avisos, notifier  # noqa: E402
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
# Estado del FRENO DE AVISOS (2026-09-14). El viernes 11 el robot entró en bucle —se caía, este
# healthcheck lo levantaba, se volvía a caer— y cada vuelta mandaba un mail. El usuario abrió el
# correo el lunes con miles: "tampoco quiero que me lleguen más estos emails, me llegan miles".
# El aviso estaba bien, el volumen no: mil mails iguales entierran al que sí importa.
FRENO_PATH = PROJECT_ROOT / "data" / "logs" / "avisos_healthcheck.json"
# Claves de problema. Separadas a propósito: que el robot se esté colgando no puede silenciar el
# aviso de "no lo puedo levantar", que es mucho más grave y necesita que el usuario haga algo.
CLAVE_COLGADO = "scheduler.colgado"
CLAVE_NO_ARRANCA = "scheduler.no_arranca"


def _avisar_con_freno(clave: str, asunto: str, cuerpo: str) -> bool:
    """Manda el mail solo si el freno lo deja pasar. Devuelve si se mandó.

    El primero de cada problema sale al instante; mientras el problema siga se cuenta en silencio;
    cada 6 h sale un recordatorio que dice cuántas veces volvió a pasar. Si algo del freno falla,
    el mail sale igual: este mecanismo está para que los avisos sirvan, nunca para tragárselos."""
    try:
        estado = freno_de_avisos.cargar(FRENO_PATH)
        decision = freno_de_avisos.decidir(estado, clave, datetime.now())
        freno_de_avisos.guardar(FRENO_PATH, estado)
    except Exception:
        logging.getLogger(__name__).exception("Freno de avisos: falló; se manda el mail igual")
        decision = None

    if decision is not None and not decision.avisar:
        logging.getLogger(__name__).warning(
            "Aviso '%s' callado por el freno (van %d repeticiones en este episodio). "
            "El próximo mail sale a las 6 h del anterior.", clave, decision.total)
        return False

    extra = decision.texto_de_repeticiones if decision is not None else ""
    try:
        notifier.send_email_robot_real(asunto, cuerpo + (f"\n{extra}\n" if extra else ""))
        return True
    except Exception:
        logging.getLogger(__name__).exception("Fallo al mandar el email del healthcheck")
        return False


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
    sin permisos de administrador.

    NUNCA LANZA. Esto es lo que se rompió el 2026-08-20 y no se descubrió hasta el 23:

        launchctl kickstart ... -> exit 113
        Could not find service "...scheduler" in domain for user gui: 501

    El agente del robot estaba descargado (lo habíamos bajado para reparar la base). El healthcheck
    lo detectó bien y quiso revivirlo, pero el `check=True` convirtió el fallo en una excepción que
    mató al proceso entero. launchd dejó de reintentar y el healthcheck no volvió a correr en TRES
    DÍAS, sin que nadie se enterara.

    O sea: el componente que existe para ser la última línea de defensa se suicidó exactamente en
    el escenario que tenía que reportar. Ahora el fallo se avisa —por email, fuerte y claro, porque
    "tu robot está muerto y no lo puedo revivir" es el mensaje más importante que este sistema
    puede mandar— y el healthcheck sigue vivo para reintentar en la próxima corrida."""
    if platform.system() == "Darwin":
        comando = ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"]
        como_arreglarlo = (
            "El LaunchAgent del robot no está cargado. Cargalo con:\n\n"
            f"    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist\n\n"
            "Y verificá que quedó con:\n\n"
            "    launchctl list | grep options-income-advisor"
        )
    else:
        comando = ["systemctl", "--user", "restart", SYSTEMD_UNIT]
        como_arreglarlo = (
            "El servicio del robot no está habilitado. Habilitalo con:\n\n"
            f"    systemctl --user enable --now {SYSTEMD_UNIT}\n\n"
            "Y verificá con:\n\n"
            f"    systemctl --user status {SYSTEMD_UNIT}"
        )

    resultado = subprocess.run(comando, capture_output=True, text=True)
    if resultado.returncode == 0:
        return

    detalle = (resultado.stderr or resultado.stdout or "").strip() or f"código {resultado.returncode}"
    logging.getLogger(__name__).error("NO se pudo reiniciar el robot: %s", detalle)
    _avisar_con_freno(
        CLAVE_NO_ARRANCA,
        "🚨 Lokshn: el robot está CAÍDO y no lo puedo levantar",
        "El healthcheck detectó que el robot no está corriendo e intentó reiniciarlo, pero el\n"
        "sistema rechazó el comando. El robot NO está operando y esto necesita tu intervención.\n\n"
        f"Comando que falló:\n    {' '.join(comando)}\n\n"
        f"Respuesta del sistema:\n    {detalle}\n\n"
        f"{como_arreglarlo}\n",
    )


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

    _avisar_con_freno(
        CLAVE_COLGADO,
        "🔧 Lokshn: el robot se colgó y lo reinicié solo",
        f"{message}\n\n"
        "Lo hizo el healthcheck automáticamente, así que no tenés que hacer nada. Te llega\n"
        "este aviso para que sepas que pasó: si se repite varias veces en el mismo día,\n"
        "algo de fondo anda mal y conviene mirarlo.\n\n"
        "El detalle queda en data/logs/healthcheck.log.\n",
    )

    notifier.send_text(f"⚠️ {message}")  # no-op silencioso si Telegram no está configurado


def main() -> None:
    logging.basicConfig(
        filename=HEALTHCHECK_LOG_PATH, level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s"
    )
    settings = load_settings()
    broker = get_broker_client(settings)
    conn = db.connect(settings.database.resolved_path())

    # Si esta corrida NO tuvo que avisar nada, el robot está sano: se limpia el freno para que el
    # PRÓXIMO episodio vuelva a avisar al instante (2026-09-14). Sin esto, un problema que aparece,
    # se va y vuelve dos horas después quedaría callado por el freno del episodio anterior — justo
    # al revés de lo que uno quiere de un sistema de alertas.
    hubo_aviso = False

    def _notify_marcando(message: str) -> None:
        nonlocal hubo_aviso
        hubo_aviso = True
        _notify(message)

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
        notify=_notify_marcando,
    )

    if not hubo_aviso:
        try:
            estado = freno_de_avisos.cargar(FRENO_PATH)
            if estado:
                # `marcar_resuelto` solo limpia si el problema estuvo quieto una ventana entera.
                # Limpiar apenas UNA corrida lo encontraba sano era el bug del 14/09: el robot se
                # reiniciaba bien, la corrida siguiente lo veía vivo, se borraba el freno, y la
                # próxima caída volvía a mandar mail. El usuario lo recibió cada dos horas.
                veces = sum(freno_de_avisos.marcar_resuelto(estado, c, datetime.now())
                            for c in (CLAVE_COLGADO, CLAVE_NO_ARRANCA))
                freno_de_avisos.guardar(FRENO_PATH, estado)
                if veces:
                    logging.getLogger(__name__).info(
                        "El robot está sano de nuevo. El episodio anterior tuvo %d incidente(s); "
                        "el freno de avisos queda limpio.", veces)
        except Exception:
            logging.getLogger(__name__).exception("Freno de avisos: no se pudo limpiar el estado")


if __name__ == "__main__":
    main()
