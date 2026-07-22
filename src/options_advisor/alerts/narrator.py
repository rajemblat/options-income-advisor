from __future__ import annotations

import json
import logging
from datetime import date

import anthropic

from options_advisor.config import LlmSettings

logger = logging.getLogger(__name__)

# Sección 6.2: la narración es puramente descriptiva sobre datos ya calculados por el motor
# de reglas (Sección 6.1) — el LLM nunca decide ni pondera, solo redacta en lenguaje simple.
SYSTEM_PROMPT = """Sos un asistente que redacta explicaciones breves de alertas de trading de opciones.

Se te da un JSON con los factores ya calculados por un motor de reglas determinístico
(IV Rank, RSI, niveles técnicos, puntaje de convicción y su desglose). Tu única tarea es
narrar esos datos en 2-4 frases claras, en español, en el estilo: "COF muestra IV Rank de 68
(prima cara), soporte técnico fuerte en 195, y RSI en zona neutral. Oportunidad de Cash-Secured
Put en el strike 195."

Reglas estrictas:
- Nunca inventes cifras que no estén en el JSON.
- Nunca agregues un juicio o recomendación propia que no se derive directamente de los datos dados.
- Nunca sugerís una acción distinta a la estrategia y los strikes que vienen en el JSON.
- Si `iv_rank_source` es "historical_volatility_proxy", mencioná que el IV Rank es una
  aproximación (todavía no hay suficiente historial de IV real).
- Siempre cerrá con una nota de que no se verificaron fechas de earnings (limitación conocida
  de esta fase) y que conviene confirmarlo manualmente antes de operar.
"""

FALLBACK_TEMPLATE = (
    "{symbol}: oportunidad de {strategy_type} (score de convicción {score}/100). "
    "IV Rank {iv_rank:.0f} ({iv_rank_source}), RSI {rsi}. Strikes propuestos: {strikes}. "
    "Nota: no se verificaron fechas de earnings ni se generó explicación narrativa (fallback "
    "por error del narrador) — confirmar manualmente antes de operar."
)


def _fallback_text(context: dict) -> str:
    rsi = context.get("rsi")
    return FALLBACK_TEMPLATE.format(
        symbol=context["symbol"],
        strategy_type=context["strategy_type"],
        score=context["conviction_score"],
        iv_rank=context["iv_rank"] or 0.0,
        iv_rank_source=context["iv_rank_source"],
        rsi=f"{rsi:.1f}" if rsi is not None else "N/D",
        strikes=context["strikes"],
    )


def narrate_alert(context: dict, llm_settings: LlmSettings, api_key: str | None) -> tuple[str, str]:
    """Devuelve (texto, fuente). fuente es 'claude' o 'fallback_template'. Una alerta nunca
    se pierde por un fallo del LLM: si algo falla, se usa la plantilla local (Sección 6.2 /
    riesgo de fallo de Anthropic API documentado en el plan de Fase 1)."""
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY no configurada; usando plantilla local para la alerta de %s", context["symbol"])
        return _fallback_text(context), "fallback_template"

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
        logger.exception("Fallo al narrar la alerta de %s con Claude; usando plantilla local", context["symbol"])
        return _fallback_text(context), "fallback_template"


def build_narration_context(
    symbol: str,
    strategy_type: str,
    conviction_score: int,
    breakdown: dict,
    iv_rank: float | None,
    iv_rank_source: str,
    rsi: float | None,
    supports: list[float],
    resistances: list[float],
    strikes: dict,
    expiration_date: date,
) -> dict:
    return {
        "symbol": symbol,
        "strategy_type": strategy_type,
        "conviction_score": conviction_score,
        "scoring_breakdown": breakdown,
        "iv_rank": iv_rank,
        "iv_rank_source": iv_rank_source,
        "rsi": rsi,
        "support_levels": supports,
        "resistance_levels": resistances,
        "strikes": strikes,
        "expiration_date": expiration_date.isoformat(),
    }
