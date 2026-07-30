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
    "roll_closed_leg": "Pata cerrada (roll)",
}

# Cuando no se conoce la fecha de earnings (símbolo sin fixture en modo mock, o modo Schwab —
# todavía no soportado ahí, ver market_context/finnhub_client.py), la alerta lo advierte
# explícitamente en vez de asumir que no hay earnings próximos.
EARNINGS_CAVEAT_UNKNOWN = "No se pudo verificar la fecha de earnings — confirmá manualmente antes de operar."

SEPARATOR = "──────────"


def strategy_label(strategy_type: str) -> str:
    return STRATEGY_LABELS.get(strategy_type, strategy_type)


# Covered Call y Collar dependen de tener (o adquirir) 100 acciones del subyacente antes de
# vender la call — sin esas acciones, la call vendida sería un Call desnudo (riesgo NO acotado),
# no una posición cubierta. Bug real encontrado 2026-07-26: esta advertencia se mostraba SOLO
# para Covered Call, nunca para Collar, pese a que el Collar depende exactamente de la misma
# posición de acciones (el put comprado protege el riesgo de las acciones, no reemplaza tenerlas).
STOCK_REQUIRED_STRATEGIES = {"covered_call", "collar"}


def share_requirement_line(strategy_type: str, symbol: str, underlying_price: float | None) -> str | None:
    """None si la estrategia no depende de tener acciones. El cálculo de beneficio/pérdida
    máxima (`strategy/payoff.py::_pnl_at`) ya incluye el valor de esas 100 acciones — esta línea
    es solo la advertencia explícita en el texto, no afecta ningún número."""
    if strategy_type not in STOCK_REQUIRED_STRATEGIES:
        return None
    if underlying_price is None:
        return f"Requiere 100 acciones de {symbol} en cartera (o asignación previa)."
    return (
        f"Requiere 100 acciones de {symbol} a {_fmt_money(underlying_price)} "
        f"(~{_fmt_money(underlying_price * 100)}) en cartera (o asignación previa)."
    )


def _earnings_line(context: dict) -> str:
    next_earnings = context.get("next_earnings_date")
    if not next_earnings:
        return f"⚠ {EARNINGS_CAVEAT_UNKNOWN}"
    expiration = context.get("expiration_date")
    if expiration and next_earnings <= expiration:
        return f"✖ Earnings el {next_earnings} — CAE DENTRO del vencimiento de esta posición, riesgo de gap."
    return f"✓ Sin earnings antes del vencimiento (próximo: {next_earnings})."


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


def compute_coverage(legs: list[dict], underlying_price: float | None) -> list[dict]:
    """% que el subyacente necesita moverse para llegar al strike de cada pata VENDIDA — el
    punto donde la posición empieza a estar en riesgo real de asignación/pérdida (pedido
    2026-07-24). Put vendido: cobertura a la baja = (precio - strike) / precio. Call vendido:
    cobertura al alza = (strike - precio) / precio — ambas fórmulas dan el % de caída/suba
    necesario, siempre positivo si el strike está OTM (lo normal al vender prima). La mayoría
    de las estrategias del MVP tiene una sola pata vendida; Iron Condor tiene dos (una cobertura
    a la baja y una al alza, independientes)."""
    if not underlying_price:
        return []
    coverage = []
    for leg in legs:
        if leg.get("side") != "sell":
            continue
        strike = leg["strike"]
        if leg["option_type"] == "put":
            pct = (underlying_price - strike) / underlying_price
        else:
            pct = (strike - underlying_price) / underlying_price
        coverage.append({"option_type": leg["option_type"], "strike": strike, "coverage_pct": pct})
    return coverage


def _coverage_lines(coverage: list[dict]) -> list[str]:
    if not coverage:
        return []
    if len(coverage) == 1:
        c = coverage[0]
        glyph = "↓" if c["option_type"] == "put" else "↑"
        verb = "caer" if c["option_type"] == "put" else "subir"
        return [f"{glyph} Cobertura: {c['coverage_pct'] * 100:.1f}% (necesita {verb} hasta ${c['strike']:,.2f})"]
    lines = []
    for c in coverage:
        glyph = "↓" if c["option_type"] == "put" else "↑"
        side_label = "a la baja" if c["option_type"] == "put" else "al alza"
        verb = "caer" if c["option_type"] == "put" else "subir"
        lines.append(f"{glyph} Cobertura {side_label}: {c['coverage_pct'] * 100:.1f}% (necesita {verb} hasta ${c['strike']:,.2f})")
    return lines


LIQUIDITY_SPREAD_WARN_PCT = 0.15  # spread bid/ask > 15% del mid_price: caro entrar/salir a precio de referencia


def assess_liquidity(legs: list[dict], threshold_pct: float = LIQUIDITY_SPREAD_WARN_PCT) -> list[dict]:
    """Spread bid/ask ancho en alguna pata VENDIDA — nadie filtraba esto todavía pese a que
    bid/ask/OI/volumen ya son reales de Schwab (backlog "menor" 2026-07-23: "con plata real
    esto importa"). Solo ADVIERTE, nunca descarta la alerta — el spread real en el momento de
    operar puede ser mejor que el snapshot, y el usuario es quien decide si es operable."""
    warnings = []
    for leg in legs:
        if leg.get("side") != "sell":
            continue
        bid, ask = leg.get("bid"), leg.get("ask")
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            continue
        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid
        if spread_pct > threshold_pct:
            warnings.append(
                {"option_type": leg["option_type"], "strike": leg["strike"], "spread_pct": spread_pct, "bid": bid, "ask": ask}
            )
    return warnings


def assess_dividend_risk(legs: list[dict], next_ex_dividend_date: date | str | None) -> list[dict]:
    """Riesgo de asignación anticipada: una call VENDIDA que sigue viva en o después de la
    próxima fecha ex-dividendo corre riesgo real de que el comprador la ejercite antes para
    capturar el dividendo (si está ITM o cerca) — el vendedor pierde las acciones (y el
    dividendo) antes de lo planeado. Aplica a cualquier estrategia con una call vendida
    (Covered Call, Collar, Iron Condor), no solo Covered Call. `next_ex_dividend_date` viene de
    `broker.get_quote()` (Schwab `fundamental.divExDate`) — None si el símbolo no paga
    dividendo o el broker no lo expone (siempre el caso en modo mock)."""
    if not next_ex_dividend_date:
        return []
    ex_date = next_ex_dividend_date if isinstance(next_ex_dividend_date, date) else date.fromisoformat(next_ex_dividend_date)
    warnings = []
    for leg in legs:
        if leg.get("side") != "sell" or leg.get("option_type") != "call":
            continue
        expiration = date.fromisoformat(leg["expiration"])
        if expiration >= ex_date:
            warnings.append({"strike": leg["strike"], "ex_dividend_date": ex_date.isoformat()})
    return warnings


def _dividend_lines(warnings: list[dict]) -> list[str]:
    return [
        f"⚠ Ex-dividendo el {w['ex_dividend_date']} — antes del vencimiento de la Call ${w['strike']:.2f} vendida, "
        "riesgo de asignación anticipada (perder las acciones y el dividendo antes de lo planeado)."
        for w in warnings
    ]


def _liquidity_lines(warnings: list[dict]) -> list[str]:
    return [
        f"⚠ Spread ancho en {'Put' if w['option_type'] == 'put' else 'Call'} ${w['strike']:.2f}: "
        f"bid ${w['bid']:.2f} / ask ${w['ask']:.2f} ({w['spread_pct'] * 100:.0f}% del precio medio) — "
        "confirmá el precio real antes de operar."
        for w in warnings
    ]


def _annualized_return_line(annualized_return_pct: float | None) -> str | None:
    if annualized_return_pct is None:
        return None
    return f"↻ Rendimiento anualizado (sobre riesgo máximo): {annualized_return_pct:.1f}%"


def _early_close_line(projection: list[dict]) -> str | None:
    if not projection:
        return None
    parts = [f"{p['pct']}% en {p['days']}d" if p["days"] is not None else f"{p['pct']}%: no alcanzado" for p in projection]
    return f"◔ Cierre anticipado (si precio/IV no cambian, solo decaimiento de tiempo): {' · '.join(parts)}"


def _leg_line(leg: dict) -> str:
    side_icon = "↓ Venta" if leg["side"] == "sell" else "↑ Compra"
    option_label = "Put" if leg["option_type"] == "put" else "Call"
    quantity = leg.get("quantity", 1)
    return (
        f"{side_icon} · {quantity} {option_label} · Strike ${leg['strike']:.2f} · "
        f"Vence {leg['expiration']} · Prima ${leg['premium']:.2f}"
    )


def _capital_at_risk_lines(max_loss: float | None, capital_available: float | None) -> list[str]:
    """Riesgo real en dólares de esta posición sola vs. el capital configurado en
    Configuración — sumado 2026-07-26 al agregar índices (RUT/NDX): cash_secured_put/
    short_put_naked sobre un índice pueden arriesgar notional de cientos de miles de dólares,
    y nada en el motor filtra por tamaño de posición, así que se avisa en vez de excluir."""
    if max_loss is None or math.isinf(max_loss) or not capital_available:
        return []
    pct = max_loss / capital_available
    if pct >= 1.0:
        return [
            f"⚠ Esta posición sola arriesga {_fmt_money(max_loss)} — {pct * 100:.0f}% de tu capital "
            f"configurado ({_fmt_money(capital_available)}). Muy por encima de tu tamaño de cuenta."
        ]
    if pct >= 0.25:
        return [f"⚠ Riesgo real: {_fmt_money(max_loss)} ({pct * 100:.0f}% de tu capital configurado) si llega a la pérdida máxima."]
    return []


def format_alert_message(context: dict, comment: str, header: str = "✦ Alerta de Opción") -> str:
    """Arma el bloque de alerta completo (emojis + patas + métricas de riesgo/retorno +
    comentario). Todo lo numérico viene ya calculado por `strategy/payoff.py` — el único
    texto libre es `comment`, escrito por el narrador (Claude o plantilla de fallback).

    `header` es parametrizable (default: candidatos/sugerencias) para reusar este mismo bloque
    en operaciones REALES ya ejecutadas (Pestaña Operaciones, ver alerts/narrator.py::
    narrate_real_trade) sin decirle al usuario "Alerta" sobre algo que ya hizo."""
    label = strategy_label(context["strategy_type"])
    lines = [header, _earnings_line(context), f"✧ {context['symbol']} — {label}"]

    if context.get("underlying_price") is not None:
        lines.append(f"• Precio actual del subyacente: ${context['underlying_price']:,.2f}")

    legs = context.get("legs") or []
    lines.extend(_dividend_lines(assess_dividend_risk(legs, context.get("next_ex_dividend_date"))))
    lines.extend(_liquidity_lines(assess_liquidity(legs)))

    lines.append(SEPARATOR)
    if legs:
        lines.extend(_leg_line(leg) for leg in legs)
        share_line = share_requirement_line(context["strategy_type"], context["symbol"], context.get("underlying_price"))
        if share_line:
            lines.append(f"▸ {share_line}")
    else:
        lines.append(f"Strikes: {context.get('strikes', {})}")
    lines.append(SEPARATOR)

    net_premium = context.get("net_premium")
    if net_premium is not None:
        kind = "crédito" if net_premium >= 0 else "débito"
        lines.append(f"＄ Prima neta: {_fmt_money(abs(net_premium))} ({kind})")
    else:
        lines.append("＄ Prima neta: N/D")

    lines.append(f"▲ Beneficio máximo: {_fmt_money(context.get('max_profit'))}")
    lines.append(f"▽ Pérdida máxima (riesgo): {_fmt_money(context.get('max_loss'))}")
    lines.extend(_capital_at_risk_lines(context.get("max_loss"), context.get("capital_available")))

    breakevens = context.get("breakevens") or []
    breakevens_str = " / ".join(f"${b:,.2f}" for b in breakevens) if breakevens else "N/D"
    lines.append(f"≡ Breakeven(s): {breakevens_str}")

    coverage = compute_coverage(legs, context.get("underlying_price"))
    lines.extend(_coverage_lines(coverage))

    lines.append(f"◎ Probabilidad de beneficio: {_fmt_pct(context.get('probability_of_profit'))}")

    dte = context.get("dte")
    lines.append(f"○ DTE: {dte if dte is not None else 'N/D'} días")

    annualized_line = _annualized_return_line(context.get("annualized_return_pct"))
    if annualized_line:
        lines.append(annualized_line)

    early_close_line = _early_close_line(context.get("early_close_projection") or [])
    if early_close_line:
        lines.append(early_close_line)

    if context.get("payoff_is_estimate"):
        lines.append(
            "⚠ Beneficio máximo, pérdida máxima y breakeven(s) son una estimación por modelo "
            "(vencimientos combinados) — no una fórmula cerrada."
        )

    news = context.get("recent_news") or []
    if news:
        lines.append(SEPARATOR)
        lines.append("▤ Noticias recientes:")
        lines.extend(f"  • {item['headline']} ({item.get('source', 'N/D')})" for item in news)

    lines.append(SEPARATOR)
    lines.append(f"✎ Comentario: {comment}")

    return "\n".join(lines)


def shorten_for_sharing(text: str) -> str:
    """Versión más corta del texto completo para copiar a WhatsApp/Telegram (pedido explícito
    2026-07-28: "muy largo para compartir rápido") — quita la línea de "Cierre anticipado" y la
    sección completa de "Noticias recientes" (el separador que la abre + el encabezado + los
    links), dejando el resto igual (avisos, patas, métricas, comentario). Opera sobre el texto
    YA ARMADO por `format_alert_message` en vez de regenerarlo con flags nuevos — más simple, y
    la tarjeta del dashboard NO usa esta función (sigue mostrando cierre anticipado desde
    `early_close_projection_json` por su cuenta, sin depender del texto copiable)."""
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("◔ Cierre anticipado"):
            i += 1
            continue
        if line.startswith("▤ Noticias recientes:"):
            if result and result[-1] == SEPARATOR:
                result.pop()
            i += 1
            while i < len(lines) and lines[i].startswith("  • "):
                i += 1
            continue
        result.append(line)
        i += 1
    return "\n".join(result)
