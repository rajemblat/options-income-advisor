"""Guardia de regresión: la suite NUNCA puede mandarle un aviso de verdad al usuario.

Historia (2026-08-23): el usuario recibió, mezclados con los avisos reales de su robot, tres
emails con datos inventados — "Lokshn CERRÓ C put 125 a $1.50", "AAL put 11 a $1.55" y un Iron
Condor de $200 cerrado a mano. Ninguna de esas operaciones existe en la base. Los mandó `pytest`:
`dashboard/components.py` hace load_dotenv(.env) al importarse, las credenciales SMTP quedaban en
el entorno de todo el proceso, y los tests de cierre real llaman a `notifier.send_email()` de
verdad.

Hay dos frenos y este archivo prueba los dos por separado, porque cada uno tapa un agujero
distinto: el fixture de conftest tapa "el .env se coló en el entorno", y el freno del notifier
tapa "alguien corre pytest sin ese conftest o con las variables puestas a mano".
"""

from __future__ import annotations

import os
import smtplib
import subprocess
from pathlib import Path

from options_advisor.alerts import notifier

# Duplicado a propósito (tests/ no es un paquete importable, así que no se puede hacer
# `from tests.conftest import ...`). El test de abajo verifica que las dos listas no se separen.
_VARIABLES_DE_AVISO = (
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO", "EMAIL_FROM",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
)

_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"


def test_la_lista_de_variables_sigue_igual_a_la_de_conftest():
    """Si alguien agrega un canal nuevo en conftest y se olvida acá, este test lo canta."""
    fuente = _CONFTEST.read_text()
    faltan = [v for v in _VARIABLES_DE_AVISO if f'"{v}"' not in fuente]
    assert faltan == [], f"conftest.py ya no limpia estas variables: {faltan}"


def test_el_entorno_del_test_no_tiene_credenciales_de_aviso():
    """Freno 1: el fixture autouse de conftest limpia el entorno en CADA test."""
    presentes = [v for v in _VARIABLES_DE_AVISO if os.environ.get(v)]
    assert presentes == [], (
        f"Estas variables llegaron al test con valor real: {presentes}. "
        "Con eso puesto, cualquier test que ejecute un cierre real le manda un correo de verdad al usuario."
    )


def test_send_email_no_abre_smtp_aunque_haya_credenciales(monkeypatch):
    """Freno 2: aun con SMTP configurado a mano, bajo pytest no se abre ninguna conexión."""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "alguien@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secreto")
    monkeypatch.setenv("EMAIL_TO", "alguien@example.com")

    def _explota(*args, **kwargs):
        raise AssertionError("Se intentó abrir una conexión SMTP real desde un test")

    monkeypatch.setattr(smtplib, "SMTP", _explota)
    assert notifier.send_email("asunto de prueba", "cuerpo") is False


def test_send_native_no_lanza_osascript(monkeypatch):
    """Las notificaciones de macOS tampoco: nadie quiere 1000 pop-ups corriendo la suite."""
    def _explota(*args, **kwargs):
        raise AssertionError("Se intentó lanzar osascript desde un test")

    monkeypatch.setattr(subprocess, "run", _explota)
    notifier.send_native("mensaje")  # no debe hacer nada ni lanzar


def test_el_freno_se_puede_activar_fuera_de_pytest(monkeypatch):
    """LOKSHN_NO_NOTIFY=1 es el mismo freno para scripts de diagnóstico corridos a mano."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert notifier._avisos_bloqueados() is False
    monkeypatch.setenv("LOKSHN_NO_NOTIFY", "1")
    assert notifier._avisos_bloqueados() is True


def test_sin_el_freno_y_sin_credenciales_sigue_siendo_no_op(monkeypatch):
    """Sanidad: sin freno y sin config, send_email devuelve False sin tocar la red (comportamiento
    de siempre — el freno nuevo no cambió este camino)."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    for nombre in _VARIABLES_DE_AVISO:
        monkeypatch.delenv(nombre, raising=False)

    def _explota(*args, **kwargs):
        raise AssertionError("Se intentó abrir SMTP sin credenciales configuradas")

    monkeypatch.setattr(smtplib, "SMTP", _explota)
    assert notifier.send_email("asunto", "cuerpo") is False


# ---------------------------------------------------------------------------
# Separación de canales (usuario 2026-08-23: "es importante que no se mezcle").

def test_email_del_robot_real_va_firmado(monkeypatch):
    """Todo email del canal 2 (robot en dinero real) lleva la marca del canal al pie."""
    capturado = {}
    monkeypatch.setattr(notifier, "send_email",
                        lambda s, b: capturado.update(asunto=s, cuerpo=b) or True)
    assert notifier.send_email_robot_real("asunto", "cuerpo del aviso") is True
    assert capturado["asunto"] == "asunto"
    assert capturado["cuerpo"].startswith("cuerpo del aviso")
    assert capturado["cuerpo"].rstrip().endswith(notifier.MARCA_ROBOT_REAL)


def test_los_motores_reales_usan_el_canal_firmado():
    """Guardia de fuente: si mañana alguien agrega un aviso del robot real llamando a send_email()
    pelado, el mensaje saldría sin firma y se confundiría con los de Operaciones. Este test lo caza."""
    raiz = Path(__file__).resolve().parents[2] / "src" / "options_advisor" / "execution"
    for archivo in ("live_engine.py", "live_condor_engine.py"):
        fuente = (raiz / archivo).read_text()
        crudos = [ln.strip() for ln in fuente.splitlines()
                  if "notifier.send_email(" in ln and "send_email_robot_real" not in ln]
        assert crudos == [], (
            f"{archivo} manda email sin firmar el canal: {crudos}. "
            "Usá notifier.send_email_robot_real() para que el aviso quede identificado.")


def test_el_canal_operaciones_no_manda_email():
    """El espejo de la cuenta del usuario ('Operaciones') va por Telegram y la pestaña del
    dashboard, NUNCA por email. Él las copia y reenvía; mezclarlas con los avisos del robot fue lo
    que pidió expresamente que no pasara."""
    raiz = Path(__file__).resolve().parents[2] / "src" / "options_advisor" / "alerts"
    fuente = (raiz / "real_trades.py").read_text()
    assert "send_email" not in fuente
