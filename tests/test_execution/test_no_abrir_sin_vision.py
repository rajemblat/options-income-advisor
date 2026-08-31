"""No se abre un condor real que despues no se pueda cuidar.

El stop de $100 NO es una orden puesta en Schwab: lo dispara el robot, mirando el precio cada
minuto. Si el robot no ve, el stop no existe -- y quedan hasta $835 de riesgo sin proteccion,
creyendo que esta cubierto.

Esta semana el robot quedo ciego tres veces con el mercado abierto:
  27/08  35 min sin DNS en la apertura
  28/08  2845 errores de conexion entre las 09 y las 11
  31/08  token vencido de 12:02 a 13:36

Ninguna de esas veces habia un condor abierto. Fue suerte, no diseno.

Regla del usuario (2026-08-31): "no puede abrir sin stop loss". Estos tests fijan que el robot
verifique que PUEDE ejecutar el stop antes de abrir.
"""

from __future__ import annotations

import time

import pytest

from options_advisor.broker import conectividad
from options_advisor.execution import live_condor_engine as lce


@pytest.fixture(autouse=True)
def _red_limpia():
    """Cada test arranca con la conectividad sana y la deja sana."""
    conectividad.registrar_exito()
    yield
    conectividad.registrar_exito()


@pytest.fixture
def token_ok(monkeypatch):
    monkeypatch.setattr(lce, "__name__", lce.__name__)  # no-op, mantiene el modulo cargado
    import options_advisor.broker.schwab_auth as auth
    monkeypatch.setattr(auth, "read_refresh_token_seconds_left", lambda *a, **k: 5 * 24 * 3600)


def _token(monkeypatch, segundos):
    import options_advisor.broker.schwab_auth as auth
    monkeypatch.setattr(auth, "read_refresh_token_seconds_left", lambda *a, **k: segundos)


def test_con_todo_sano_deja_abrir(token_ok):
    ok, porque = lce.puede_cuidar_la_posicion()
    assert ok is True, porque


def test_no_abre_con_el_token_vencido(monkeypatch):
    """Lo del 31/08: token vencido a las 12:02, robot ciego hasta las 13:36."""
    _token(monkeypatch, -60)
    ok, porque = lce.puede_cuidar_la_posicion()
    assert ok is False
    assert "vencido" in porque


def test_no_abre_si_al_token_le_queda_poco(monkeypatch):
    """Un condor 0DTE hay que poder vigilarlo hasta el cierre. Con 1 hora de token no alcanza."""
    _token(monkeypatch, 3600)
    ok, porque = lce.puede_cuidar_la_posicion()
    assert ok is False
    assert "no alcanza" in porque


def test_con_token_de_sobra_si_abre(monkeypatch):
    _token(monkeypatch, 6 * 3600)
    assert lce.puede_cuidar_la_posicion()[0] is True


def test_no_abre_con_la_red_inestable(monkeypatch):
    """Lo del 28/08: 2845 errores de conexion en dos horas."""
    _token(monkeypatch, 5 * 24 * 3600)
    for _ in range(6):
        conectividad.registrar_fallo_de_red("nodename nor servname provided")
    ok, porque = lce.puede_cuidar_la_posicion()
    assert ok is False
    assert "inestable" in porque or "sin conexión" in porque


def test_un_fallo_suelto_no_frena_nada(monkeypatch):
    """Un timeout aislado no puede dejar al robot sin operar: pasa todos los dias."""
    _token(monkeypatch, 5 * 24 * 3600)
    conectividad.registrar_fallo_de_red("timeout")
    conectividad.registrar_exito()
    assert lce.puede_cuidar_la_posicion()[0] is True


def test_sin_archivo_de_token_no_bloquea(monkeypatch):
    """Si no se puede leer el token no inventamos un motivo para frenar: de eso ya se encarga el
    propio flujo de autenticacion, que va a fallar ruidosamente."""
    _token(monkeypatch, None)
    assert lce.puede_cuidar_la_posicion()[0] is True


def test_no_confunde_el_timestamp_del_ultimo_exito_con_los_segundos(monkeypatch):
    """Candado sobre un bug atrapado al escribir esto.

    `conectividad.estado()` devuelve el TIMESTAMP del ultimo exito, no los segundos transcurridos.
    La primera version comparaba ese timestamp contra 300 segundos: como un timestamp de Unix vale
    ~1.8 mil millones, la condicion daba SIEMPRE verdadera y el condor no habria abierto nunca mas.
    Un freno de seguridad que frena siempre no protege: rompe."""
    _token(monkeypatch, 5 * 24 * 3600)
    conectividad.registrar_exito()          # exito AHORA MISMO
    ok, porque = lce.puede_cuidar_la_posicion()
    assert ok is True, f"Freno sin motivo: {porque}"
