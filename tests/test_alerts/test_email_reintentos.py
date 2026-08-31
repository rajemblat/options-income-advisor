"""Un aviso importante no puede morir en el primer intento fallido.

Historia real: el 30/08 a las 12:07 el vigilante disparo "tu token de Schwab vence en 24 horas".
El envio fallo con `socket.gaierror: nodename nor servname provided` -- un parpadeo de DNS, no un
problema de credenciales. El aviso se perdio. Lo mismo el 31/08 a las 06:07 con el "ultimo
llamado". El lunes el token vencio con el mercado abierto y el usuario se entero mirando el
dashboard, no por mail.

Peor todavia: el vigilante marcaba la bandera "ya avise del nivel 24h" AUNQUE el mail hubiera
fallado, asi que -- corriendo cada hora durante 24 horas -- no volvio a intentarlo ni una vez.

De todos los mails de esa semana solo fallaron 4. Dos de esos cuatro eran estos.
"""

from __future__ import annotations

import sqlite3

import pytest

from options_advisor.alerts import notifier
from options_advisor.broker import token_watch

DIA = 24 * 3600


@pytest.fixture
def smtp_configurado(monkeypatch):
    for k, v in [("SMTP_HOST", "smtp.test"), ("SMTP_USER", "yo@test"),
                 ("SMTP_PASSWORD", "x"), ("EMAIL_TO", "vos@test")]:
        monkeypatch.setenv(k, v)
    # El autouse `_sin_avisos_reales` de conftest borra estas variables; las reponemos a proposito
    # para poder ejercitar el camino de envio, con un SMTP falso que nunca sale a la red.
    monkeypatch.setattr(notifier, "_avisos_bloqueados", lambda: False)
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)


class _SmtpFalso:
    """Falla las primeras `fallas` veces y despues funciona."""

    def __init__(self, fallas):
        self.fallas = fallas
        self.intentos = 0
        self.enviados = []

    def __call__(self, host, port, timeout=None):
        self.intentos += 1
        if self.intentos <= self.fallas:
            raise OSError(8, "nodename nor servname provided, or not known")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg):
        self.enviados.append(msg["Subject"])


def test_un_fallo_de_dns_no_pierde_el_aviso(smtp_configurado, monkeypatch):
    """EL caso del 30/08: falla una vez, entra en el segundo intento."""
    smtp = _SmtpFalso(fallas=1)
    monkeypatch.setattr(notifier.smtplib, "SMTP", smtp)

    assert notifier.send_email("Token vence en 24h", "cuerpo") is True
    assert smtp.enviados == ["Token vence en 24h"]
    assert smtp.intentos == 2


def test_reintenta_tres_veces_antes_de_rendirse(smtp_configurado, monkeypatch):
    smtp = _SmtpFalso(fallas=99)
    monkeypatch.setattr(notifier.smtplib, "SMTP", smtp)

    assert notifier.send_email("asunto", "cuerpo") is False
    assert smtp.intentos == 3, "Se rindio antes de los 3 intentos"


def test_si_entra_a_la_primera_no_reintenta(smtp_configurado, monkeypatch):
    smtp = _SmtpFalso(fallas=0)
    monkeypatch.setattr(notifier.smtplib, "SMTP", smtp)

    assert notifier.send_email("asunto", "cuerpo") is True
    assert smtp.intentos == 1


# ─────────────── el vigilante del token ───────────────

@pytest.fixture
def conn():
    from options_advisor.storage import db
    return db.connect(":memory:")


def _token_por_vencer(tmp_path, horas):
    import json
    import time
    p = tmp_path / "t.json"
    p.write_text(json.dumps({
        "refresh_token": "R", "access_token": "A", "expires_in": 1800,
        "obtained_at": time.time(),
        "refresh_token_obtained_at": time.time() - (7 * DIA - horas * 3600),
    }))
    return p


def test_si_el_mail_falla_NO_marca_el_aviso_como_enviado(conn, tmp_path, monkeypatch):
    """EL bug del 30/08. Si no se marca, la corrida de la hora siguiente vuelve a intentar."""
    store = _token_por_vencer(tmp_path, horas=20)
    monkeypatch.setattr(notifier, "send_email", lambda *a, **k: False)   # el mail no sale
    monkeypatch.setattr(notifier, "send_native", lambda *a, **k: None)

    assert token_watch.check_and_warn(conn, token_store_path=store) is None
    from options_advisor.storage.repository import get_robot_flag
    assert get_robot_flag(conn, token_watch.FLAG_KEY, "") in ("", None), (
        "Marco el aviso como enviado con el mail fallado: asi murio el aviso del 30/08"
    )

    # A la hora siguiente el mail entra, y ahora si se marca.
    monkeypatch.setattr(notifier, "send_email", lambda *a, **k: True)
    assert token_watch.check_and_warn(conn, token_store_path=store) == "24h"
    assert "24h" in (get_robot_flag(conn, token_watch.FLAG_KEY, "") or "")


def test_si_el_mail_entra_no_vuelve_a_avisar(conn, tmp_path, monkeypatch):
    """El anti-repeticion sigue funcionando: un aviso por nivel y por token."""
    store = _token_por_vencer(tmp_path, horas=20)
    monkeypatch.setattr(notifier, "send_email", lambda *a, **k: True)
    monkeypatch.setattr(notifier, "send_native", lambda *a, **k: None)

    assert token_watch.check_and_warn(conn, token_store_path=store) == "24h"
    assert token_watch.check_and_warn(conn, token_store_path=store) is None, "Aviso dos veces del mismo nivel"
