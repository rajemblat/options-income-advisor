"""Vigilante del vencimiento del token de Schwab (usuario 2026-08-18: "me avisas 24 horas antes").

Por qué existe: el refresh_token dura ~7 días y cuando vence el robot queda CIEGO — no puede pedir
ni una cotización, así que no evalúa nada y no opera. El 18/08 venció durante la noche y recién se
notó a las 11:46 de la mañana: media rueda perdida, con el mercado cayendo fuerte, y las dos órdenes
del día salieron recién 95 segundos después de reconectar. Este módulo avisa ANTES, con tiempo de
reconectar fuera del horario de mercado.

Diseño: dos avisos, cada uno una sola vez por token — a las 24 h ("te queda un día") y a las 6 h
("último llamado"), más uno cuando efectivamente venció. El anti-repetición se guarda en
`robot_flags` junto con el sello de emisión del token, así que al reconectar arranca de cero solo.
"""

from __future__ import annotations

import logging
import sqlite3
import time

from options_advisor.alerts import notifier
from options_advisor.broker.schwab_auth import read_refresh_token_seconds_left
from options_advisor.storage.repository import get_robot_flag, set_robot_flag

logger = logging.getLogger(__name__)

FLAG_KEY = "schwab.token_warn_sent"

# Umbrales en segundos, del más lejano al más urgente. El orden importa: se elige el más urgente
# que ya se haya cruzado, así un robot que estuvo apagado dos días manda "vencido", no "24h".
THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("24h", 24 * 3600),
    ("6h", 6 * 3600),
    ("vencido", 0.0),
)

_LEVEL_ORDER = {label: i for i, (label, _) in enumerate(THRESHOLDS)}


def pick_level(seconds_left: float) -> str | None:
    """Etiqueta del aviso que corresponde ahora, o None si todavía falta mucho.

    Función pura, sin base ni red: es la que se puede probar con fechas inventadas."""
    level = None
    for label, threshold in THRESHOLDS:
        if seconds_left <= threshold:
            level = label
    return level


def _already_sent(conn: sqlite3.Connection, token_id: str, level: str) -> bool:
    """¿Ya mandamos este aviso (o uno MÁS urgente) para este mismo token?

    La bandera guarda "<sello de emisión>:<nivel>". Comparar el sello es lo que hace que al
    reconectar el ciclo empiece limpio sin tener que acordarse de borrar nada."""
    raw = get_robot_flag(conn, FLAG_KEY, "") or ""
    previous_token, _, previous_level = raw.partition(":")
    if previous_token != token_id:
        return False
    return _LEVEL_ORDER.get(previous_level, -1) >= _LEVEL_ORDER[level]


def _message(level: str, seconds_left: float) -> tuple[str, str]:
    if level == "vencido":
        subject = "🔌 Lokshn: el token de Schwab VENCIÓ — el robot está ciego"
        headline = (
            "El token de Schwab venció. El robot NO puede leer precios ni operar hasta que "
            "te reconectes."
        )
    else:
        horas = max(0, int(seconds_left // 3600))
        urgencia = "ÚLTIMO LLAMADO" if level == "6h" else "Aviso"
        subject = f"⏳ Lokshn: al token de Schwab le quedan ~{horas} h ({urgencia})"
        headline = (
            f"Al token de Schwab le quedan aproximadamente {horas} horas. Cuando venza, el robot "
            "deja de ver el mercado y no opera hasta que te reconectes a mano."
        )
    body = (
        f"{headline}\n\n"
        "Reconectar toma 30 segundos:\n\n"
        "  1. Abrí la Terminal y corré:\n"
        "       cd ~/options-income-advisor\n"
        "       source .venv/bin/activate\n"
        "       python scripts/schwab_login.py\n\n"
        "  2. Abrí la URL que imprime, iniciá sesión en Schwab y aprobá el acceso.\n"
        "     El navegador va a mostrar un error de 'no se puede acceder a este sitio' —\n"
        "     es esperado. Copiá la URL completa de la barra de direcciones.\n\n"
        "  3. Pegala en la Terminal cuando te la pida.\n\n"
        "  4. Reiniciá el robot para que tome los tokens nuevos:\n"
        "       launchctl kickstart -k gui/$(id -u)/com.robertoajemblat.options-income-advisor.scheduler\n\n"
        "El paso 4 no es opcional: el robot cachea los tokens en memoria al arrancar, así que "
        "sin reinicio sigue usando el viejo aunque ya te hayas reconectado.\n"
    )
    return subject, body


def check_and_warn(conn: sqlite3.Connection, *, token_store_path=None) -> str | None:
    """Chequea cuánto le queda al token y avisa si toca. Devuelve el nivel avisado, o None.

    Nunca lanza: lo llama un job del scheduler y una falla de correo jamás debe tumbar el trading."""
    try:
        seconds_left = (
            read_refresh_token_seconds_left(token_store_path)
            if token_store_path is not None
            else read_refresh_token_seconds_left()
        )
        if seconds_left is None:
            return None

        level = pick_level(seconds_left)
        if level is None:
            return None

        # Identifica al token actual: si te reconectaste, cambia y los avisos se rehabilitan solos.
        token_id = _token_id(seconds_left)
        if _already_sent(conn, token_id, level):
            return None

        subject, body = _message(level, seconds_left)
        enviado = notifier.send_email(subject, body)
        notifier.send_native(
            "Reconectá Schwab: corré scripts/schwab_login.py",
            title="Lokshn",
            subtitle=subject.split(": ", 1)[-1],
        )
        # La bandera se pone SOLO si el mail salio de verdad.
        #
        # Antes se ponia siempre. El 30/08 el envio fallo por DNS, la bandera quedo marcada como
        # "nivel 24h ya avisado", y el vigilante -- que corre cada hora -- no volvio a intentarlo
        # nunca. El aviso murio por un parpadeo de internet de segundos. Ahora, si el mail no sale,
        # no se marca nada y a la hora siguiente se reintenta.
        if not enviado:
            logger.error("Aviso de token nivel %s NO se pudo enviar por mail — se reintenta en la "
                         "proxima corrida (dentro de una hora)", level)
            return None
        set_robot_flag(conn, FLAG_KEY, f"{token_id}:{level}")
        logger.warning("Aviso de vencimiento del token de Schwab enviado (nivel %s)", level)
        return level
    except Exception:
        logger.exception("Falló el vigilante del token de Schwab (no afecta el trading)")
        return None


def _token_id(seconds_left: float) -> str:
    """Identificador estable del token actual: el instante de vencimiento redondeado al minuto.
    Derivarlo de `seconds_left` evita releer el archivo, y cambia en cuanto te reconectás."""
    return str(int((time.time() + seconds_left) // 60))
