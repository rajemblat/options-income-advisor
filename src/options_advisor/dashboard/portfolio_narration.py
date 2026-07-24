from __future__ import annotations

import json
import logging

import anthropic

from options_advisor.config import LlmSettings

logger = logging.getLogger(__name__)

# Mismo principio que alerts/narrator.py (Sección 6.2): el LLM nunca calcula ni decide qué es
# "concentrado" o "riesgoso" — eso ya lo resuelve portfolio_analysis.py con reglas fijas
# (compute_concentration, compute_earnings_clusters). Acá solo redacta en lenguaje simple lo
# que esos números ya determinaron.
SYSTEM_PROMPT = """Sos un asistente que redacta un resumen de exposición de un portafolio real de opciones.

Se te da un JSON con: valor total del portafolio, P&L no realizado, concentración por símbolo
subyacente (% del valor total, ya calculado y ordenado de mayor a menor), y clusters de
earnings (grupos de símbolos cuya próxima fecha de earnings cae dentro de una ventana de días
entre sí, ya identificados). Todos esos números YA se calcularon con reglas fijas, no los
inventes ni los recalcules.

Tu única tarea es escribir 3-5 frases en español explicando en lenguaje simple qué implica esa
concentración y esos clusters de earnings para el riesgo del portafolio — no des recomendación
de comprar/vender nada puntual, solo describí la exposición tal como está. No agregues saludo,
título, ni la palabra "Resumen" — solo el texto.

Reglas estrictas:
- Nunca inventes cifras que no estén en el JSON.
- Nunca sugerís una acción de trading concreta (comprar, vender, cerrar una posición).
- Si no hay clusters de earnings, decilo explícitamente en vez de omitirlo.
- Si la concentración está reducida (ningún símbolo por encima de ~20% del total), decilo — no
  hace falta alarmar donde no hay alarma.
"""


def _fallback_summary(context: dict) -> str:
    top = context["concentration"][0] if context["concentration"] else None
    top_line = f"{top['symbol']} concentra el {top['pct']:.1f}% del portafolio." if top else "Sin posiciones con valor."
    clusters = context.get("earnings_clusters") or []
    cluster_line = (
        f"{len(clusters)} cluster(s) de earnings simultáneos detectado(s)." if clusters else "Sin clusters de earnings detectados."
    )
    return f"No se generó resumen narrativo (fallback por error del narrador). {top_line} {cluster_line} Revisar los datos de arriba."


def narrate_portfolio(context: dict, llm_settings: LlmSettings, api_key: str | None) -> tuple[str, str]:
    """Devuelve (texto del resumen, fuente) — mismo patrón de fallback que alerts/narrator.py:
    un fallo del LLM nunca deja al usuario sin nada, cae a un resumen local armado con los
    mismos números determinísticos."""
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY no configurada; usando resumen de fallback para el portafolio")
        return _fallback_summary(context), "fallback_template"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=llm_settings.model,
            max_tokens=llm_settings.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(context, default=str, ensure_ascii=False)}],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise ValueError("Respuesta vacía de Claude")
        return text, "claude"
    except Exception:
        logger.exception("Fallo al narrar el resumen de portafolio con Claude; usando fallback")
        return _fallback_summary(context), "fallback_template"
