"""El cortacircuito: un endpoint caído no puede frenar el escaneo entero (usuario 2026-09-14).

El caso real: Schwab devolvía 502 en la cadena de `$SPX`. Como un 502 es del servidor, el cliente
lo reintentaba 4 veces con backoff; con 15 s de timeout por intento, ESE símbolo se comía más de un
minuto, y el escaneo del robot corre cada minuto. El log repetía

    Execution of job "run_robot_scan" skipped: maximum number of running instances reached (1)

minuto tras minuto: el escaneo nunca terminaba la lista y los símbolos sanos nunca se evaluaban.
El robot no abrió nada en toda la rueda por culpa de un solo endpoint.

Lo que se afirma acá:
  · hacen falta varios fallos SEGUIDOS para cortar — un parpadeo se sigue reintentando;
  · una vez cortado, las llamadas siguientes fallan al instante (eso es lo que salva el minuto);
  · el corte es POR ENDPOINT: $SPX caído no puede frenar a AAPL;
  · se cierra solo pasado el enfriamiento, y si sigue roto vuelve a cortar sin gastar otra tanda;
  · un éxito borra la cuenta: fallos aislados no acercan el corte para siempre.
"""

from __future__ import annotations

from options_advisor.broker.cortacircuito import Cortacircuito, EndpointCaido


class _Reloj:
    """Reloj falso: los tests no duermen."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def avanzar(self, segundos):
        self.t += segundos


def _breaker(reloj=None):
    return Cortacircuito(fallos_para_abrir=3, enfriamiento_segundos=300.0,
                         reloj=reloj or _Reloj())


SPX = "/chains?symbol=$SPX"
AAPL = "/chains?symbol=AAPL"


def test_un_parpadeo_no_corta_nada():
    """Uno o dos fallos son un hipo de la red: hay que seguir reintentando."""
    cb = _breaker()
    cb.registrar_fallo(SPX)
    assert cb.esta_abierto(SPX) is False
    cb.registrar_fallo(SPX)
    assert cb.esta_abierto(SPX) is False


def test_tres_fallos_seguidos_cortan():
    cb = _breaker()
    for _ in range(3):
        cb.registrar_fallo(SPX)
    assert cb.esta_abierto(SPX) is True


def test_el_corte_es_por_endpoint():
    """Lo más importante: que $SPX esté caído no puede dejar sin operar a los demás símbolos."""
    cb = _breaker()
    for _ in range(5):
        cb.registrar_fallo(SPX)
    assert cb.esta_abierto(SPX) is True
    assert cb.esta_abierto(AAPL) is False


def test_un_exito_borra_la_cuenta():
    cb = _breaker()
    cb.registrar_fallo(SPX)
    cb.registrar_fallo(SPX)
    cb.registrar_exito(SPX)
    cb.registrar_fallo(SPX)
    assert cb.esta_abierto(SPX) is False, "un éxito en el medio tiene que reiniciar la racha"


def test_se_cierra_solo_pasado_el_enfriamiento():
    reloj = _Reloj()
    cb = _breaker(reloj)
    for _ in range(3):
        cb.registrar_fallo(SPX)
    assert cb.esta_abierto(SPX) is True
    reloj.avanzar(299)
    assert cb.esta_abierto(SPX) is True
    reloj.avanzar(2)
    assert cb.esta_abierto(SPX) is False, "pasado el enfriamiento hay que dejar probar de nuevo"


def test_si_sigue_roto_vuelve_a_cortar_con_UN_solo_fallo():
    """Pasado el enfriamiento se da UN intento de gracia. Si falla, se corta de nuevo enseguida en
    vez de gastar otra tanda entera de reintentos lentos."""
    reloj = _Reloj()
    cb = _breaker(reloj)
    for _ in range(3):
        cb.registrar_fallo(SPX)
    reloj.avanzar(301)
    assert cb.esta_abierto(SPX) is False     # intento de gracia
    cb.registrar_fallo(SPX)
    assert cb.esta_abierto(SPX) is True


def test_si_se_recupera_vuelve_a_la_normalidad():
    reloj = _Reloj()
    cb = _breaker(reloj)
    for _ in range(3):
        cb.registrar_fallo(SPX)
    reloj.avanzar(301)
    cb.esta_abierto(SPX)
    cb.registrar_exito(SPX)
    cb.registrar_fallo(SPX)
    cb.registrar_fallo(SPX)
    assert cb.esta_abierto(SPX) is False, "después de recuperarse hacen falta 3 fallos otra vez"


def test_endpoint_caido_no_es_un_error_http():
    """Tiene que ser un tipo propio: si fuera HTTPStatusError, la política de reintentos lo
    consideraría reintentable y volveríamos a esperar — justo lo que el corte viene a evitar."""
    import httpx
    assert not issubclass(EndpointCaido, httpx.HTTPStatusError)
    assert issubclass(EndpointCaido, RuntimeError)


def test_la_politica_de_reintentos_no_reintenta_un_endpoint_cortado():
    from options_advisor.broker.schwab_client import _is_retryable_http_error
    assert _is_retryable_http_error(EndpointCaido("cortado")) is False


def test_la_clave_separa_por_simbolo():
    """Sin el símbolo en la clave, un $SPX caído cortaría las cadenas de TODOS los símbolos."""
    from options_advisor.broker.schwab_client import _clave_de_endpoint
    spx = _clave_de_endpoint("/chains", {"symbol": "$SPX", "fromDate": "2026-09-21"})
    aapl = _clave_de_endpoint("/chains", {"symbol": "AAPL", "fromDate": "2026-09-21"})
    assert spx != aapl
    # Las variaciones de fecha son la misma consulta al mismo instrumento: misma clave.
    assert spx == _clave_de_endpoint("/chains", {"symbol": "$SPX", "fromDate": "2026-10-01"})
    assert _clave_de_endpoint("/accounts", {}) == "/accounts"
