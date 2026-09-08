"""Entrypoint del proceso de scheduler (polling periódico durante horario de mercado).

Uso: python scripts/run_scheduler.py
"""

from __future__ import annotations

import logging
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
from options_advisor.scheduler import maquina_real, zona_horaria  # noqa: E402
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

    # CANDADO DE ZONA HORARIA (mudanza al servidor, 2026-08-23). Va acá arriba, antes de abrir la
    # base o hablar con el broker: si el reloj está corrido, todo lo que el robot escriba a partir
    # de este momento queda con la fecha equivocada, y eso no se nota hasta que se leen los números.
    try:
        zona_horaria.exigir_zona_horaria(settings.scheduler.timezone)
    except zona_horaria.ZonaHorariaIncorrecta as exc:
        print("\n" + "=" * 70)
        print("  EL ROBOT NO ARRANCA")
        print("=" * 70)
        print(exc)
        print("=" * 70 + "\n")
        sys.exit(1)

    # CANDADO DE MÁQUINA (2026-09-08). Va pegado al de zona horaria y por el mismo motivo: es un
    # error que no se ve. El 07/09 el servidor quedó en modo real y la Mac nunca se apagó — dos
    # robots reales sobre la misma cuenta, más de un día, sin una sola línea de error. Cada uno
    # lleva sus topes en su propia base, así que "1 condor por día" habrían sido dos.
    try:
        maquina_real.exigir_maquina_real(getattr(settings.live_trading, "real_machine_hostname", ""))
    except maquina_real.MaquinaEquivocada as exc:
        _lt0 = settings.live_trading
        if _lt0.enabled and not _lt0.dry_run and not _lt0.kill_switch:
            print("\n" + "=" * 70)
            print("  EL ROBOT NO ARRANCA — ESTA MÁQUINA NO ES LA QUE OPERA")
            print("=" * 70)
            print(exc)
            print("=" * 70 + "\n")
            logging.getLogger("options_advisor").error("Arranque abortado: %s", exc)
            sys.exit(1)
        # En modo prueba no molesta: mirar desde cualquier máquina es justamente lo que se quiere.
        logging.getLogger("options_advisor").info(
            "Esta máquina no es la designada para operar en real, pero arranca en modo prueba.")

    # Decir EN VOZ ALTA en qué modo arranca. Durante la mudanza (agosto 2026) conviven el robot de
    # la Mac —que opera de verdad— y el del servidor —que solo mira—, y confundirlos es la única
    # forma de que esto salga caro. Va a la consola y al log, antes que cualquier otra cosa.
    _lt = settings.live_trading
    _modo = ("MODO PRUEBA (mira pero NO opera)"
             if (_lt.dry_run or _lt.kill_switch or not _lt.enabled)
             else "MODO REAL (opera con plata de verdad)")
    print(f"\n>>> Lokshn arrancando en {_modo}\n")
    logging.getLogger("options_advisor").warning("Lokshn arranca en %s", _modo)

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
    # Conexión dedicada del hilo que DETECTA las operaciones reales (executor propio) — antes
    # compartía worker con el escaneo pesado de puts y quedaba minutos atrás (usuario 2026-08-17:
    # "demora más de 5 minutos y debe demorar menos de 10 segundos"). Mismo patrón que los de arriba.
    trades_conn = db.connect(settings.database.resolved_path())
    # Conexión dedicada del MANTENIMIENTO de posiciones reales (executor propio, cada 1 min): cierre
    # por objetivo de ganancia, re-precio y email de apertura. Antes colgaban del final del escaneo
    # pesado y llegaban tarde (usuario 2026-08-19). Mismo patrón seguro que los de arriba.
    live_conn = db.connect(settings.database.resolved_path())
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    finnhub_api_key = os.environ.get("FINNHUB_API_KEY")
    fred_api_key = os.environ.get("FRED_API_KEY")

    scheduler = build_scheduler(
        broker, conn, symbols, settings, api_key,
        finnhub_api_key=finnhub_api_key, fred_api_key=fred_api_key, butterfly_conn=butterfly_conn,
        chat_conn=chat_conn, trades_conn=trades_conn, live_conn=live_conn,
    )
    print(f"Scheduler iniciado (broker.mode={settings.broker.mode}, {len(symbols)} símbolos). Ctrl+C para salir.")
    scheduler.start()


if __name__ == "__main__":
    main()
