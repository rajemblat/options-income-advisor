"""El healthcheck tiene que vigilar el log que el robot realmente escribe.

Trampa encontrada el 23/08 al pasar la consola a WARNING: `scripts/healthcheck_scheduler.py`
decide si el robot está colgado mirando la FECHA DE MODIFICACIÓN de un archivo de log. Apuntaba a
data/logs/scheduler.err.log — el de launchd. Con la consola en WARNING un robot SANO no escribe
nada ahí, así que su mtime se congela y el healthcheck lo habría reiniciado en bucle, con el
mercado abierto y posiciones reales puestas.

Este test ata la ruta del healthcheck al handler de archivo de config/logging.yaml. Si mañana
alguien cambia uno de los dos, salta acá y no en producción un lunes a las 10 de la mañana.
"""

from __future__ import annotations

import re

import yaml

from options_advisor.config import PROJECT_ROOT


def _ruta_del_handler_de_archivo() -> str:
    with open(PROJECT_ROOT / "config" / "logging.yaml") as f:
        cfg = yaml.safe_load(f)
    archivo = cfg["handlers"]["archivo"]["filename"]
    return archivo.rsplit("/", 1)[-1]


def _ruta_que_vigila_el_healthcheck() -> str:
    fuente = (PROJECT_ROOT / "scripts" / "healthcheck_scheduler.py").read_text()
    linea = re.search(r'^LOG_PATH\s*=\s*(.+)$', fuente, re.M)
    assert linea, "No se encontró LOG_PATH en healthcheck_scheduler.py"
    nombre = re.findall(r'"([^"]+\.log)"', linea.group(1))
    assert nombre, f"No se pudo leer el archivo de LOG_PATH: {linea.group(1)}"
    return nombre[-1]


def test_el_healthcheck_vigila_el_log_que_el_robot_escribe():
    assert _ruta_que_vigila_el_healthcheck() == _ruta_del_handler_de_archivo()


def test_el_healthcheck_no_vigila_el_log_de_launchd():
    """scheduler.err.log solo recibe WARNING+. Un robot sano no lo toca: no sirve de latido."""
    assert _ruta_que_vigila_el_healthcheck() != "scheduler.err.log"
