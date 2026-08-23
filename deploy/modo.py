"""Pone el robot en MODO PRUEBA o en MODO REAL, y lo dice en voz alta.

Por qué existe (mudanza al servidor, 2026-08-23, pedido del usuario: "no quiero que se borre de la
Mac hasta comprobar que funciona bien en otro lado"):

Durante la validación conviven dos robots — el de la Mac, que opera de verdad, y el del servidor,
que solo mira. Eso es seguro únicamente si el del servidor NO puede mandar una orden ni un aviso.
Confiar en acordarse de configurar eso a mano es exactamente el tipo de cosa que sale mal una vez y
cuesta plata. Así que se hace con un comando, se verifica leyendo la configuración ya parseada, y
si no quedó como se pidió, falla.

    python deploy/modo.py prueba     # el robot mira pero no toca nada
    python deploy/modo.py real       # el robot opera (lo de siempre)
    python deploy/modo.py estado     # en qué modo está esta máquina

Edita `config/settings.yaml` como TEXTO, cambiando solo dos líneas dentro del bloque
`live_trading:`. Es a propósito: cargar y reescribir el YAML con una librería se llevaría puestos
los cientos de comentarios que explican por qué cada número es el que es, y esos comentarios son
media documentación del proyecto. Después de editar, vuelve a cargar la configuración de verdad y
comprueba el resultado — o sea que el texto se edita a mano pero el resultado se verifica en serio.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROYECTO = Path(__file__).resolve().parents[1]
AJUSTES = PROYECTO / "config" / "settings.yaml"
sys.path.insert(0, str(PROYECTO / "src"))


def _bloque_live_trading(texto: str) -> tuple[int, int]:
    """Índices [inicio, fin) del bloque `live_trading:` dentro del archivo.

    Acotar el reemplazo al bloque importa: `dry_run` y `kill_switch` son nombres genéricos y no
    queremos tocar por accidente una clave con el mismo nombre en otra sección."""
    lineas = texto.splitlines(keepends=True)
    inicio = next((i for i, l in enumerate(lineas) if l.startswith("live_trading:")), None)
    if inicio is None:
        raise SystemExit("No se encontró el bloque 'live_trading:' en config/settings.yaml")
    fin = len(lineas)
    for i in range(inicio + 1, len(lineas)):
        if lineas[i].strip() and not lineas[i][0].isspace() and not lineas[i].startswith("#"):
            fin = i
            break
    return inicio, fin


def _cambiar(clave: str, valor: bool, texto: str) -> str:
    inicio, fin = _bloque_live_trading(texto)
    lineas = texto.splitlines(keepends=True)
    patron = re.compile(rf"^(\s*{clave}\s*:\s*)(true|false)(\s*)(#.*)?$", re.I)
    for i in range(inicio, fin):
        m = patron.match(lineas[i])
        if m:
            nuevo_valor = "true" if valor else "false"
            # Se compensa la diferencia de largo entre "true" (4) y "false" (5) para que el
            # comentario de la derecha no se mueva de columna. Es cosmético, pero settings.yaml se
            # lee a mano seguido y un diff de git donde la única diferencia real queda escondida
            # entre reacomodos de espacios es un diff peor.
            ancho = len(m.group(2)) + len(m.group(3))   # lo que ocupaban valor + espacios juntos
            relleno = " " * max(1, ancho - len(nuevo_valor))
            comentario = m.group(4) or ""
            lineas[i] = f"{m.group(1)}{nuevo_valor}{relleno}{comentario}\n".rstrip() + "\n"
            return "".join(lineas)
    raise SystemExit(f"No se encontró '{clave}' dentro del bloque live_trading de settings.yaml")


def _leer_estado() -> tuple[bool, bool, bool]:
    """(enabled, dry_run, kill_switch) leídos de la configuración YA parseada, no del texto."""
    from options_advisor.config import load_settings
    lt = load_settings().live_trading
    return bool(lt.enabled), bool(lt.dry_run), bool(lt.kill_switch)


def _describir() -> str:
    enabled, dry_run, kill = _leer_estado()
    if dry_run or kill or not enabled:
        frenos = []
        if dry_run:
            frenos.append("dry_run=true")
        if kill:
            frenos.append("kill_switch=true")
        if not enabled:
            frenos.append("enabled=false")
        return (
            "MODO PRUEBA — este robot MIRA pero NO opera.\n"
            f"  Frenos puestos: {', '.join(frenos)}\n"
            "  Escanea, evalúa y registra lo que HARÍA, pero no manda órdenes, no cierra\n"
            "  posiciones, no re-precia y no manda emails de apertura ni de cierre."
        )
    return (
        "MODO REAL — este robot OPERA con plata de verdad.\n"
        "  Sigue habiendo doble confirmación: hace falta dar START del día (require_manual_arm)\n"
        "  y que el guardián apruebe cada orden."
    )


def main() -> None:
    accion = (sys.argv[1] if len(sys.argv) > 1 else "estado").lower()

    if accion == "estado":
        print(_describir())
        return

    if accion == "prueba":
        texto = AJUSTES.read_text()
        texto = _cambiar("dry_run", True, texto)
        texto = _cambiar("kill_switch", True, texto)
        AJUSTES.write_text(texto)
        _, dry_run, kill = _leer_estado()
        # Dos frenos independientes, y se comprueban los dos. Uno solo alcanzaría; tener dos
        # significa que un error de edición no deja la máquina operando sin que nadie lo note.
        if not (dry_run and kill):
            raise SystemExit(
                f"NO se pudo poner en modo prueba (dry_run={dry_run}, kill_switch={kill}).\n"
                "Revisá config/settings.yaml a mano ANTES de arrancar el robot."
            )
        print(_describir())
        return

    if accion == "real":
        texto = AJUSTES.read_text()
        texto = _cambiar("dry_run", False, texto)
        texto = _cambiar("kill_switch", False, texto)
        AJUSTES.write_text(texto)
        enabled, dry_run, kill = _leer_estado()
        if dry_run or kill or not enabled:
            raise SystemExit(
                f"NO se pudo poner en modo real (enabled={enabled}, dry_run={dry_run}, kill_switch={kill}).\n"
                "Revisá config/settings.yaml a mano."
            )
        print(_describir())
        print()
        print("  RECORDATORIO: no puede haber DOS robots en modo real al mismo tiempo.")
        print("  Antes de arrancar este, apagá el otro.")
        return

    raise SystemExit(f"No entiendo '{accion}'. Usá: prueba | real | estado")


if __name__ == "__main__":
    main()
