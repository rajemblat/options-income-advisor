"""Candado de zona horaria: el robot no arranca si el reloj del sistema no es el que espera.

Por qué existe (mudanza al servidor, 2026-08-23): hay 123 lugares en el código que preguntan la
hora con `datetime.now()` o `date.today()`, sin zona. Esas llamadas siguen la zona LOCAL de la
máquina. En la Mac de casa eso es Nueva York y todo cierra; en un servidor recién creado, que
viene en UTC, `date.today()` después de las 20:00 de Nueva York ya devuelve el día SIGUIENTE.

Qué se rompería, en concreto y con plata real:
  - el tope diario de órdenes se resetea a mitad de la tarde y el robot puede volver a operar;
  - `entry_date` / `log_date` quedan con la fecha equivocada, y con ellas los "días en la operación"
    y el anualizado real de cada cierre;
  - el aprendizaje y los reportes agrupan por día, así que quedan corridos.

Nada de eso se ve: no hay excepción, no hay error en el log. Los números simplemente son otros. Por
eso esto no avisa y sigue — impide arrancar. La corrección es un comando, y está en el mensaje.

Los horarios de mercado NO dependen de esto: `market_calendar.py` trabaja en UTC y los disparadores
de APScheduler llevan `timezone=` explícito. Esto protege únicamente a las llamadas sin zona.
"""

from __future__ import annotations

import platform
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ZonaHorariaIncorrecta(RuntimeError):
    """El reloj del sistema no está en la zona que el robot da por sentada."""


def zona_del_sistema_coincide(zona_esperada: str, ahora: datetime | None = None) -> bool:
    """¿El reloj local de esta máquina está en `zona_esperada` (p. ej. 'America/New_York')?

    Compara el DESFASE con UTC, no el nombre de la zona. Es a propósito: 'US/Eastern',
    'America/New_York' y 'EST5EDT' son nombres distintos del mismo reloj, y lo que le importa al
    código es la hora que devuelve `datetime.now()`, no cómo se llame. Comparar desfases también
    resuelve solo el horario de verano, sin tablas ni fechas a mano.
    """
    ahora = _reloj_del_sistema(ahora)
    return ahora.utcoffset() == ahora.astimezone(ZoneInfo(zona_esperada)).utcoffset()


def _reloj_del_sistema(ahora: datetime | None) -> datetime:
    """El instante a evaluar, con la zona del SISTEMA pegada.

    Sin argumento lo saca del reloj real (`datetime.now().astimezone()` toma la zona local). Con
    argumento, su `tzinfo` ES el reloj del sistema simulado — por eso no se re-convierte: hacerlo
    lo movería a la zona de la máquina que corre el test y no se podría probar ningún otro caso.
    Un valor sin zona se interpreta como hora local, igual que hace `datetime.now()`."""
    if ahora is None:
        return datetime.now().astimezone()
    return ahora.astimezone() if ahora.tzinfo is None else ahora


def _como_arreglarlo(zona_esperada: str) -> str:
    if platform.system() == "Darwin":
        return (
            "  Preferencias del Sistema → General → Fecha y hora → Zona horaria,\n"
            f"  y elegí {zona_esperada}. Si tenés activado 'Establecer zona horaria\n"
            "  automáticamente según la ubicación', desactivalo: viajar cambiaría el reloj\n"
            "  del robot."
        )
    return (
        f"  sudo timedatectl set-timezone {zona_esperada}\n"
        "  systemctl --user restart lokshn-robot          (sin sudo: es un servicio de usuario)\n\n"
        "  Para confirmar que quedó bien:  timedatectl | grep 'Time zone'"
    )


def exigir_zona_horaria(zona_esperada: str, ahora: datetime | None = None) -> None:
    """Lanza `ZonaHorariaIncorrecta` si el reloj del sistema no es el esperado.

    Se llama en el arranque de `scripts/run_scheduler.py`, antes de tocar la base o el broker."""
    try:
        coincide = zona_del_sistema_coincide(zona_esperada, ahora)
    except (ZoneInfoNotFoundError, KeyError) as exc:
        # La máquina no tiene la base de datos de zonas horarias (pasa en imágenes muy pelada de
        # Linux). Es un problema de la máquina, no del robot, pero igual no podemos verificar nada.
        raise ZonaHorariaIncorrecta(
            f"No se pudo resolver la zona horaria '{zona_esperada}' en esta máquina ({exc}).\n"
            "En Debian/Ubuntu se arregla con:  sudo apt install -y tzdata"
        ) from exc

    if coincide:
        return

    ahora = _reloj_del_sistema(ahora)
    esperada = ahora.astimezone(ZoneInfo(zona_esperada))
    raise ZonaHorariaIncorrecta(
        "EL RELOJ DE ESTA MÁQUINA NO ESTÁ EN LA ZONA QUE USA EL ROBOT.\n\n"
        f"  Zona que espera el robot : {zona_esperada}  (son las {esperada:%H:%M} de {esperada:%d/%m})\n"
        f"  Hora local de la máquina : {ahora:%H:%M} de {ahora:%d/%m}  (desfase {ahora.utcoffset()})\n\n"
        "El robot NO arranca así. Con el reloj corrido, el tope diario de órdenes se resetea a\n"
        "destiempo y las fechas de entrada, los días en la operación y el anualizado de cada cierre\n"
        "quedan mal. Nada de eso da error: los números simplemente salen distintos.\n\n"
        "Cómo se arregla:\n" + _como_arreglarlo(zona_esperada)
    )
