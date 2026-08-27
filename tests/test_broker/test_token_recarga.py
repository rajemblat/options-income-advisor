"""El robot tiene que ENTERARSE cuando el usuario reconecta la cuenta desde otra terminal.

Contexto real (2026-08-24): el usuario corrio `scripts/schwab_login.py` a las 12:02 con el mercado
abierto. Schwab, al emitir el token nuevo, ANULA el viejo. El robot que estaba corriendo tenia el
viejo cacheado en memoria desde su arranque y `_load_tokens` nunca volvia a mirar el disco, asi que
siguio 22 MINUTOS pidiendo refresh con una llave muerta —~100 errores por minuto— hasta que lo
reiniciamos a mano. Al servidor le paso exactamente lo mismo.

La regla que fijan estos tests: si el archivo de tokens cambia en disco, el proceso lo relee solo.
"""
import json
import time

import pytest

from options_advisor.broker.schwab_auth import SchwabAuth, SchwabAuthError


def _escribir(p, refresh, access="A", edad_del_access=0):
    p.write_text(json.dumps({
        "refresh_token": refresh, "access_token": access, "expires_in": 1800,
        "obtained_at": time.time() - edad_del_access,
        "refresh_token_obtained_at": time.time(),
    }))
    return p


@pytest.fixture
def store(tmp_path):
    return _escribir(tmp_path / "tokens.json", "VIEJO", access="ACCESS_VIEJO")


def test_relee_el_archivo_cuando_alguien_reconecta(store):
    """EL test del 24/08: login nuevo en otra terminal -> el robot usa el token NUEVO sin reiniciar."""
    auth = SchwabAuth("id", "sec", "uri", store)
    assert auth.get_valid_access_token() == "ACCESS_VIEJO"

    # El usuario corre schwab_login.py: el archivo se reescribe con otro token.
    time.sleep(0.01)
    _escribir(store, "NUEVO", access="ACCESS_NUEVO")

    assert auth.get_valid_access_token() == "ACCESS_NUEVO", (
        "Se quedo con el token viejo: es el bug que dejo al robot 22 minutos ciego el 24/08"
    )


def test_no_relee_si_el_archivo_no_cambio(store, monkeypatch):
    """No queremos leer el disco en cada llamada: solo cuando el mtime cambia."""
    auth = SchwabAuth("id", "sec", "uri", store)
    auth.get_valid_access_token()

    lecturas = {"n": 0}
    original = type(store).read_text

    def contando(self, *a, **k):
        lecturas["n"] += 1
        return original(self, *a, **k)

    monkeypatch.setattr(type(store), "read_text", contando)
    for _ in range(5):
        auth.get_valid_access_token()
    assert lecturas["n"] == 0, "Releyo el archivo sin que cambiara"


def test_un_archivo_corrupto_no_tumba_el_trading(store):
    """Si el archivo esta a medio escribir, seguimos con lo que teniamos en memoria en vez de
    reventar: esto corre en el camino critico de mandar ordenes."""
    auth = SchwabAuth("id", "sec", "uri", store)
    assert auth.get_valid_access_token() == "ACCESS_VIEJO"

    time.sleep(0.01)
    store.write_text('{"refresh_token": "a medio escri')   # JSON roto

    assert auth.get_valid_access_token() == "ACCESS_VIEJO"


def test_un_archivo_sin_refresh_token_se_ignora(store):
    """Un JSON valido pero sin refresh_token no sirve; nos quedamos con el bueno."""
    auth = SchwabAuth("id", "sec", "uri", store)
    auth.get_valid_access_token()

    time.sleep(0.01)
    store.write_text(json.dumps({"hola": "mundo"}))

    assert auth.get_valid_access_token() == "ACCESS_VIEJO"


def test_sin_archivo_al_arrancar_avisa_que_hay_que_loguearse(tmp_path):
    auth = SchwabAuth("id", "sec", "uri", tmp_path / "no_existe.json")
    with pytest.raises(SchwabAuthError, match="schwab_login"):
        auth.get_valid_access_token()
