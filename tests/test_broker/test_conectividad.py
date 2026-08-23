"""Vigilante de conexión y freno del refresh — el apagón de red del viernes 21/08.

Ese día la Mac se quedó sin resolución de DNS. El robot siguió "corriendo" (309 escaneos), pero
288 terminaron en menos de un segundo porque cada símbolo reventaba al instante con
`httpx.ConnectError: nodename nor servname provided`. Nadie se enteró hasta el domingo. Además,
cada llamada re-entraba a la rama de refresh del token: 31.000 líneas de "refrescando..." para
~15 refrescos de verdad, y un log de 560 MB.

Estos tests cubren las tres piezas del arreglo: contar, frenar, y avisar.
"""

from __future__ import annotations

import sqlite3
import time

import httpx
import pytest

from options_advisor.broker import conectividad
from options_advisor.broker import schwab_auth
from options_advisor.storage import db


@pytest.fixture(autouse=True)
def _estado_limpio():
    """El estado del vigilante es global al proceso (así lo ven todos los hilos del robot).
    Cada test arranca de cero."""
    conectividad._fallos_seguidos = 0
    conectividad._ultimo_exito = None
    conectividad._ultimo_error = ""
    yield
    conectividad._fallos_seguidos = 0
    conectividad._ultimo_exito = None
    conectividad._ultimo_error = ""


def _fallar(n: int) -> None:
    for _ in range(n):
        conectividad.registrar_fallo_de_red(httpx.ConnectError("nodename nor servname provided"))


# --- contar ---------------------------------------------------------------

def test_un_hipo_de_red_no_es_estar_ciego():
    _fallar(3)
    assert conectividad.esta_ciego() is False


def test_una_respuesta_buena_reinicia_el_contador():
    _fallar(conectividad.MIN_FALLOS_SEGUIDOS + 5)
    conectividad.registrar_exito()
    assert conectividad.estado()[0] == 0
    assert conectividad.esta_ciego() is False


def test_muchos_fallos_sin_ningun_exito_previo_es_estar_ciego():
    """Arrancó sin red: nunca entró una respuesta buena en esta corrida."""
    _fallar(conectividad.MIN_FALLOS_SEGUIDOS)
    assert conectividad.esta_ciego() is True


def test_hacen_falta_las_dos_condiciones_juntas():
    """Muchos fallos pero hace un ratito que había conexión → todavía no es un apagón."""
    ahora = 1_000_000.0
    conectividad._ultimo_exito = ahora
    _fallar(conectividad.MIN_FALLOS_SEGUIDOS + 50)
    assert conectividad.esta_ciego(ahora + 10) is False
    assert conectividad.esta_ciego(ahora + conectividad.MIN_SEGUNDOS_SIN_EXITO + 1) is True


def test_un_4xx_no_cuenta_como_fallo_de_red():
    """Schwab contestó: hay conexión. Solo `httpx.TransportError` cuenta como apagón — es lo que
    distingue "la API me rechazó" de "la máquina está aislada"."""
    conectividad.registrar_exito()
    assert conectividad.estado()[0] == 0


# --- frenar ---------------------------------------------------------------

def _auth_con_token_vencido(tmp_path):
    store = tmp_path / "tok.json"
    store.write_text(
        '{"access_token":"A","refresh_token":"R","expires_in":1800,'
        f'"obtained_at":{time.time() - 3600}}}'
    )
    return schwab_auth.SchwabAuth("id", "sec", "uri", store)


def test_el_refresh_que_falla_por_red_frena_los_siguientes(tmp_path, monkeypatch):
    """El arreglo del martilleo: tras un fallo de red, el segundo intento NO vuelve a pegarle a
    la red — corta de una con un mensaje que dice que NO es el token."""
    auth = _auth_con_token_vencido(tmp_path)
    intentos = []

    def _sin_red(*args, **kwargs):
        intentos.append(1)
        raise httpx.ConnectError("nodename nor servname provided")

    monkeypatch.setattr(httpx, "post", _sin_red)

    with pytest.raises(httpx.ConnectError):
        auth.get_valid_access_token()
    assert len(intentos) == 1

    for _ in range(50):                       # los ~100 símbolos del escaneo siguiente
        with pytest.raises(schwab_auth.SchwabAuthError) as err:
            auth.get_valid_access_token()
    assert len(intentos) == 1                 # ni un POST más
    assert "NO es el token vencido" in str(err.value)


def test_pasado_el_enfriamiento_vuelve_a_intentar(tmp_path, monkeypatch):
    auth = _auth_con_token_vencido(tmp_path)
    auth._ultimo_fallo_de_refresh = (time.time() - schwab_auth.REFRESH_FAIL_BACKOFF_SECONDS - 1, "sin red")
    llamadas = []

    class _Respuesta:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"access_token": "A2", "refresh_token": "R", "expires_in": 1800}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: llamadas.append(1) or _Respuesta())
    assert auth.get_valid_access_token() == "A2"
    assert len(llamadas) == 1
    assert auth._ultimo_fallo_de_refresh is None   # se limpia al salir bien


def test_el_token_vencido_de_verdad_sigue_dando_su_mensaje(tmp_path, monkeypatch):
    """El freno nuevo no puede tapar el caso real: si Schwab contesta 400, hay que re-loguearse."""
    auth = _auth_con_token_vencido(tmp_path)

    class _Respuesta:
        status_code = 400
        def raise_for_status(self): pass
        def json(self): return {}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Respuesta())
    with pytest.raises(schwab_auth.SchwabAuthError) as err:
        auth.get_valid_access_token()
    assert "schwab_login.py" in str(err.value)
    assert auth._ultimo_fallo_de_refresh is None   # no fue un fallo de red


# --- avisar ---------------------------------------------------------------

def test_avisa_una_sola_vez_por_apagon(monkeypatch):
    conn = db.connect(":memory:")
    enviados = []
    from options_advisor.alerts import notifier
    monkeypatch.setattr(notifier, "send_email_robot_real", lambda s, b: enviados.append(s) or True)
    monkeypatch.setattr(notifier, "send_native", lambda *a, **k: None)

    _fallar(conectividad.MIN_FALLOS_SEGUIDOS)
    assert conectividad.avisar_si_esta_ciego(conn) is True
    assert conectividad.avisar_si_esta_ciego(conn) is False    # no repite
    assert len(enviados) == 1
    assert "SIN CONEXIÓN" in enviados[0]


def test_cuando_vuelve_la_red_se_rehabilita_el_aviso(monkeypatch):
    conn = db.connect(":memory:")
    enviados = []
    from options_advisor.alerts import notifier
    monkeypatch.setattr(notifier, "send_email_robot_real", lambda s, b: enviados.append(s) or True)
    monkeypatch.setattr(notifier, "send_native", lambda *a, **k: None)

    _fallar(conectividad.MIN_FALLOS_SEGUIDOS)
    conectividad.avisar_si_esta_ciego(conn)
    conectividad.registrar_exito()
    assert conectividad.avisar_si_esta_ciego(conn) is False    # sana: no avisa
    _fallar(conectividad.MIN_FALLOS_SEGUIDOS)                  # segundo apagón
    conectividad._ultimo_exito = time.time() - conectividad.MIN_SEGUNDOS_SIN_EXITO - 1
    assert conectividad.avisar_si_esta_ciego(conn) is True
    assert len(enviados) == 2


def test_el_aviso_nunca_lanza(monkeypatch):
    """Lo llama un job del scheduler: una falla de correo jamás puede tumbar el trading."""
    conn = db.connect(":memory:")
    from options_advisor.alerts import notifier

    def _explota(*a, **k):
        raise RuntimeError("SMTP caído")

    monkeypatch.setattr(notifier, "send_email_robot_real", _explota)
    monkeypatch.setattr(notifier, "send_native", lambda *a, **k: None)
    _fallar(conectividad.MIN_FALLOS_SEGUIDOS)
    assert conectividad.avisar_si_esta_ciego(conn) is False


def test_el_texto_explica_que_no_es_el_token():
    """Fue el malentendido caro: durante un apagón de red el dashboard decía "no autenticado" y la
    reacción natural era salir a re-loguearse a mano, que no arregla nada."""
    _, cuerpo = conectividad._cuerpo_del_aviso(30, 600.0, "nodename nor servname provided")
    assert "NO es el token vencido" in cuerpo
    assert "ping" in cuerpo


def test_el_transporte_del_cliente_reporta_exito_y_fallo(monkeypatch):
    """El enganche está en el transporte de httpx y no en cada método: hay una docena de llamadas
    distintas (quotes, cadenas, cuentas, órdenes) y una nueva se olvidaría de reportar."""
    from options_advisor.broker.schwab_client import _TransporteVigilado

    transporte = _TransporteVigilado()
    pedido = httpx.Request("GET", "https://api.schwabapi.com/marketdata/v1/AAPL/quotes")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request",
                        lambda self, req: (_ for _ in ()).throw(httpx.ConnectError("sin DNS")))
    with pytest.raises(httpx.ConnectError):
        transporte.handle_request(pedido)
    assert conectividad.estado()[0] == 1

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request",
                        lambda self, req: httpx.Response(200, request=req))
    transporte.handle_request(pedido)
    assert conectividad.estado()[0] == 0
