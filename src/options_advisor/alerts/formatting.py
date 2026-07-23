from __future__ import annotations

STRATEGY_LABELS = {
    "cash_secured_put": "Cash-Secured Put",
    "short_put_naked": "Short Put (Naked)",
    "covered_call": "Covered Call",
    "bull_put_spread": "Bull Put Spread",
    "iron_condor": "Iron Condor",
    "calendar_put_spread": "Calendar Put Spread",
    "diagonal_put_spread": "Diagonal Put Spread",
}

# Fase 1 no verifica fechas de earnings (es Fase 3) — la alerta siempre lo advierte
# explícitamente en vez de arriesgar una detección de "jugada de earnings" no implementada.
EARNINGS_CAVEAT = "No se verificaron fechas de earnings — confirmá manualmente antes de operar."

SEPARATOR = "──────────"


def strategy_label(strategy_type: str) -> str:
    return STRATEGY_LABELS.get(strategy_type, strategy_type)


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "N/D"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/D"
    return f"{value * 100:.0f}%"


def _leg_line(leg: dict) -> str:
    side_icon = "🔴 Venta" if leg["side"] == "sell" else "🟢 Compra"
    option_label = "Put" if leg["option_type"] == "put" else "Call"
    return (
        f"{side_icon} · 1 {option_label} · Strike ${leg['strike']:.2f} · "
        f"Vence {leg['expiration']} · Prima ${leg['premium']:.2f}"
    )


def format_alert_message(context: dict, comment: str) -> str:
    """Arma el bloque de alerta completo (emojis + patas + métricas de riesgo/retorno +
    comentario). Todo lo numérico viene ya calculado por `strategy/payoff.py` — el único
    texto libre es `comment`, escrito por el narrador (Claude o plantilla de fallback)."""
    label = strategy_label(context["strategy_type"])
    lines = ["🔔 Alerta de Opción", f"⚠️ {EARNINGS_CAVEAT}", f"📌 {context['symbol']} — {label}"]

    if context.get("underlying_price") is not None:
        lines.append(f"💲 Precio actual del subyacente: ${context['underlying_price']:,.2f}")

    lines.append(SEPARATOR)
    legs = context.get("legs") or []
    if legs:
        lines.extend(_leg_line(leg) for leg in legs)
        if context["strategy_type"] == "covered_call":
            lines.append(f"📎 Requiere 100 acciones de {context['symbol']} en cartera (o asignación previa).")
    else:
        lines.append(f"Strikes: {context.get('strikes', {})}")
    lines.append(SEPARATOR)

    net_premium = context.get("net_premium")
    if net_premium is not None:
        kind = "crédito" if net_premium >= 0 else "débito"
        lines.append(f"💵 Prima neta: {_fmt_money(abs(net_premium))} ({kind})")
    else:
        lines.append("💵 Prima neta: N/D")

    lines.append(f"🏆 Beneficio máximo: {_fmt_money(context.get('max_profit'))}")
    lines.append(f"📉 Pérdida máxima (riesgo): {_fmt_money(context.get('max_loss'))}")

    breakevens = context.get("breakevens") or []
    breakevens_str = " / ".join(f"${b:,.2f}" for b in breakevens) if breakevens else "N/D"
    lines.append(f"⚖️ Breakeven(s): {breakevens_str}")

    lines.append(f"📊 Probabilidad de beneficio: {_fmt_pct(context.get('probability_of_profit'))}")

    dte = context.get("dte")
    lines.append(f"⏳ DTE: {dte if dte is not None else 'N/D'} días")

    if context.get("payoff_is_estimate"):
        lines.append(
            "ℹ️ Beneficio máximo, pérdida máxima y breakeven(s) son una estimación por modelo "
            "(vencimientos combinados) — no una fórmula cerrada."
        )

    lines.append(SEPARATOR)
    lines.append(f"💡 Comentario: {comment}")

    return "\n".join(lines)
