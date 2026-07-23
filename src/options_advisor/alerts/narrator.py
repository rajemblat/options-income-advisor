from __future__ import annotations

import json
import logging
from datetime import date

import anthropic

from options_advisor.alerts import formatting
from options_advisor.config import LlmSettings

logger = logging.getLogger(__name__)

# Sección 6.2: la narración es puramente descriptiva sobre datos ya calculados por el motor
# de reglas (Sección 6.1) — el LLM nunca decide ni pondera, solo redacta en lenguaje simple.
# El bloque con patas, prima, beneficio/pérdida máxima, breakevens y probabilidad de beneficio
# ya se le muestra al usuario armado por `alerts/formatting.py` con datos 100% determinísticos
# (`strategy/payoff.py`); acá el LLM solo escribe el párrafo final de "Comentario".
SYSTEM_PROMPT = """Sos un asistente que redacta el comentario final de una alerta de trading de opciones.

Se te da un JSON con los datos de la estrategia ya armada por un motor de reglas determinístico:
símbolo, estrategia, patas (strike/prima/vencimiento de cada leg), precio del subyacente, las
métricas de riesgo/retorno ya calculadas (prima neta, beneficio máximo, pérdida máxima,
breakevens, probabilidad de beneficio, DTE) y los factores técnicos (IV Rank, RSI, soportes/
resistencias, puntaje de convicción y su desglose). Todos esos datos YA se le muestran al
usuario en un bloque separado antes de tu texto — no los repitas ni los vuelvas a listar.

Tu única tarea es escribir el "Comentario" final: 2-4 frases en español explicando POR QUÉ esta
oportunidad tiene sentido dado el contexto técnico (IV Rank, RSI, niveles, probabilidad de
beneficio). No agregues saludo, título ni la palabra "Comentario" — solo el texto.

Reglas estrictas:
- Nunca inventes cifras que no estén en el JSON.
- Nunca agregues un juicio o recomendación propia que no se derive directamente de los datos dados.
- Nunca sugerís una acción distinta a la estrategia y los strikes que vienen en el JSON.
- Si `iv_rank_source` es "historical_volatility_proxy", mencioná que el IV Rank es una
  aproximación (todavía no hay suficiente historial de IV real).
- Si `payoff_is_estimate` es true, aclará que el beneficio máximo y los breakevens son una
  estimación por modelo (vencimientos combinados), no una fórmula cerrada.
"""


def _fallback_comment(context: dict) -> str:
    return (
        f"Score de convicción {context['conviction_score']}/100. No se generó comentario narrativo "
        "(fallback por error del narrador) — revisar los datos numéricos de arriba y confirmar "
        "manualmente antes de operar."
    )


def narrate_alert(context: dict, llm_settings: LlmSettings, api_key: str | None) -> tuple[str, str]:
    """Devuelve (texto completo de la alerta, fuente). fuente es 'claude' o 'fallback_template'
    según de dónde salió el párrafo de comentario — el resto del texto (patas, prima, P&L) es
    siempre determinístico. Una alerta nunca se pierde por un fallo del LLM: si algo falla, se
    usa un comentario de fallback local (Sección 6.2 / riesgo de fallo de Anthropic API
    documentado en el plan de Fase 1)."""
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY no configurada; usando comentario de fallback para la alerta de %s", context["symbol"])
        comment, source = _fallback_comment(context), "fallback_template"
    else:
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
            comment, source = text, "claude"
        except Exception:
            logger.exception("Fallo al narrar la alerta de %s con Claude; usando comentario de fallback", context["symbol"])
            comment, source = _fallback_comment(context), "fallback_template"

    return formatting.format_alert_message(context, comment), source


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
    underlying_price: float | None = None,
    legs: list[dict] | None = None,
    net_premium: float | None = None,
    max_profit: float | None = None,
    max_loss: float | None = None,
    breakevens: list[float] | None = None,
    probability_of_profit: float | None = None,
    dte: int | None = None,
    payoff_is_estimate: bool = False,
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
        "underlying_price": underlying_price,
        "legs": legs or [],
        "net_premium": net_premium,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "breakevens": breakevens or [],
        "probability_of_profit": probability_of_profit,
        "dte": dte,
        "payoff_is_estimate": payoff_is_estimate,
    }
