"""Freno para avisos que se repiten — que un problema mande UN mail, no mil.

Nació el 2026-09-14. El viernes 11 el robot entró en bucle (se caía, el healthcheck lo levantaba,
se volvía a caer) y el usuario abrió el correo el lunes con miles de mails idénticos: "🔧 Lokshn: el
robot se colgó y lo reinicié solo". Sus palabras: "tampoco quiero que me lleguen más estos emails,
me llegan miles".

Lo importante es que el aviso en sí estaba BIEN: el robot se estaba cayendo de verdad y había que
enterarse. Lo que estaba mal era el volumen. Y no es un detalle estético: mil mails iguales es peor
que ninguno, porque el que los recibe deja de mirarlos y el día que llegue uno distinto —uno que sí
necesita su intervención— va a estar enterrado entre los otros. Un canal que grita siempre deja de
ser un canal.

La regla, entonces:

  · el PRIMER aviso de un problema sale al instante, sin esperar nada;
  · mientras el mismo problema siga, se calla y solo se cuenta;
  · pasada la ventana (6 h por defecto) sale UN aviso de recordatorio que dice cuántas veces
    volvió a pasar mientras estuvo callado — esa cuenta es la información valiosa, porque
    distingue "se colgó una vez" de "se colgó 400 veces";
  · cuando el problema se resuelve DE VERDAD —o sea, estuvo sano una ventana entera, no un rato—
    se limpia el estado, así el próximo episodio vuelve a avisar al instante en vez de quedar
    tapado por el freno del anterior.

Este módulo es pura decisión sobre un diccionario: no manda mails, no lee el reloj del sistema ni
abre archivos por su cuenta. Así se puede testear el comportamiento —que es lo delicado— sin
mandar un solo mail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

VENTANA_POR_DEFECTO_HORAS = 6.0


@dataclass(frozen=True)
class Decision:
    """Qué hacer con este aviso.

    `calladas` son las veces que el mismo problema se repitió mientras el freno lo tapaba. Va en el
    cuerpo del mail cuando finalmente sale: "volvió a pasar 412 veces desde el aviso anterior" es
    la diferencia entre un hipo y un incendio."""

    avisar: bool
    calladas: int
    total: int

    @property
    def texto_de_repeticiones(self) -> str:
        """Frase lista para pegar en el cuerpo del mail. Vacía si no hubo repeticiones calladas."""
        if self.calladas <= 0:
            return ""
        return (f"Esto volvió a pasar {self.calladas} vez/veces desde el aviso anterior "
                f"({self.total} en total en este episodio). No te mandé un mail por cada una a "
                f"propósito.")


def decidir(estado: dict, clave: str, ahora: datetime,
            *, ventana_horas: float = VENTANA_POR_DEFECTO_HORAS) -> Decision:
    """Registra una ocurrencia de `clave` y decide si corresponde avisar. MUTA `estado`.

    `clave` identifica el PROBLEMA, no el momento: todos los "el robot se colgó" comparten clave,
    así el freno los agrupa. Dos problemas distintos tienen claves distintas y no se tapan entre
    sí — que el robot se esté colgando no puede silenciar un "no lo puedo levantar"."""
    entrada = estado.setdefault(clave, {"ultimo_aviso": None, "calladas": 0, "total": 0})
    entrada["total"] = int(entrada.get("total", 0)) + 1
    entrada["ultimo_evento"] = ahora.isoformat()   # para saber cuándo dejó de pasar de verdad

    ultimo = entrada.get("ultimo_aviso")
    if not ultimo:
        entrada["ultimo_aviso"] = ahora.isoformat()
        entrada["calladas"] = 0
        return Decision(avisar=True, calladas=0, total=entrada["total"])

    try:
        desde = (ahora - datetime.fromisoformat(ultimo)).total_seconds() / 3600.0
    except (TypeError, ValueError):
        # Estado corrupto (archivo editado a mano, versión vieja): se avisa, que es el lado seguro
        # del error — preferimos un mail de más que perder el aviso de un robot caído.
        entrada["ultimo_aviso"] = ahora.isoformat()
        entrada["calladas"] = 0
        return Decision(avisar=True, calladas=0, total=entrada["total"])

    if desde >= ventana_horas:
        calladas = int(entrada.get("calladas", 0))
        entrada["ultimo_aviso"] = ahora.isoformat()
        entrada["calladas"] = 0
        return Decision(avisar=True, calladas=calladas, total=entrada["total"])

    entrada["calladas"] = int(entrada.get("calladas", 0)) + 1
    return Decision(avisar=False, calladas=entrada["calladas"], total=entrada["total"])


def marcar_resuelto(estado: dict, clave: str, ahora: datetime,
                    *, sano_durante_horas: float = VENTANA_POR_DEFECTO_HORAS) -> int:
    """El problema dejó de pasar: si estuvo sano lo suficiente, borra su estado y devuelve cuántas
    veces había ocurrido. Si todavía no pasó esa ventana, no limpia nada y devuelve 0.

    `sano_durante_horas` es la corrección del 2026-09-14, y es el corazón del asunto. La primera
    versión limpiaba en cuanto UNA corrida encontraba todo bien, y con eso el freno no servía para
    nada en el caso que más importaba:

        el robot se cae → se avisa → el healthcheck lo reinicia bien → la corrida siguiente lo ve
        sano → se limpiaba el freno → el robot se vuelve a caer → se avisa OTRA VEZ

    El usuario recibió ese mail cada dos horas toda la jornada. El bucle de caídas es justamente el
    escenario donde el freno tiene que actuar, y era el único donde no actuaba.

    Limpiar sigue siendo necesario —sin eso, un problema que aparece, se va y vuelve dos horas
    después quedaría callado por el freno del episodio anterior—. Lo que cambia es qué cuenta como
    "se fue": no alcanza con un respiro, tiene que aguantar sano una ventana entera."""
    entrada = estado.get(clave)
    if not entrada:
        return 0
    ultimo = entrada.get("ultimo_evento") or entrada.get("ultimo_aviso")
    if ultimo:
        try:
            sano = (ahora - datetime.fromisoformat(ultimo)).total_seconds() / 3600.0
        except (TypeError, ValueError):
            sano = float("inf")   # estado ilegible: mejor limpiar que arrastrar basura
        if sano < sano_durante_horas:
            return 0              # respiró, pero todavía no se puede decir que se resolvió
    estado.pop(clave, None)
    return int(entrada.get("total", 0))


# --- Persistencia (lo único que toca el disco) ---

def cargar(ruta: Path) -> dict:
    """Lee el estado. Un archivo ausente o ilegible devuelve estado vacío: este módulo existe para
    que los avisos funcionen mejor, nunca para impedir que salga un aviso."""
    try:
        with open(ruta, encoding="utf-8") as fh:
            datos = json.load(fh)
        return datos if isinstance(datos, dict) else {}
    except Exception:
        return {}


def guardar(ruta: Path, estado: dict) -> None:
    """Escribe el estado. Si falla (disco lleno, permisos), no rompe: se pierde el freno, no el aviso."""
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_suffix(ruta.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(estado, fh, indent=2, sort_keys=True)
        tmp.replace(ruta)
    except Exception:
        pass
