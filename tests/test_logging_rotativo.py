"""El log del robot no puede volver a comerse el disco.

El 21/08 la Mac se quedó sin DNS, cada símbolo de cada escaneo volcó su stack trace completo, y
data/logs/scheduler.err.log llegó a 560 MB. Ese archivo lo escribe launchd y NO rota: si el disco
se llena con el robot operando plata real, se pierde la rueda.
"""

from __future__ import annotations

import logging
import logging.handlers

import yaml

from options_advisor.config import PROJECT_ROOT, configure_logging


def _config_cruda() -> dict:
    with open(PROJECT_ROOT / "config" / "logging.yaml") as f:
        return yaml.safe_load(f)


def _restaurar(anteriores):
    raiz = logging.getLogger()
    for h in raiz.handlers[:]:
        h.close()
        raiz.removeHandler(h)
    for h in anteriores:
        raiz.addHandler(h)


def test_el_archivo_de_log_rota_con_techo_conocido():
    archivo = _config_cruda()["handlers"]["archivo"]
    assert archivo["class"] == "logging.handlers.RotatingFileHandler"
    techo = archivo["maxBytes"] * (archivo["backupCount"] + 1)
    assert techo <= 200 * 1024 * 1024, f"El log puede crecer hasta {techo / 1e6:.0f} MB"


def test_la_consola_solo_recibe_warnings():
    """La consola es lo que launchd guarda en scheduler.err.log, que no rota. Ahí solo va lo que
    hay que mirar; el detalle completo vive en el archivo rotado."""
    assert _config_cruda()["handlers"]["console"]["level"] == "WARNING"


def test_configure_logging_crea_la_carpeta_que_falta(tmp_path):
    destino = tmp_path / "sub" / "carpeta" / "robot.log"
    yaml_tmp = tmp_path / "logging.yaml"
    yaml_tmp.write_text(yaml.safe_dump({
        "version": 1, "disable_existing_loggers": False,
        "handlers": {"archivo": {"class": "logging.handlers.RotatingFileHandler",
                                 "filename": str(destino), "maxBytes": 1024, "backupCount": 1}},
        "root": {"level": "INFO", "handlers": ["archivo"]},
    }))
    anteriores = logging.getLogger().handlers[:]
    try:
        configure_logging(yaml_tmp)
        logging.getLogger("prueba").info("hola")
        assert destino.exists()
    finally:
        _restaurar(anteriores)


def test_una_ruta_relativa_cae_dentro_del_proyecto(tmp_path):
    """Sin esto, arrancar el robot a mano desde otra carpeta escribiría el log en cualquier lado."""
    yaml_tmp = tmp_path / "logging.yaml"
    yaml_tmp.write_text(yaml.safe_dump({
        "version": 1, "disable_existing_loggers": False,
        "handlers": {"archivo": {"class": "logging.handlers.RotatingFileHandler",
                                 "filename": "data/logs/prueba_ruta.log", "maxBytes": 1024, "backupCount": 1}},
        "root": {"level": "INFO", "handlers": ["archivo"]},
    }))
    anteriores = logging.getLogger().handlers[:]
    try:
        configure_logging(yaml_tmp)
        escrito = [h for h in logging.getLogger().handlers
                   if isinstance(h, logging.handlers.RotatingFileHandler)][0]
        assert escrito.baseFilename == str(PROJECT_ROOT / "data" / "logs" / "prueba_ruta.log")
    finally:
        _restaurar(anteriores)
        (PROJECT_ROOT / "data" / "logs" / "prueba_ruta.log").unlink(missing_ok=True)


def test_apscheduler_sigue_en_info_porque_es_el_latido():
    """No bajar `apscheduler` a WARNING.

    `scripts/healthcheck_scheduler.py` da por colgado al robot si el log pasa ~9 minutos sin
    escribirse, y lo reinicia. Las líneas de APScheduler ("Running job...", cada 15 segundos) son
    lo que mantiene ese latido vivo. Silenciarlas dejaría el archivo quieto con el robot sano y el
    healthcheck lo reiniciaría en bucle en pleno horario de mercado. El tamaño del log lo controla
    la rotación, no el silencio."""
    loggers = _config_cruda().get("loggers") or {}
    nivel = (loggers.get("apscheduler") or {}).get("level")
    assert nivel in (None, "INFO", "DEBUG"), (
        f"apscheduler quedó en {nivel}: sin sus líneas el healthcheck reinicia el robot en bucle."
    )
