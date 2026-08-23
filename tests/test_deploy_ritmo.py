"""El interruptor de ritmo del escaneo (convivencia de dos robots, agosto 2026).

Durante la validación conviven el robot de la Mac y el del servidor sobre la MISMA cuenta de
Schwab. Cada escaneo del universo son ~300 llamadas (101 símbolos x cotización + historial +
cadena) y dispara cada minuto. `place_order()` NO reintenta: un 429 justo cuando sale una orden la
pierde. Así que el robot que solo mira tiene que bajar el ritmo para no comerle el cupo al que
opera.
"""

from __future__ import annotations

import importlib.util

import pytest

from options_advisor.config import PROJECT_ROOT

_spec = importlib.util.spec_from_file_location("deploy_ritmo", PROJECT_ROOT / "deploy" / "ritmo.py")
ritmo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ritmo)


LINEA = ("  robot_scan_interval_minutes: 1   # escaneo cada 1 min (usuario 2026-08-10) "
         "— re-negocia y detecta más rápido\n")
YAML_EJEMPLO = "scheduler:\n  timezone: America/New_York\n" + LINEA + "  poll_interval_minutes: 30\n"


def _cambiar(texto: str, accion: str) -> str:
    """Replica la sustitución que hace el script, sin tocar el archivo real del proyecto."""
    minutos = ritmo.RITMOS[accion]

    def _rep(m):
        resto = ritmo._MARCA.sub("", m.group(3))
        marca = "" if accion == "normal" else f"   <- ritmo '{accion}' (deploy/ritmo.py)"
        return f"{m.group(1)}{minutos}{resto}{marca}"

    return ritmo._PATRON.sub(_rep, texto, count=1)


def test_lento_pone_5_minutos():
    salida = _cambiar(YAML_EJEMPLO, "lento")
    assert "robot_scan_interval_minutes: 5" in salida


def test_conserva_el_comentario_original():
    """Ese comentario explica POR QUÉ el valor de producción es 1, con fecha y pedido del usuario.
    Perderlo sería perder la razón del parámetro, y volver a 'normal' no podría restaurarlo."""
    salida = _cambiar(YAML_EJEMPLO, "lento")
    assert "usuario 2026-08-10" in salida
    assert "re-negocia y detecta más rápido" in salida


def test_alternar_no_acumula_marcas():
    texto = YAML_EJEMPLO
    for _ in range(5):
        texto = _cambiar(texto, "lento")
    assert texto.count("deploy/ritmo.py") == 1


def test_ida_y_vuelta_deja_el_archivo_identico():
    texto = YAML_EJEMPLO
    for _ in range(3):
        texto = _cambiar(_cambiar(texto, "lento"), "normal")
    assert texto == YAML_EJEMPLO


def test_no_toca_otras_claves():
    salida = _cambiar(YAML_EJEMPLO, "lento")
    assert "poll_interval_minutes: 30" in salida
    assert "timezone: America/New_York" in salida


def test_el_archivo_real_del_proyecto_se_puede_conmutar():
    """Contra el settings.yaml de verdad: si cambiara el formato de esa línea, el interruptor
    dejaría de funcionar justo cuando haga falta."""
    real = (PROJECT_ROOT / "config" / "settings.yaml").read_text()
    lento = _cambiar(real, "lento")
    assert lento != real
    assert _cambiar(lento, "normal") == real


def test_solo_acepta_ritmos_conocidos():
    assert set(ritmo.RITMOS) == {"lento", "normal"}
    with pytest.raises(KeyError):
        ritmo.RITMOS["turbo"]
