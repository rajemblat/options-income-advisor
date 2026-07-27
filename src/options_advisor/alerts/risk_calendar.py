from __future__ import annotations

from datetime import date, timedelta

RISK_HIGH = "alto"
RISK_MEDIUM = "medio"
RISK_LOW = "bajo"

# Eventos que históricamente mueven el mercado con fuerza incluso cuando Finnhub no marca
# `impact: high` explícitamente (p.ej. algunos meses el CPI aparece como "medium") — el pedido
# original los nombra de forma explícita: FOMC, CPI, empleo (NFP).
_HIGH_IMPACT_KEYWORDS = ("cpi", "consumer price", "nonfarm", "non-farm", "payroll", "employment", "fomc", "fed")


def _classify_macro_event(event: dict) -> str:
    text = (event.get("event") or "").lower()
    if any(keyword in text for keyword in _HIGH_IMPACT_KEYWORDS):
        return RISK_HIGH
    impact = event.get("impact")
    if impact == "high":
        return RISK_HIGH
    if impact == "medium":
        return RISK_MEDIUM
    return RISK_LOW


def build_risk_calendar(
    upcoming_events: list[dict],
    earnings_by_symbol: dict[str, date | None],
    today: date,
    lookahead_days: int = 30,
) -> list[dict]:
    """Combina eventos macro (FRED/Kalshi/Finnhub, vía `upcoming_events`) y earnings por
    símbolo de la watchlist en una única línea de tiempo, ordenada cronológicamente, cada
    uno con un nivel de riesgo estimado (alto/medio/bajo).

    Earnings siempre se clasifica "medio": el impacto real varía mucho por empresa y no
    tenemos movimiento histórico de earnings pasados para calibrarlo por símbolo — es un
    punto de partida conservador, no una medición."""
    horizon = today + timedelta(days=lookahead_days)
    events: list[dict] = []

    for raw_event in upcoming_events:
        try:
            event_date = date.fromisoformat(raw_event["date"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (today <= event_date <= horizon):
            continue
        events.append(
            {
                "date": event_date,
                "kind": "macro",
                "label": raw_event.get("event") or "Evento macro",
                "symbol": None,
                "risk_level": _classify_macro_event(raw_event),
            }
        )

    for symbol, earnings_date in earnings_by_symbol.items():
        if earnings_date is None or not (today <= earnings_date <= horizon):
            continue
        events.append(
            {
                "date": earnings_date,
                "kind": "earnings",
                "label": f"Earnings de {symbol}",
                "symbol": symbol,
                "risk_level": RISK_MEDIUM,
            }
        )

    events.sort(key=lambda e: (e["date"], e["kind"], e["label"]))
    return events


def is_high_risk_event_day(upcoming_events: list[dict], today: date) -> bool:
    """True si HOY cae un evento macro de riesgo alto (CPI/NFP/FOMC, ver
    `_HIGH_IMPACT_KEYWORDS`) — usado para bloquear la generación de candidatos NUEVOS ese día
    (Sección Fed/FRED, pedido 2026-07-26: "bloqueo de días de riesgo CPI/NFP"). Solo bloquea
    sugerencias nuevas, nunca afecta posiciones ya abiertas."""
    for raw_event in upcoming_events:
        try:
            event_date = date.fromisoformat(raw_event["date"])
        except (KeyError, TypeError, ValueError):
            continue
        if event_date == today and _classify_macro_event(raw_event) == RISK_HIGH:
            return True
    return False


# Días de anticipación para el aviso proactivo (Sección Fed/FRED, "alertas proactivas") — 2 y 1
# día antes de un evento de riesgo alto, para que el usuario pueda planificar vencimientos
# nuevos alrededor de esa fecha en vez de enterarse recién el mismo día.
PROACTIVE_WARNING_DAYS_BEFORE = (2, 1)


def build_proactive_risk_warnings(upcoming_events: list[dict], today: date) -> list[dict]:
    """Eventos de riesgo alto que caen exactamente 1 o 2 días después de hoy — cada uno con
    `days_until` para el texto del aviso. No repite el evento del día 2 y del día 1 como el
    mismo aviso: son dos avisos DISTINTOS (uno por cada corrida del digest pre-apertura en la
    que ese evento está a esa distancia), el dedup real lo hace el caller por `title` exacto
    (ver `repo.notification_exists`) para no duplicar si el job corre más de una vez el mismo
    día."""
    warnings = []
    for raw_event in upcoming_events:
        try:
            event_date = date.fromisoformat(raw_event["date"])
        except (KeyError, TypeError, ValueError):
            continue
        days_until = (event_date - today).days
        if days_until in PROACTIVE_WARNING_DAYS_BEFORE and _classify_macro_event(raw_event) == RISK_HIGH:
            warnings.append({"date": event_date, "label": raw_event.get("event") or "Evento macro", "days_until": days_until})
    return warnings
