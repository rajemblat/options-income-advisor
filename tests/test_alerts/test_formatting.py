from __future__ import annotations

from options_advisor.alerts.formatting import format_alert_message

_BASE_CONTEXT = {
    "symbol": "AAPL",
    "strategy_type": "cash_secured_put",
    "expiration_date": "2026-08-21",
    "underlying_price": 200.0,
    "legs": [],
    "net_premium": None,
    "max_profit": None,
    "max_loss": None,
    "breakevens": [],
    "probability_of_profit": None,
    "dte": 30,
}


def test_unknown_earnings_shows_generic_caveat():
    text = format_alert_message({**_BASE_CONTEXT, "next_earnings_date": None}, "comentario")
    assert "No se pudo verificar la fecha de earnings" in text


def test_earnings_within_dte_shows_strong_warning():
    text = format_alert_message({**_BASE_CONTEXT, "next_earnings_date": "2026-08-10"}, "comentario")
    assert "CAE DENTRO del vencimiento" in text
    assert "2026-08-10" in text


def test_earnings_after_expiration_shows_reassuring_note():
    text = format_alert_message({**_BASE_CONTEXT, "next_earnings_date": "2026-09-01"}, "comentario")
    assert "Sin earnings antes del vencimiento" in text
    assert "2026-09-01" in text


def test_recent_news_are_listed():
    context = {
        **_BASE_CONTEXT,
        "next_earnings_date": None,
        "recent_news": [{"headline": "AAPL sube tras resultados", "source": "Reuters"}],
    }
    text = format_alert_message(context, "comentario")
    assert "Noticias recientes" in text
    assert "AAPL sube tras resultados" in text
    assert "Reuters" in text


def test_no_news_section_when_empty():
    text = format_alert_message({**_BASE_CONTEXT, "next_earnings_date": None, "recent_news": []}, "comentario")
    assert "Noticias recientes" not in text
