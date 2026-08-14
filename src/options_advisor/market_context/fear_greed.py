"""Índice de Miedo y Codicia (Fear & Greed Index) — sentimiento del mercado 0-100
(0 = miedo extremo, 100 = codicia extrema). Sin API key. Intenta primero el endpoint público de
CNN (con headers de navegador; a veces bloquea) y, si falla, un espejo libre (feargreedchart.com).
Nunca rompe el caller: None si ninguna fuente responde (usuario 2026-08-07)."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 8.0

# Fuente 1: CNN (el original). Requiere User-Agent de navegador o devuelve 418/403.
_CNN_URL = "https://production.data-manager.cnn.io/index/fearandgreed/graphdata"
_CNN_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cnn.com/markets/fear-and-greed",
    "Origin": "https://www.cnn.com",
}

# Fuente 2 (respaldo): espejo libre del índice de acciones, sin auth, CORS, cache 15 min.
_FGC_URL = "https://feargreedchart.com/api/?action=all"


def _parse_cnn(payload: dict | None) -> dict | None:
    fg = (payload or {}).get("fear_and_greed") or {}
    score = fg.get("score")
    if not isinstance(score, (int, float)):
        return None
    return {"score": round(float(score), 1), "rating": (fg.get("rating") or "").strip(),
            "timestamp": fg.get("timestamp"), "source": "CNN"}


def _parse_fgc(payload: dict | None) -> dict | None:
    """Espejo feargreedchart.com: el valor viene en score.score (o score directo)."""
    if not payload:
        return None
    sc = payload.get("score")
    score = sc.get("score") if isinstance(sc, dict) else sc
    if not isinstance(score, (int, float)):
        return None
    return {"score": round(float(score), 1), "rating": "", "timestamp": payload.get("timestamp"),
            "source": "feargreedchart"}


def rating_label_es(score: float) -> str:
    """Etiqueta en castellano por tramo (mismos cortes que CNN)."""
    if score < 25:
        return "MIEDO EXTREMO"
    if score < 45:
        return "MIEDO"
    if score < 55:
        return "NEUTRAL"
    if score < 75:
        return "CODICIA"
    return "CODICIA EXTREMA"


def get_fear_greed_index() -> dict | None:
    """Índice actual: {'score': 0-100, 'rating', 'timestamp', 'source'} o None si nadie responde."""
    # 1) CNN
    try:
        r = httpx.get(_CNN_URL, headers=_CNN_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        r.raise_for_status()
        out = _parse_cnn(r.json())
        if out:
            return out
    except Exception:
        logger.info("Fear & Greed: CNN no respondió; probando el espejo", exc_info=True)
    # 2) Espejo libre
    try:
        r = httpx.get(_FGC_URL, headers={"Accept": "application/json"}, timeout=_TIMEOUT, follow_redirects=True)
        r.raise_for_status()
        out = _parse_fgc(r.json())
        if out:
            return out
    except Exception:
        logger.warning("Fear & Greed: ninguna fuente disponible; se omite", exc_info=True)
    return None
