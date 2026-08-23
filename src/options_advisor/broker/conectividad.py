"""Detector de "el robot se quedó ciego": vigila si las llamadas a Schwab están fallando por RED.

Por qué existe (viernes 2026-08-21, encontrado el 23/08 leyendo el log):
la Mac perdió la resolución de DNS y TODAS las llamadas a la API empezaron a fallar con
`httpx.ConnectError: [Errno 8] nodename nor servname provided, or not known`. El robot siguió
"corriendo" — 309 escaneos completados ese día — pero 288 de ellos terminaron en menos de un
segundo porque cada símbolo reventaba al instante. Nadie se enteró. Ese mismo apagón de red es lo
que produjo la lectura vacía de posiciones que casi cierra 5 operaciones abiertas por error.

Un robot ciego que no avisa es peor que un robot apagado: parece que está trabajando. Esto lo
convierte en un aviso, una sola vez por apagón, con el mismo patrón anti-repetición que el
vigilante del token.

Distingue a propósito el fallo de RED (no llegamos a Schwab) del fallo de la API (Schwab contestó
un 4xx/5xx). El segundo es normal y esporádico; el primero significa que la máquina está aislada.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

FLAG_KEY = "schwab.sin_conexion_avisado"

# Cuántos fallos de red seguidos, y por cuánto tiempo, antes de dar por caída la conexión.
# Los dos a la vez: un puñado de fallos en un segundo puede ser un hipo del Wi-Fi; lo que importa
# es que no haya entrado NI UNA respuesta buena en varios minutos.
MIN_FALLOS_SEGUIDOS = 20
MIN_SEGUNDOS_SIN_EXITO = 180.0

_lock = threading.Lock()
_fallos_seguidos = 0
_ultimo_exito: float | None = None
_ultimo_error: str = ""


def registrar_exito() -> None:
    """Entró una respuesta buena de Schwab: la conexión está viva."""
    global _fallos_seguidos, _ultimo_exito
    with _lock:
        _fallos_seguidos = 0
        _ultimo_exito = time.time()


def registrar_fallo_de_red(error: BaseException | str) -> None:
    """No se pudo ni llegar a Schwab (DNS, Wi-Fi, TLS). NO se llama para 4xx/5xx."""
    global _fallos_seguidos, _ultimo_error
    with _lock:
        _fallos_seguidos += 1
        _ultimo_error = str(error)[:200]


def estado() -> tuple[int, float | None, str]:
    """(fallos de red seguidos, timestamp del último éxito, texto del último error)."""
    with _lock:
        return _fallos_seguidos, _ultimo_exito, _ultimo_error


def segundos_sin_exito(ahora: float | None = None) -> float | None:
    """Hace cuánto que no entra una respuesta buena. None si todavía no entró ninguna en esta
    corrida (recién arrancado: no sabemos nada, y no sabemos NO es lo mismo que está caído)."""
    _, ultimo, _ = estado()
    if ultimo is None:
        return None
    return (ahora if ahora is not None else time.time()) - ultimo


def esta_ciego(ahora: float | None = None) -> bool:
    """¿Damos por caída la conexión con Schwab? Función pura sobre el estado del proceso."""
    fallos, ultimo, _ = estado()
    if fallos < MIN_FALLOS_SEGUIDOS:
        return False
    if ultimo is None:
        # Nunca hubo una respuesta buena en esta corrida y ya llevamos muchos fallos seguidos:
        # arrancó sin red. Cuenta como ciego.
        return True
    return ((ahora if ahora is not None else time.time()) - ultimo) >= MIN_SEGUNDOS_SIN_EXITO


def _cuerpo_del_aviso(fallos: int, sin_exito: float | None, error: str) -> tuple[str, str]:
    if sin_exito is None:
        cuanto = "desde que arrancó"
    elif sin_exito >= 3600:
        cuanto = f"hace {sin_exito / 3600:.1f} horas"
    else:
        cuanto = f"hace {int(sin_exito // 60)} minutos"
    asunto = "📡 Lokshn: SIN CONEXIÓN con Schwab — el robot está ciego"
    cuerpo = (
        f"El robot no logra comunicarse con Schwab {cuanto} ({fallos} intentos seguidos fallados).\n\n"
        f"Último error de red: {error or 'sin detalle'}\n\n"
        "IMPORTANTE: esto NO es el token vencido. La máquina no está llegando a internet o no está\n"
        "resolviendo nombres de dominio. Mientras dure, el robot no ve precios, no abre nada y no\n"
        "puede cerrar por objetivo de ganancia — aunque en el dashboard parezca que está corriendo.\n\n"
        "Qué mirar, en orden:\n"
        "  1. ¿La Mac tiene Wi-Fi? Abrí cualquier página en el navegador.\n"
        "  2. Si el navegador anda pero esto sigue, es el DNS. Probá en la Terminal:\n"
        "       ping -c 2 api.schwabapi.com\n"
        "  3. Apagar y prender el Wi-Fi suele alcanzar. Si no, reiniciá el robot:\n"
        "       launchctl kickstart -k gui/$(id -u)/com.robertoajemblat.options-income-advisor.scheduler\n\n"
        "Te aviso una sola vez por apagón: cuando vuelva la conexión, el contador se reinicia solo.\n"
    )
    return asunto, cuerpo


def avisar_si_esta_ciego(conn: sqlite3.Connection, ahora: float | None = None) -> bool:
    """Manda UN aviso por apagón de red. Devuelve True si avisó.

    Nunca lanza: lo llama un job del scheduler y una falla de correo jamás debe tumbar el trading."""
    from options_advisor.alerts import notifier
    from options_advisor.storage.repository import get_robot_flag, set_robot_flag

    try:
        fallos, _, error = estado()
        if not esta_ciego(ahora):
            # Conexión sana: rehabilitamos el aviso para el próximo apagón.
            if get_robot_flag(conn, FLAG_KEY, "") :
                set_robot_flag(conn, FLAG_KEY, "")
            return False
        if get_robot_flag(conn, FLAG_KEY, ""):
            return False  # ya avisamos de ESTE apagón

        asunto, cuerpo = _cuerpo_del_aviso(fallos, segundos_sin_exito(ahora), error)
        notifier.send_email_robot_real(asunto, cuerpo)
        notifier.send_native("Sin conexión con Schwab: el robot no ve el mercado",
                             title="Lokshn", subtitle="SIN CONEXIÓN")
        set_robot_flag(conn, FLAG_KEY, "1")
        logger.error("Sin conexión con Schwab: %d fallos de red seguidos (%s)", fallos, error)
        return True
    except Exception:
        logger.exception("Falló el vigilante de conexión (no afecta el trading)")
        return False
