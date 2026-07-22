from __future__ import annotations

from options_advisor.alerts.narrator import build_narration_context, narrate_alert
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
    assert "cash_secured_put" in text


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
