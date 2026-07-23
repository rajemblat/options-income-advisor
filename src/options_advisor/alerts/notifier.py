from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

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
