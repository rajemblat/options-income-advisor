"""Cortacircuito: un endpoint que viene fallando deja de costar tiempo.

Nació el 2026-09-14, mirando por qué el robot no abrió NADA en toda la rueda.

Lo que pasaba: Schwab devolvía 502 en la cadena de opciones de `$SPX`. Un 502 es un error del
servidor, o sea de los que SÍ conviene reintentar, así que el cliente lo reintentaba 4 veces con
backoff exponencial. Con 15 s de timeout por intento más 1+2+4 s de espera, UN símbolo enfermo se
podía comer más de un minuto — y el escaneo del robot corre cada minuto. Resultado en el log:

    Execution of job "run_robot_scan" skipped: maximum number of running instances reached (1)

repetido minuto tras minuto. El escaneo nunca llegaba al final de la lista, así que los demás
símbolos —los que sí se podían operar— nunca se evaluaban. Un solo endpoint caído dejó al robot
sin operar el día entero, sin que nada dijera que eso estaba pasando.

La lección no es "no reintentes": reintentar está bien para un parpadeo. Es que el reintento supone
que el problema es pasajero, y cuando deja de serlo hay que dejar de pagarlo. Después de unos
fallos seguidos, este cortacircuito hace que las llamadas siguientes a ESE endpoint fallen al
instante durante un rato, en vez de volver a esperar. El símbolo enfermo se saltea rápido y el
escaneo sigue con los demás.

El corte es POR ENDPOINT, no global: que la cadena de $SPX esté caída no puede impedir pedir el
precio de AAPL. Y se cierra solo: pasado el enfriamiento se deja pasar un intento, y si anda,
vuelve a la normalidad.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

FALLOS_PARA_ABRIR = 3          # tres 5xx seguidos ya no son un parpadeo
ENFRIAMIENTO_SEGUNDOS = 300.0  # 5 minutos: suficiente para no insistir, corto para recuperarse solo


class EndpointCaido(RuntimeError):
    """El cortacircuito está abierto para este endpoint: se falla YA, sin reintentar.

    Es un tipo propio a propósito. No es `httpx.HTTPStatusError`, así que la política de reintentos
    no lo considera reintentable y tenacity lo deja pasar de una — que es justo lo que se busca."""


@dataclass
class _Estado:
    fallos: int = 0
    abierto_hasta: float = 0.0


@dataclass
class Cortacircuito:
    """Lleva la cuenta de fallos por clave. `reloj` se inyecta para poder testear sin dormir."""

    fallos_para_abrir: int = FALLOS_PARA_ABRIR
    enfriamiento_segundos: float = ENFRIAMIENTO_SEGUNDOS
    reloj: callable = time.monotonic
    _estados: dict[str, _Estado] = field(default_factory=dict)

    def _estado(self, clave: str) -> _Estado:
        return self._estados.setdefault(clave, _Estado())

    def esta_abierto(self, clave: str) -> bool:
        """¿Hay que fallar al instante? True mientras dure el enfriamiento.

        Pasado el enfriamiento devuelve False y deja el contador a un fallo de volver a abrir: si el
        endpoint sigue caído, el siguiente error lo vuelve a cortar sin gastar otra tanda entera."""
        est = self._estado(clave)
        if est.abierto_hasta and self.reloj() >= est.abierto_hasta:
            est.abierto_hasta = 0.0
            est.fallos = self.fallos_para_abrir - 1   # un intento de gracia
        return est.abierto_hasta > 0.0

    def registrar_fallo(self, clave: str) -> None:
        est = self._estado(clave)
        est.fallos += 1
        if est.fallos >= self.fallos_para_abrir and not est.abierto_hasta:
            est.abierto_hasta = self.reloj() + self.enfriamiento_segundos
            logger.warning(
                "Cortacircuito ABIERTO para %s tras %d fallos seguidos: se saltea por %.0f s para "
                "no frenar el resto del escaneo.", clave, est.fallos, self.enfriamiento_segundos)

    def registrar_exito(self, clave: str) -> None:
        """Anda de nuevo: se olvida todo. Un fallo aislado no debe acercar el corte para siempre."""
        est = self._estados.get(clave)
        if est and (est.fallos or est.abierto_hasta):
            if est.abierto_hasta:
                logger.info("Cortacircuito CERRADO para %s: volvió a responder.", clave)
            est.fallos = 0
            est.abierto_hasta = 0.0
