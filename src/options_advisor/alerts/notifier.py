from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def notify(symbol: str, strategy_type: str, conviction_score: int, narrative_text: str) -> None:
    """Canal de alertas de Fase 1: dashboard web (Streamlit lee directamente la tabla `alerts`).
    Esta función solo deja constancia en el log del proceso; agregar un canal push (Telegram,
    email) en una fase futura implica sumar una implementación acá, sin tocar alerts/engine.py."""
    logger.info("ALERTA %s | %s | score=%d | %s", symbol, strategy_type, conviction_score, narrative_text)
