from __future__ import annotations

from datetime import date

from options_advisor.alerts.formatting import strategy_label
from options_advisor.alerts.risk_calendar import build_risk_calendar

_RISK_LEVEL_ICONS = {"alto": "🔴", "medio": "🟡", "bajo": "🟢"}


def build_premarket_digest_text(
    upcoming_events: list[dict],
    earnings_by_symbol: dict[str, date | None],
    new_alerts: list[dict],
    today: date,
) -> str:
    """Resumen pre-apertura: eventos de riesgo de HOY (FOMC/CPI/empleo/earnings de la
    watchlist, mismo cálculo que la página Eventos de riesgo vía `build_risk_calendar` con
    `lookahead_days=0`) + alertas de estrategia nuevas generadas en la corrida que dispara este
    digest. `new_alerts` trae el mismo shape que devuelve `alerts/engine.py::process_symbol_alerts`
    (symbol, strategy_type, score)."""
    lines = [f"☀️ Resumen pre-apertura — {today.isoformat()}", ""]

    risk_events = build_risk_calendar(upcoming_events, earnings_by_symbol, today, lookahead_days=0)
    lines.append("📅 Eventos de riesgo hoy:")
    if risk_events:
        lines.extend(f"  {_RISK_LEVEL_ICONS.get(e['risk_level'], '⚪')} {e['label']}" for e in risk_events)
    else:
        lines.append("  Sin eventos de riesgo detectados hoy.")

    lines.append("")
    lines.append("🔔 Alertas nuevas de esta corrida:")
    if new_alerts:
        lines.extend(f"  • {a['symbol']} — {strategy_label(a['strategy_type'])} (score {a['score']})" for a in new_alerts)
    else:
        lines.append("  Ninguna alerta nueva.")

    return "\n".join(lines)
