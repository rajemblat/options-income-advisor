"""Cambia cada cuánto escanea el universo ESTA máquina. Para la convivencia de dos robots.

Por qué existe (validación de la mudanza, agosto 2026):

Durante la validación conviven el robot de la Mac —que opera de verdad— y el del servidor —que
solo mira—. Los dos usan la MISMA cuenta de Schwab, y cada escaneo del universo son unas 300
llamadas a la API (101 símbolos x cotización + historial + cadena de opciones), disparadas cada
minuto. Dos máquinas haciendo eso a la vez duplican la presión sobre el mismo límite de peticiones.

Eso importa por un motivo concreto y verificado: `SchwabBrokerClient.place_order()` NO reintenta.
Si Schwab contesta 429 justo cuando el robot manda una orden, la orden falla y se pierde la
entrada. Y está bien que no reintente —reintentar el POST de una orden puede terminar en orden
duplicada—, así que la solución no es tocar ese camino: es que el robot que solo MIRA no le coma
el cupo al que OPERA.

    python3 deploy/ritmo.py lento     # cada 5 min — para el robot que solo valida
    python3 deploy/ritmo.py normal    # cada 1 min — el ritmo de producción
    python3 deploy/ritmo.py estado

Cada 5 minutos alcanza de sobra para validar: el servidor sigue analizando los mismos símbolos con
los mismos datos, solo que con menos frecuencia. Lo que se valida es QUÉ decide, no cuántas veces
por hora lo decide.

Edita config/settings.yaml como TEXTO (una sola línea) para no perder los comentarios del archivo,
y verifica el resultado releyendo la configuración ya parseada.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROYECTO = Path(__file__).resolve().parents[1]
AJUSTES = PROYECTO / "config" / "settings.yaml"
sys.path.insert(0, str(PROYECTO / "src"))

RITMOS = {"lento": 5, "normal": 1}
_PATRON = re.compile(r"^(\s*robot_scan_interval_minutes\s*:\s*)(\d+)(.*)$", re.M)
_MARCA = re.compile(r"\s*<- ritmo '[^']*' \(deploy/ritmo\.py\)")


def _leer_de_la_config() -> int:
    """El valor EFECTIVO, leído de la configuración ya parseada (no del texto)."""
    from options_advisor.config import load_settings
    return int(load_settings().scheduler.robot_scan_interval_minutes)


def _describir(minutos: int) -> str:
    if minutos <= 1:
        return f"RITMO NORMAL — escanea cada {minutos} min (producción)"
    return f"RITMO LENTO — escanea cada {minutos} min (validación: no le come el cupo de Schwab al robot real)"


def main() -> None:
    accion = (sys.argv[1] if len(sys.argv) > 1 else "estado").lower()

    if accion == "estado":
        print(_describir(_leer_de_la_config()))
        return

    if accion not in RITMOS:
        raise SystemExit(f"No entiendo '{accion}'. Usá: lento | normal | estado")

    minutos = RITMOS[accion]
    texto = AJUSTES.read_text()
    if not _PATRON.search(texto):
        raise SystemExit("No encontré 'robot_scan_interval_minutes' en config/settings.yaml")

    def _reemplazo(m: re.Match) -> str:
        # Se CONSERVA el comentario original de la línea (explica por qué el valor de producción es
        # el que es, con fecha y pedido del usuario) y solo se le agrega una marca al final. Perder
        # ese comentario sería perder media documentación del proyecto, y volver a "normal" no
        # podría restaurarlo. La marca se limpia antes de escribir, así alternar mil veces no la
        # acumula y volver a "normal" deja el archivo byte a byte como estaba.
        resto = _MARCA.sub("", m.group(3))
        marca = "" if accion == "normal" else f"   <- ritmo '{accion}' (deploy/ritmo.py)"
        return f"{m.group(1)}{minutos}{resto}{marca}"

    AJUSTES.write_text(_PATRON.sub(_reemplazo, texto, count=1))

    efectivo = _leer_de_la_config()
    if efectivo != minutos:
        raise SystemExit(f"NO se pudo cambiar el ritmo (quedó en {efectivo}, se pidió {minutos}). "
                         "Revisá config/settings.yaml a mano.")
    print(_describir(efectivo))
    print()
    print("  Para que tome efecto, reiniciá el robot:")
    print("      systemctl --user restart lokshn-robot     (servidor)")
    print("      launchctl kickstart -k gui/$(id -u)/com.robertoajemblat.options-income-advisor.scheduler   (Mac)")


if __name__ == "__main__":
    main()
