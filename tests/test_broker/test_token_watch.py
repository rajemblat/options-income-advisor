"""Vigilante del vencimiento del token de Schwab (usuario 2026-08-18: "me avisas 24 horas antes").

Contexto: el refresh_token dura ~7 dias y cuando vence el robot queda CIEGO. El 18/08 vencio de
madrugada y no se noto hasta las 11:46; se perdio media rueda con el mercado cayendo fuerte.
"""
import json
import sqlite3
import time

import pytest

from options_advisor.broker import token_watch
from options_advisor.broker.schwab_auth import SchwabAuth, read_refresh_token_seconds_left

DIA = 24 * 3600


@pytest.fixture
def conn():
    from options_advisor.storage import db
    return db.connect(":memory:")


def _store(tmp_path, dias_desde_el_login):
    p = tmp_path / "tokens.json"
    p.write_text(json.dumps({
        "refresh_token": "R", "access_token": "A", "expires_in": 1800,
        "obtained_at": time.time(),
        "refresh_token_obtained_at": time.time() - dias_desde_el_login * DIA,
    }))
    return p


@pytest.mark.parametrize("faltan_horas, esperado", [
    (7 * 24, None), (25, None), (24, "24h"), (23, "24h"), (6, "6h"), (1, "6h"), (-3, "vencido"),
])
def test_pick_level(faltan_horas, esperado):
    assert token_watch.pick_level(faltan_horas * 3600) == esperado


def test_el_reloj_de_7_dias_no_se_reinicia_al_refrescar_el_access_token(tmp_path):
    """El bug de fondo del 18/08: `obtained_at` se pisa en cada refresh (cada 30 min), asi que el
    archivo siempre parecia recien emitido y no habia forma de saber cuanto faltaba de verdad."""
    store = tmp_path / "t.json"
    auth = SchwabAuth("id", "sec", "uri", store)
    auth._store_tokens({"refresh_token": "R1", "access_token": "A", "expires_in": 1800}, from_login=True)
    sello = json.loads(store.read_text())["refresh_token_obtained_at"]

    for i in range(12):                      # 6 horas de robot andando
        auth._store_tokens({"refresh_token": "R1", "access_token": f"A{i}", "expires_in": 1800})

    d = json.loads(store.read_text())
    assert d["refresh_token_obtained_at"] == sello, "el sello del login no se toca en un refresh"
    assert d["obtained_at"] > sello, "pero obtained_at si se actualiza"


def test_un_login_nuevo_si_reinicia_el_reloj(tmp_path):
    store = tmp_path / "t.json"
    auth = SchwabAuth("id", "sec", "uri", store)
    auth._store_tokens({"refresh_token": "R1", "access_token": "A", "expires_in": 1800}, from_login=True)
    viejo = json.loads(store.read_text())["refresh_token_obtained_at"]
    time.sleep(0.01)
    auth._store_tokens({"refresh_token": "R2", "access_token": "B", "expires_in": 1800}, from_login=True)
    assert json.loads(store.read_text())["refresh_token_obtained_at"] > viejo


def test_una_correccion_hecha_por_afuera_le_gana_a_la_memoria(tmp_path):
    """El robot cachea los tokens al arrancar. Si te reconectas desde otra terminal, el archivo es
    mas nuevo que su memoria — y el refresh siguiente no debe pisarlo con el sello viejo."""
    store = tmp_path / "t.json"
    auth = SchwabAuth("id", "sec", "uri", store)
    auth._store_tokens({"refresh_token": "R", "access_token": "A", "expires_in": 1800}, from_login=True)
    d = json.loads(store.read_text())
    correcto = d["refresh_token_obtained_at"] - 14 * 3600
    d["refresh_token_obtained_at"] = correcto
    store.write_text(json.dumps(d))

    auth._store_tokens({"refresh_token": "R", "access_token": "A2", "expires_in": 1800})
    assert json.loads(store.read_text())["refresh_token_obtained_at"] == correcto


def test_archivo_viejo_sin_el_campo_no_rompe(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"refresh_token": "R", "access_token": "A", "expires_in": 1800,
                             "obtained_at": time.time() - 6.5 * DIA}))
    faltan = read_refresh_token_seconds_left(p)
    assert faltan is not None and 11 < faltan / 3600 < 13


def test_sin_archivo_devuelve_none(tmp_path):
    assert read_refresh_token_seconds_left(tmp_path / "no_existe.json") is None


def test_avisa_una_sola_vez_por_nivel_y_escala(conn, tmp_path, monkeypatch):
    """No spamea: un aviso por nivel. Pero si el tiempo sigue corriendo, escala 24h -> 6h -> vencido."""
    enviados = []
    monkeypatch.setattr(token_watch.notifier, "send_email", lambda s, b: enviados.append(s) or True)
    monkeypatch.setattr(token_watch.notifier, "send_native", lambda *a, **k: None)

    p = _store(tmp_path, 6.5)                      # quedan ~12 h
    assert token_watch.check_and_warn(conn, token_store_path=p) == "24h"
    assert token_watch.check_and_warn(conn, token_store_path=p) is None
    assert token_watch.check_and_warn(conn, token_store_path=p) is None

    p = _store(tmp_path, 6.9)                      # quedan ~2.4 h
    assert token_watch.check_and_warn(conn, token_store_path=p) == "6h"
    assert token_watch.check_and_warn(conn, token_store_path=p) is None

    p = _store(tmp_path, 7.1)                      # ya vencio
    assert token_watch.check_and_warn(conn, token_store_path=p) == "vencido"
    assert token_watch.check_and_warn(conn, token_store_path=p) is None
    assert len(enviados) == 3


def test_al_reconectar_el_ciclo_arranca_limpio(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(token_watch.notifier, "send_email", lambda s, b: True)
    monkeypatch.setattr(token_watch.notifier, "send_native", lambda *a, **k: None)
    assert token_watch.check_and_warn(conn, token_store_path=_store(tmp_path, 6.5)) == "24h"
    # Te reconectas: token nuevo, vencimiento a 7 dias vista → nada que avisar.
    assert token_watch.check_and_warn(conn, token_store_path=_store(tmp_path, 0)) is None
    # Pasa la semana y el token NUEVO se acerca a su vencimiento → vuelve a avisar.
    # Ojo: 6.6 y no 6.5, porque el anti-repeticion identifica al token por su instante de
    # vencimiento redondeado al minuto; con 6.5 el archivo caeria en el mismo minuto que el
    # primero del test y quedaria marcado como "ya avisado", que es justo lo que debe hacer.
    assert token_watch.check_and_warn(conn, token_store_path=_store(tmp_path, 6.6)) == "24h"


def test_nunca_lanza_aunque_falle_el_correo(conn, tmp_path, monkeypatch):
    """Una falla de correo jamas debe tumbar el trading: el job corre dentro del scheduler."""
    def _explota(*a, **k):
        raise RuntimeError("SMTP caido")
    monkeypatch.setattr(token_watch.notifier, "send_email", _explota)
    monkeypatch.setattr(token_watch.notifier, "send_native", lambda *a, **k: None)
    assert token_watch.check_and_warn(conn, token_store_path=_store(tmp_path, 6.5)) is None
