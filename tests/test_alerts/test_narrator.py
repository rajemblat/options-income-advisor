from __future__ import annotations

from options_advisor.alerts.narrator import build_narration_context, build_real_trade_context, narrate_alert, narrate_real_trade
from options_advisor.config import LlmSettings
from datetime import date


def test_narrate_alert_without_api_key_uses_fallback():
    context = build_narration_context(
        symbol="AAPL",
        strategy_type="cash_secured_put",
        conviction_score=76,
        breakdown={"iv_rank_alignment": 30},
        iv_rank=68.0,
        iv_rank_source="implied_volatility",
        rsi=55.0,
        supports=[195.0],
        resistances=[210.0],
        strikes={"short_strike": 195.0},
        expiration_date=date(2026, 8, 15),
    )
    text, source = narrate_alert(context, LlmSettings(model="claude-haiku-4-5-20251001", max_tokens=300), api_key=None)
    assert source == "fallback_template"
    assert "AAPL" in text
    assert "Cash-Secured Put" in text


def test_narrate_alert_never_raises_when_api_call_fails(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("fallo simulado de red")

    monkeypatch.setattr(
        "anthropic.Anthropic", lambda api_key: type("C", (), {"messages": type("M", (), {"create": staticmethod(_boom)})()})()
    )

    context = build_narration_context(
        symbol="AAPL",
        strategy_type="cash_secured_put",
        conviction_score=76,
        breakdown={},
        iv_rank=68.0,
        iv_rank_source="implied_volatility",
        rsi=55.0,
        supports=[195.0],
        resistances=[],
        strikes={"short_strike": 195.0},
        expiration_date=date(2026, 8, 15),
    )
    text, source = narrate_alert(context, LlmSettings(model="claude-haiku-4-5-20251001", max_tokens=300), api_key="fake-key")
    assert source == "fallback_template"
    assert "AAPL" in text


def test_narrate_real_trade_without_api_key_uses_fallback_and_real_trade_header():
    context = build_real_trade_context(
        symbol="TSLA",
        strategy_type="cash_secured_put",
        quantity=1,
        entry_price=5.5,
        strikes={"short_strike": 320.0},
        expiration_date=date(2026, 8, 21),
        underlying_price=330.0,
        legs=[{"side": "sell", "option_type": "put", "strike": 320.0, "expiration": "2026-08-21", "premium": 5.5, "quantity": 1}],
        net_premium=550.0,
        max_profit=550.0,
        max_loss=31450.0,
        breakevens=[314.5],
        probability_of_profit=0.72,
        dte=25,
    )
    text, source = narrate_real_trade(context, LlmSettings(model="claude-haiku-4-5-20251001", max_tokens=300), api_key=None)
    assert source == "fallback_template"
    assert text.startswith("✦ Operación Real Ejecutada")
    assert "TSLA" in text
    assert "Cash-Secured Put" in text
    # el fallback de operaciones reales no debe referenciar conviction_score (no existe acá)
    assert "Score de convicción" not in text


def test_narrate_real_trade_never_raises_when_api_call_fails(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("fallo simulado de red")

    monkeypatch.setattr(
        "anthropic.Anthropic", lambda api_key: type("C", (), {"messages": type("M", (), {"create": staticmethod(_boom)})()})()
    )

    context = build_real_trade_context(
        symbol="TSLA",
        strategy_type="cash_secured_put",
        quantity=1,
        entry_price=5.5,
        strikes={"short_strike": 320.0},
        expiration_date=date(2026, 8, 21),
    )
    text, source = narrate_real_trade(context, LlmSettings(model="claude-haiku-4-5-20251001", max_tokens=300), api_key="fake-key")
    assert source == "fallback_template"
    assert "TSLA" in text


def test_build_real_trade_context_has_no_conviction_score_field():
    context = build_real_trade_context(
        symbol="TSLA",
        strategy_type="cash_secured_put",
        quantity=1,
        entry_price=5.5,
        strikes={"short_strike": 320.0},
        expiration_date=date(2026, 8, 21),
    )
    assert "conviction_score" not in context
    assert "scoring_breakdown" not in context
    assert context["quantity"] == 1
    assert context["entry_price"] == 5.5
