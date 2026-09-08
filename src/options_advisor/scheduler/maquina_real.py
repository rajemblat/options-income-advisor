"""Candado de máquina: solo UNA computadora puede operar con plata real, y está escrita en el config.

Por qué existe (2026-09-08, con dinero real en juego). Durante la mudanza al servidor conviven dos
robots: el de la Mac, que opera, y el del servidor, que solo mira. El 07/09 la mudanza avanzó y el
servidor quedó en modo real — pero el robot de la Mac nunca se apagó, que era el primer paso. Los
dos quedaron en MODO REAL, y así estuvieron más de un día sin que nada lo detectara.

No pasó nada de casualidad: el servidor no estaba armado. Hasta que el 08/09 el usuario dio START
en el dashboard del servidor creyendo que era el de la Mac —son idénticos— y quedó habilitado para
operar. Se frenó a los minutos, sin órdenes enviadas.

Qué habría pasado si no: cada robot lleva sus topes diarios en SU PROPIA base y ninguno ve la del
otro. "1 condor por día" son dos condors. "2 órdenes por día" son cuatro. El doble de riesgo del
autorizado, por acumulación silenciosa. El candado de proceso único (`single_instance`) no protege
de esto: usa un `flock` sobre un archivo local y entre máquinas distintas no ve nada.

Cómo funciona: el config dice CUÁL máquina opera de verdad (`live_trading.real_machine_hostname`).
Cualquier otra que arranque en modo real se niega y explica cómo corregirlo. El dato vive en git, se
ve en un diff, y viaja con el `git pull` — así que mudarse es cambiar ESA línea: en el mismo commit
el servidor pasa a poder operar y la Mac deja de poder. Mutuamente excluyente por construcción, no
por acordarse.

Vacío = candado apagado, para no cambiarle el comportamiento a nadie que no lo configure.
"""

from __future__ import annotations

import platform


class MaquinaEquivocada(RuntimeError):
    """Esta computadora no es la designada para operar con plata real."""


def nombre_de_esta_maquina(nombre: str | None = None) -> str:
    """El nombre de red de esta computadora, normalizado."""
    return _normalizar(nombre if nombre is not None else platform.node())


def _normalizar(nombre: str) -> str:
    """Solo la primera etiqueta, en minúsculas: 'MacBook-Pro-8.local' y 'macbook-pro-8' son la misma
    máquina. macOS agrega y saca el '.local' según cómo esté la red, y eso no puede ser la diferencia
    entre operar y no operar."""
    return str(nombre or "").strip().split(".")[0].lower()


def es_la_maquina_real(esperado: str | None, nombre: str | None = None) -> bool:
    """¿Esta computadora es la que el config designó para operar con plata real?
    Sin `esperado` configurado devuelve True: el candado nace apagado."""
    esperado_norm = _normalizar(esperado or "")
    if not esperado_norm:
        return True
    return nombre_de_esta_maquina(nombre) == esperado_norm


def exigir_maquina_real(esperado: str | None, nombre: str | None = None) -> None:
    """Lanza `MaquinaEquivocada` si esta computadora no es la designada. El mensaje trae los dos
    caminos posibles, porque quien lo lee está en medio de una mudanza y tiene que decidir cuál de
    las dos máquinas opera — no adivinar qué archivo tocar."""
    if es_la_maquina_real(esperado, nombre):
        return
    soy = nombre_de_esta_maquina(nombre)
    raise MaquinaEquivocada(
        f"Esta computadora se llama '{soy}', pero el config dice que la que opera con plata real es\n"
        f"'{_normalizar(esperado or '')}' (live_trading.real_machine_hostname).\n\n"
        "Dos robots en modo real sobre la misma cuenta duplican los topes diarios: cada uno lleva\n"
        "los suyos en su propia base y ninguno ve al otro. '1 condor por día' se convierte en dos.\n\n"
        "Elegí UNA de las dos:\n\n"
        "  a) Esta máquina NO tiene que operar — ponela a mirar:\n"
        "       ./.venv/bin/python deploy/modo.py prueba\n\n"
        "  b) Esta máquina SÍ pasa a ser la que opera — entonces primero APAGÁ la otra, y recién\n"
        f"     después cambiá en config/settings.yaml:  real_machine_hostname: \"{soy}\"\n"
        "     (ese cambio va a git: en el mismo commit una empieza a operar y la otra deja de poder)"
    )
