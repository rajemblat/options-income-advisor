from __future__ import annotations

import math
from datetime import date

STRATEGY_LABELS = {
    "cash_secured_put": "Cash-Secured Put",
    "covered_call": "Covered Call",
    "short_put_naked": "Short Put (Naked)",
    "short_call_naked": "Short Call (Naked)",
    "bull_put_spread": "Bull Put Spread",
    "bear_call_spread": "Bear Call Spread",
    "bull_call_spread": "Bull Call Spread",
    "bear_put_spread": "Bear Put Spread",
    "collar": "Collar",
    "iron_condor": "Iron Condor",
    "calendar_put_spread": "Calendar Put Spread",
    "calendar_call_spread": "Calendar Call Spread",
    "diagonal_put_spread": "Diagonal Put Spread",
    "diagonal_call_spread": "Diagonal Call Spread",
    "call_ratio_backspread": "Call Ratio Backspread",
    "call_ratio_spread": "Call Ratio Spread",
    "put_ratio_spread": "Put Ratio Spread",
    "short_call_condor": "Short Call Condor",
    "short_put_condor": "Short Put Condor",
}

# Cuando no se conoce la fecha de earnings (símbolo sin fixture en modo mock, o modo Schwab —
# todavía no soportado ahí, ver market_context/finnhub_client.py), la alerta lo advierte
# explícitamente en vez de asumir que no hay earnings próximos.
EARNINGS_CAVEAT_UNKNOWN = "No se pudo verificar la fecha de earnings — confirmá manualmente antes de operar."

SEPARATOR = "──────────"


def strategy_label(strategy_type: str) -> str:
    return STRATEGY_LABELS.get(strategy_type, strategy_type)


def _earnings_line(context: dict) -> str:
    next_earnings = context.get("next_earnings_date")
    if not next_earnings:
        return f"⚠️ {EARNINGS_CAVEAT_UNKNOWN}"
    expiration = context.get("expiration_date")
    if expiration and next_earnings <= expiration:
        return f"🚨 Earnings el {next_earnings} — CAE DENTRO del vencimiento de esta posición, riesgo de gap."
    return f"✅ Sin earnings antes del vencimiento (próximo: {next_earnings})."


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "N/D"
    if math.isinf(value):
        return "Ilimitado"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/D"
    return f"{value * 100:.0f}%"


def _leg_line(leg: dict) -> str:
    side_icon = "🔴 Venta" if leg["side"] == "sell" else "🟢 Compra"
    option_label = "Put" if leg["option_type"] == "put" else "Call"
    quantity = leg.get("quantity", 1)
    return (
        f"{side_icon} · {quantity} {option_label} · Strike ${leg['strike']:.2f} · "
        f"Vence {leg['expiration']} · Prima ${leg['premium']:.2f}"
    )


def format_alert_message(context: dict, comment: str) -> str:
    """Arma el bloque de alerta completo (emojis + patas + métricas de riesgo/retorno +
    comentario). Todo lo numérico viene ya calculado por `strategy/payoff.py` — el único
    texto libre es `comment`, escrito por el narrador (Claude o plantilla de fallback)."""
    label = strategy_label(context["strategy_type"])
    lines = ["🔔 Alerta de Opción", _earnings_line(context), f"📍 {context['symbol']} — {label}"]

    if context.get("underlying_price") is not None:
        lines.append(f"🏷️ Precio actual del subyacente: ${context['underlying_price']:,.2f}")

    lines.append(SEPARATOR)
    legs = context.get("legs") or []
    if legs:
        lines.extend(_leg_line(leg) for leg in legs)
        if context["strategy_type"] == "covered_call":
            lines.append(f"🔖 Requiere 100 acciones de {context['symbol']} en cartera (o asignación previa).")
    else:
        lines.append(f"Strikes: {context.get('strikes', {})}")
    lines.append(SEPARATOR)

    net_premium = context.get("net_premium")
    if net_premium is not None:
        kind = "crédito" if net_premium >= 0 else "débito"
        lines.append(f"💰 Prima neta: {_fmt_money(abs(net_premium))} ({kind})")
    else:
        lines.append("💰 Prima neta: N/D")

    lines.append(f"📈 Beneficio máximo: {_fmt_money(context.get('max_profit'))}")
    lines.append(f"📉 Pérdida máxima (riesgo): {_fmt_money(context.get('max_loss'))}")

    breakevens = context.get("breakevens") or []
    breakevens_str = " / ".join(f"${b:,.2f}" for b in breakevens) if breakevens else "N/D"
    lines.append(f"⚖️ Breakeven(s): {breakevens_str}")

    lines.append(f"🎯 Probabilidad de beneficio: {_fmt_pct(context.get('probability_of_profit'))}")

    dte = context.get("dte")
    lines.append(f"⏱ DTE: {dte if dte is not None else 'N/D'} días")

    if context.get("payoff_is_estimate"):
        lines.append(
            "ℹ️ Beneficio máximo, pérdida máxima y breakeven(s) son una estimación por modelo "
            "(vencimientos combinados) — no una fórmula cerrada."
        )

    news = context.get("recent_news") or []
    if news:
        lines.append(SEPARATOR)
        lines.append("📰 Noticias recientes:")
        lines.extend(f"  • {item['headline']} ({item.get('source', 'N/D')})" for item in news)

    lines.append(SEPARATOR)
    lines.append(f"💬 Comentario: {comment}")

    return "\n".join(lines)
