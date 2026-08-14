from __future__ import annotations

import logging
import os
import smtplib
import subprocess
from email.message import EmailMessage

import httpx

logger = logging.getLogger(__name__)


def send_email(subject: str, body: str) -> bool:
    """Envía un email por SMTP si está configurado (variables de entorno). No-op y devuelve False si falta
    config; nunca lanza (una falla de correo jamás debe tumbar el trading). Pensado para avisar cada
    apertura/cierre REAL (usuario 2026-08-10, roberto@crownsensor.com).

    Config por .env: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, EMAIL_TO (destino), EMAIL_FROM
    (opcional; por defecto = SMTP_USER). Con Gmail: host smtp.gmail.com, user tu-gmail, password un
    'app password' de Google (no la contraseña normal)."""
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


def send_native(message: str, title: str = "Lokshn", subtitle: str = "") -> None:
    """Notificación NATIVA de macOS (osascript) — llega aunque Telegram no esté configurado. Nunca
    lanza. `message` lo arma siempre el código interno (sin interpolar input externo), así que no
    hay inyección de AppleScript posible."""
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
