from __future__ import annotations

from datetime import date

from options_advisor.alerts.digest import build_premarket_digest_text

TODAY = date(2026, 7, 23)


def test_digest_includes_todays_risk_events_and_excludes_future_ones():
    upcoming_events = [
        {"date": TODAY.isoformat(), "event": "Decisión de tasas de la Fed (FOMC)", "country": "US", "impact": "high"},
        {"date": "2026-08-12", "event": "Publicación de CPI (inflación)", "country": "US", "impact": "high"},
    ]
    text = build_premarket_digest_text(upcoming_events, {}, [], TODAY)
    assert "FOMC" in text
    assert "CPI" not in text


def test_digest_includes_todays_earnings():
    text = build_premarket_digest_text([], {"AAPL": TODAY}, [], TODAY)
    assert "Earnings de AAPL" in text


def test_digest_shows_placeholder_when_no_risk_events():
    text = build_premarket_digest_text([], {}, [], TODAY)
    assert "Sin eventos de riesgo detectados hoy." in text


def test_digest_includes_new_alerts_with_strategy_label_and_score():
    new_alerts = [{"symbol": "AAPL", "strategy_type": "cash_secured_put", "score": 82}]
    text = build_premarket_digest_text([], {}, new_alerts, TODAY)
    assert "AAPL — Cash-Secured Put (score 82)" in text


def test_digest_shows_placeholder_when_no_new_alerts():
    text = build_premarket_digest_text([], {}, [], TODAY)
    assert "Ninguna alerta nueva." in text


def test_digest_starts_with_date_header():
    text = build_premarket_digest_text([], {}, [], TODAY)
    assert text.startswith(f"☀️ Resumen pre-apertura — {TODAY.isoformat()}")


def test_digest_risk_level_icons_reflect_severity():
    upcoming_events = [{"date": TODAY.isoformat(), "event": "Retail Sales", "country": "US", "impact": "low"}]
    text = build_premarket_digest_text(upcoming_events, {}, [], TODAY)
    assert "🟢" in text
