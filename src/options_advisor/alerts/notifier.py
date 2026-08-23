from __future__ import annotations

import logging
import os
import smtplib
import subprocess
from email.message import EmailMessage

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FRENO DE MANO: nunca mandar un aviso de verdad desde un test.
#
# Por qué existe (usuario 2026-08-23: "mientras arreglábamos todo, me enviaba estos emails"):
# `dashboard/components.py` hace `load_dotenv(.env)` al importarse. Con que pytest recogiera UN
# test del dashboard, las credenciales SMTP reales quedaban en os.environ para todo el proceso.
# Y los tests de cierre real (tests/test_execution/test_live_engine.py) llaman a
# `close_real_positions()` de verdad — así que cada corrida de `pytest` disparaba emails REALES a
# la casilla del usuario con datos inventados de fixture: "C put 125 vendido a $1.50", "AAL put 11
# a $1.55", "Iron Condor de $200 cerrado manual". Nada de eso salió nunca del robot: salió de
# correr los tests. Se mezclaban con los avisos de verdad, que es justo lo que no puede pasar.
#
# `PYTEST_CURRENT_TEST` la pone pytest sola, en cada test, sin que haya que acordarse de nada.
# `LOKSHN_NO_NOTIFY=1` es el mismo freno a mano, para scripts de diagnóstico o para revisar el
# robot en seco sin llenarle la casilla a nadie.
#
# Esto es la segunda línea de defensa: la primera es el fixture autouse de tests/conftest.py que
# borra las credenciales del entorno. Van las dos a propósito — el correo real es irreversible.
def _avisos_bloqueados() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or os.environ.get("LOKSHN_NO_NOTIFY") == "1"


def send_email(subject: str, body: str) -> bool:
    """Envía un email por SMTP si está configurado (variables de entorno). No-op y devuelve False si falta
    config; nunca lanza (una falla de correo jamás debe tumbar el trading). Pensado para avisar cada
    apertura/cierre REAL (usuario 2026-08-10, roberto@crownsensor.com).

    Config por .env: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, EMAIL_TO (destino), EMAIL_FROM
    (opcional; por defecto = SMTP_USER). Con Gmail: host smtp.gmail.com, user tu-gmail, password un
    'app password' de Google (no la contraseña normal)."""
    if _avisos_bloqueados():
        logger.debug("Email bloqueado (corriendo bajo pytest o LOKSHN_NO_NOTIFY=1): %s", subject)
        return False
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("EMAIL_TO") or user
    frm = os.environ.get("EMAIL_FROM") or user
    if not (host and user and password and to):
        return False
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
    except ValueError:
        port = 587
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = frm
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("Fallo al enviar el email (no afecta el trading)")
        return False



# ---------------------------------------------------------------------------
# Los tres canales de aviso, separados a propósito (usuario 2026-08-23: "es importante que no se
# mezcle"):
#
#   1. OPERACIONES (espejo) — alerts/real_trades.py. Replica en vivo lo que hace el USUARIO en su
#      cuenta de Schwab, haga lo que haga el robot. Sale por la pestaña Operaciones y por Telegram;
#      son las que él copia y reenvía. NO manda email y NO se toca.
#   2. ROBOT REAL — execution/live_engine.py y live_condor_engine.py. Es el ÚNICO canal que manda
#      email, y desde hoy cada mensaje va firmado con la marca de abajo.
#   3. SIMULADOR — no avisa hacia afuera; se ve solo en el dashboard.
#
# La firma existe para que un mensaje suelto se pueda identificar de un vistazo, sin abrirlo entero.
MARCA_ROBOT_REAL = "— Lokshn · ROBOT operando con DINERO REAL —"


def send_email_robot_real(subject: str, body: str) -> bool:
    """Email del canal 2 (robot en dinero real), firmado al pie con la marca del canal."""
    return send_email(subject, f"{body}\n\n{MARCA_ROBOT_REAL}")


def send_native(message: str, title: str = "Lokshn", subtitle: str = "") -> None:
    """Notificación NATIVA de macOS (osascript) — llega aunque Telegram no esté configurado. Nunca
    lanza. `message` lo arma siempre el código interno (sin interpolar input externo), así que no
    hay inyección de AppleScript posible."""
    if _avisos_bloqueados():
        return
    safe = message.replace('"', "'")
    sub = f' subtitle "{subtitle}"' if subtitle else ""
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{safe}" with title "{title}"{sub} sound name "Glass"'],
            timeout=5, check=False,
        )
    except Exception:
        logger.debug("No se pudo enviar la notificación nativa de macOS", exc_info=True)

_TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
_TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_TRUNCATION_SUFFIX = "\n\n[…continúa en el dashboard]"
_TIMEOUT = 10.0


def send_text(text: str) -> None:
    """Envía `text` al chat configurado. Sin parse_mode: `narrative_text` ya viene formateado en
    texto plano (emojis, saltos de línea) por alerts/formatting.py, y HTML/Markdown de Telegram
    rompería con caracteres sin escapar. Nunca lanza: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID
    ausentes o Telegram caído no deben tumbar el resto del pipeline de alertas (Sección 6) — la
    alerta ya quedó persistida en la tabla `alerts` de todas formas."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    if len(text) > _TELEGRAM_MAX_MESSAGE_LENGTH:
        text = text[: _TELEGRAM_MAX_MESSAGE_LENGTH - len(_TRUNCATION_SUFFIX)].rstrip() + _TRUNCATION_SUFFIX

    try:
        response = httpx.post(
            _TELEGRAM_API_URL.format(token=token),
            json={"chat_id": chat_id, "text": text},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
    except Exception:
        logger.exception("Fallo al enviar la alerta a Telegram; la alerta queda igual en la tabla `alerts`")


def notify(symbol: str, strategy_type: str, conviction_score: int, narrative_text: str) -> None:
    """Canal de alertas: la tabla `alerts` (dashboard) siempre queda como registro; además,
    si TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID están configurados en .env, se envía la misma alerta
    al chat de Telegram — sin canal configurado, esta función se comporta como antes (solo log)."""
    logger.info("ALERTA %s | %s | score=%d | %s", symbol, strategy_type, conviction_score, narrative_text)
    send_text(narrative_text)


def notify_real_trade(symbol: str, strategy_type: str, narrative_text: str) -> None:
    """Mismo canal que `notify` (tabla `real_trade_alerts` siempre queda como registro; Telegram
    si está configurado) para una operación YA ejecutada — sin conviction_score, que no existe
    acá (Pestaña Operaciones, pedido 2026-07-25)."""
    logger.info("OPERACIÓN REAL %s | %s | %s", symbol, strategy_type, narrative_text)
    send_text(narrative_text)
