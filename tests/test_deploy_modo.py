"""El interruptor entre "mirar" y "operar".

Existe por el pedido del usuario del 23/08: mudar el robot a un servidor SIN apagar el de la Mac
hasta comprobar que el nuevo anda bien. Durante esos días conviven dos robots mirando el mismo
mercado, y eso solo es seguro si el del servidor no puede mandar ni una orden ni un aviso.

Lo que se prueba acá es la parte delicada: que la edición del archivo toque exactamente las dos
líneas que tiene que tocar, dentro del bloque correcto, y que no destruya los cientos de
comentarios de settings.yaml —que son media documentación del proyecto— ni al ir ni al volver.
"""

from __future__ import annotations

import importlib.util

import pytest

from options_advisor.config import PROJECT_ROOT

_spec = importlib.util.spec_from_file_location("deploy_modo", PROJECT_ROOT / "deploy" / "modo.py")
modo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(modo)


YAML_EJEMPLO = """\
simulator:
  # Un comentario largo que explica por qué este número es el que es.
  dry_run: false                   # ESTA NO se debe tocar: es de otra sección
  profit_target_pct: 0.5

live_trading:
  # ===== FASE 1: REAL ENCENDIDO =====
  enabled: true                    # REAL ENCENDIDO
  dry_run: false                   # false = MANDA la orden de verdad
  kill_switch: false               # freno de emergencia
  max_orders_per_day: 2            # dos por día

alerts:
  kill_switch: false               # ESTA TAMPOCO: otra sección
"""


def test_solo_toca_el_bloque_live_trading():
    """`dry_run` y `kill_switch` son nombres genéricos que aparecen en otras secciones. Tocar la
    de `simulator` por accidente cambiaría el comportamiento del simulador sin que nadie lo pida."""
    salida = modo._cambiar("dry_run", True, YAML_EJEMPLO)
    lineas = salida.splitlines()
    assert "dry_run: false" in lineas[2], "Cambió el dry_run del simulador"
    assert "dry_run: true" in lineas[8], "No cambió el dry_run de live_trading"


def test_no_toca_la_seccion_de_abajo():
    salida = modo._cambiar("kill_switch", True, YAML_EJEMPLO)
    assert salida.splitlines()[-1].strip().startswith("kill_switch: false"), "Cambió el de alerts"


def test_conserva_todos_los_comentarios():
    salida = modo._cambiar("dry_run", True, modo._cambiar("kill_switch", True, YAML_EJEMPLO))
    for comentario in ("# ESTA NO se debe tocar", "# ===== FASE 1: REAL ENCENDIDO =====",
                       "# false = MANDA la orden de verdad", "# freno de emergencia",
                       "# Un comentario largo que explica por qué este número es el que es."):
        assert comentario in salida, f"Se perdió el comentario: {comentario}"


def test_ida_y_vuelta_deja_el_archivo_identico():
    """Si el interruptor dejara basura acumulada, cada cambio de modo ensuciaría el diff de git y
    con el tiempo nadie sabría qué cambió de verdad."""
    texto = YAML_EJEMPLO
    for _ in range(3):
        texto = modo._cambiar("dry_run", True, texto)
        texto = modo._cambiar("kill_switch", True, texto)
        texto = modo._cambiar("dry_run", False, texto)
        texto = modo._cambiar("kill_switch", False, texto)
    assert texto == YAML_EJEMPLO


def test_no_mueve_los_comentarios_de_columna():
    """Cosmético, pero settings.yaml se lee a mano seguido: un diff donde el cambio real queda
    escondido entre reacomodos de espacios es un diff peor."""
    salida = modo._cambiar("dry_run", True, YAML_EJEMPLO)
    original = [l for l in YAML_EJEMPLO.splitlines() if "MANDA la orden" in l][0]
    nueva = [l for l in salida.splitlines() if "MANDA la orden" in l][0]
    assert original.index("#") == nueva.index("#")


def test_falla_fuerte_si_no_encuentra_la_clave():
    """Mejor un error ruidoso que dejar la máquina en un modo que nadie sabe cuál es."""
    with pytest.raises(SystemExit):
        modo._cambiar("no_existe_esta_clave", True, YAML_EJEMPLO)


def test_falla_fuerte_si_no_hay_bloque_live_trading():
    with pytest.raises(SystemExit):
        modo._cambiar("dry_run", True, "simulator:\n  enabled: true\n")


def test_el_archivo_real_del_proyecto_se_puede_conmutar():
    """Contra el settings.yaml de verdad, no contra el de ejemplo: si el formato del archivo real
    cambiara (comillas, comentario en otra línea, etc.) el interruptor dejaría de funcionar justo
    el día de la mudanza."""
    real = (PROJECT_ROOT / "config" / "settings.yaml").read_text()
    prueba = modo._cambiar("kill_switch", True, modo._cambiar("dry_run", True, real))
    assert prueba != real
    vuelta = modo._cambiar("kill_switch", False, modo._cambiar("dry_run", False, prueba))
    assert vuelta == real
