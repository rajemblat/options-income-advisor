from __future__ import annotations

from datetime import date

import pytest

from options_advisor.alerts.formatting import (
    assess_dividend_risk,
    assess_liquidity,
    compute_coverage,
    format_alert_message,
    share_requirement_line,
)

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


def test_coverage_sold_put_uses_downside_formula():
    # META a $600, put vendido a $510 → (600-510)/600 = 15% (ejemplo del pedido 2026-07-24).
    legs = [{"side": "sell", "option_type": "put", "strike": 510.0}]
    coverage = compute_coverage(legs, 600.0)
    assert coverage == [{"option_type": "put", "strike": 510.0, "coverage_pct": 0.15}]


def test_coverage_sold_call_uses_upside_formula():
    legs = [{"side": "sell", "option_type": "call", "strike": 660.0}]
    coverage = compute_coverage(legs, 600.0)
    assert coverage == [{"option_type": "call", "strike": 660.0, "coverage_pct": 0.1}]


def test_coverage_ignores_bought_legs():
    legs = [
        {"side": "sell", "option_type": "put", "strike": 510.0},
        {"side": "buy", "option_type": "put", "strike": 500.0},
    ]
    coverage = compute_coverage(legs, 600.0)
    assert len(coverage) == 1
    assert coverage[0]["strike"] == 510.0


def test_coverage_iron_condor_has_both_downside_and_upside():
    legs = [
        {"side": "sell", "option_type": "put", "strike": 510.0},
        {"side": "buy", "option_type": "put", "strike": 500.0},
        {"side": "sell", "option_type": "call", "strike": 660.0},
        {"side": "buy", "option_type": "call", "strike": 670.0},
    ]
    coverage = compute_coverage(legs, 600.0)
    assert [c["option_type"] for c in coverage] == ["put", "call"]
    assert coverage[0]["coverage_pct"] == 0.15
    assert coverage[1]["coverage_pct"] == 0.1


def test_coverage_empty_without_underlying_price():
    legs = [{"side": "sell", "option_type": "put", "strike": 510.0}]
    assert compute_coverage(legs, None) == []


def test_format_alert_message_includes_coverage_line():
    context = {
        **_BASE_CONTEXT,
        "next_earnings_date": None,
        "legs": [{"side": "sell", "option_type": "put", "strike": 170.0, "quantity": 1, "expiration": "2026-08-21", "premium": 2.5}],
    }
    text = format_alert_message(context, "comentario")
    assert "↓ Cobertura: 15.0% (necesita caer hasta $170.00)" in text


def test_assess_liquidity_flags_wide_spread_on_sold_leg():
    legs = [{"side": "sell", "option_type": "put", "strike": 170.0, "bid": 1.00, "ask": 1.40}]  # spread 33% del mid ($1.20)
    warnings = assess_liquidity(legs)
    assert len(warnings) == 1
    assert warnings[0]["strike"] == 170.0
    assert warnings[0]["spread_pct"] == pytest.approx(0.4 / 1.2)


def test_assess_liquidity_ignores_tight_spread():
    legs = [{"side": "sell", "option_type": "put", "strike": 170.0, "bid": 2.45, "ask": 2.55}]  # spread ~4%
    assert assess_liquidity(legs) == []


def test_assess_liquidity_ignores_bought_legs():
    legs = [{"side": "buy", "option_type": "put", "strike": 165.0, "bid": 0.10, "ask": 1.00}]  # spread enorme, pero es compra
    assert assess_liquidity(legs) == []


def test_assess_liquidity_ignores_legs_without_bid_ask():
    legs = [{"side": "sell", "option_type": "put", "strike": 170.0}]
    assert assess_liquidity(legs) == []


def test_format_alert_message_includes_liquidity_warning():
    context = {
        **_BASE_CONTEXT,
        "next_earnings_date": None,
        "legs": [
            {
                "side": "sell", "option_type": "put", "strike": 170.0, "quantity": 1,
                "expiration": "2026-08-21", "premium": 1.2, "bid": 1.00, "ask": 1.40,
            }
        ],
    }
    text = format_alert_message(context, "comentario")
    assert "⚠ Spread ancho en Put $170.00" in text
    assert "bid $1.00 / ask $1.40" in text


def test_assess_dividend_risk_flags_sold_call_expiring_after_ex_date():
    legs = [{"side": "sell", "option_type": "call", "strike": 285.0, "expiration": "2026-08-21"}]
    warnings = assess_dividend_risk(legs, date(2026, 8, 15))
    assert warnings == [{"strike": 285.0, "ex_dividend_date": "2026-08-15"}]


def test_assess_dividend_risk_ignores_call_expiring_before_ex_date():
    legs = [{"side": "sell", "option_type": "call", "strike": 285.0, "expiration": "2026-08-10"}]
    assert assess_dividend_risk(legs, date(2026, 8, 15)) == []


def test_assess_dividend_risk_ignores_sold_puts_and_bought_calls():
    legs = [
        {"side": "sell", "option_type": "put", "strike": 250.0, "expiration": "2026-08-21"},
        {"side": "buy", "option_type": "call", "strike": 290.0, "expiration": "2026-08-21"},
    ]
    assert assess_dividend_risk(legs, date(2026, 8, 15)) == []


def test_assess_dividend_risk_none_without_ex_dividend_date():
    legs = [{"side": "sell", "option_type": "call", "strike": 285.0, "expiration": "2026-08-21"}]
    assert assess_dividend_risk(legs, None) == []


def test_assess_dividend_risk_accepts_iso_string_date():
    legs = [{"side": "sell", "option_type": "call", "strike": 285.0, "expiration": "2026-08-21"}]
    assert assess_dividend_risk(legs, "2026-08-15") == [{"strike": 285.0, "ex_dividend_date": "2026-08-15"}]


def test_format_alert_message_includes_dividend_warning():
    context = {
        **_BASE_CONTEXT,
        "next_earnings_date": None,
        "next_ex_dividend_date": "2026-08-15",
        "legs": [
            {"side": "sell", "option_type": "call", "strike": 285.0, "quantity": 1, "expiration": "2026-08-21", "premium": 1.06}
        ],
    }
    text = format_alert_message(context, "comentario")
    assert "⚠ Ex-dividendo el 2026-08-15" in text
    assert "Call $285.00 vendida" in text


def test_format_alert_message_includes_annualized_return():
    context = {**_BASE_CONTEXT, "next_earnings_date": None, "annualized_return_pct": 24.83}
    text = format_alert_message(context, "comentario")
    assert "↻ Rendimiento anualizado (sobre riesgo máximo): 24.8%" in text


def test_format_alert_message_omits_annualized_return_when_none():
    context = {**_BASE_CONTEXT, "next_earnings_date": None, "annualized_return_pct": None}
    text = format_alert_message(context, "comentario")
    assert "Rendimiento anualizado" not in text


def test_format_alert_message_includes_early_close_projection():
    context = {
        **_BASE_CONTEXT,
        "next_earnings_date": None,
        "early_close_projection": [{"pct": 30, "days": 5}, {"pct": 50, "days": 12}, {"pct": 100, "days": 30}],
    }
    text = format_alert_message(context, "comentario")
    assert "◔ Cierre anticipado (si precio/IV no cambian, solo decaimiento de tiempo): 30% en 5d · 50% en 12d · 100% en 30d" in text


def test_format_alert_message_early_close_projection_shows_not_reached():
    context = {
        **_BASE_CONTEXT,
        "next_earnings_date": None,
        "early_close_projection": [{"pct": 30, "days": None}, {"pct": 50, "days": None}, {"pct": 100, "days": 30}],
    }
    text = format_alert_message(context, "comentario")
    assert "30%: no alcanzado" in text
    assert "50%: no alcanzado" in text


def test_format_alert_message_omits_early_close_when_empty():
    context = {**_BASE_CONTEXT, "next_earnings_date": None, "early_close_projection": []}
    text = format_alert_message(context, "comentario")
    assert "Cierre anticipado" not in text


def test_format_alert_message_flags_capital_at_risk_exceeding_account_size():
    context = {**_BASE_CONTEXT, "next_earnings_date": None, "max_loss": 590_000.0, "capital_available": 50_000.0}
    text = format_alert_message(context, "comentario")
    assert "Esta posición sola arriesga $590,000.00 — 1180% de tu capital configurado ($50,000.00)" in text


def test_format_alert_message_flags_capital_at_risk_above_quarter_of_account():
    context = {**_BASE_CONTEXT, "next_earnings_date": None, "max_loss": 15_000.0, "capital_available": 50_000.0}
    text = format_alert_message(context, "comentario")
    assert "Riesgo real: $15,000.00 (30% de tu capital configurado)" in text


def test_format_alert_message_omits_capital_at_risk_when_small():
    context = {**_BASE_CONTEXT, "next_earnings_date": None, "max_loss": 500.0, "capital_available": 50_000.0}
    text = format_alert_message(context, "comentario")
    assert "Riesgo real" not in text
    assert "arriesga" not in text


def test_format_alert_message_omits_capital_at_risk_for_unbounded_loss():
    context = {**_BASE_CONTEXT, "next_earnings_date": None, "max_loss": float("inf"), "capital_available": 50_000.0}
    text = format_alert_message(context, "comentario")
    assert "Riesgo real" not in text
    assert "arriesga" not in text


def test_format_alert_message_omits_capital_at_risk_without_capital_available():
    context = {**_BASE_CONTEXT, "next_earnings_date": None, "max_loss": 590_000.0, "capital_available": None}
    text = format_alert_message(context, "comentario")
    assert "Riesgo real" not in text
    assert "arriesga" not in text


_DUMMY_LEG = {
    "side": "sell",
    "quantity": 1,
    "option_type": "call",
    "strike": 290.0,
    "expiration": "2026-08-21",
    "premium": 1.31,
}


def test_share_requirement_line_none_for_strategies_without_stock():
    assert share_requirement_line("cash_secured_put", "AAPL", 200.0) is None
    assert share_requirement_line("iron_condor", "AAPL", 200.0) is None


def test_share_requirement_line_includes_price_and_total_cost_for_covered_call():
    line = share_requirement_line("covered_call", "AAPL", 276.23)
    assert line == "Requiere 100 acciones de AAPL a $276.23 (~$27,623.00) en cartera (o asignación previa)."


def test_share_requirement_line_includes_price_and_total_cost_for_collar():
    """Bug real encontrado 2026-07-26: esta línea no se mostraba nunca para Collar, solo para
    Covered Call — pese a que el Collar depende de la misma posición de 100 acciones (el put
    comprado protege esas acciones, no las reemplaza)."""
    line = share_requirement_line("collar", "AAPL", 276.23)
    assert line == "Requiere 100 acciones de AAPL a $276.23 (~$27,623.00) en cartera (o asignación previa)."


def test_share_requirement_line_falls_back_without_underlying_price():
    assert share_requirement_line("covered_call", "AAPL", None) == "Requiere 100 acciones de AAPL en cartera (o asignación previa)."


def test_format_alert_message_shows_share_requirement_for_collar():
    context = {**_BASE_CONTEXT, "strategy_type": "collar", "next_earnings_date": None, "legs": [_DUMMY_LEG], "underlying_price": 276.23}
    text = format_alert_message(context, "comentario")
    assert "Requiere 100 acciones de AAPL a $276.23 (~$27,623.00)" in text


def test_format_alert_message_shows_share_requirement_for_covered_call():
    context = {**_BASE_CONTEXT, "strategy_type": "covered_call", "next_earnings_date": None, "legs": [_DUMMY_LEG], "underlying_price": 276.23}
    text = format_alert_message(context, "comentario")
    assert "Requiere 100 acciones de AAPL a $276.23 (~$27,623.00)" in text


def test_format_alert_message_omits_share_requirement_for_cash_secured_put():
    context = {**_BASE_CONTEXT, "strategy_type": "cash_secured_put", "next_earnings_date": None, "legs": [_DUMMY_LEG]}
    text = format_alert_message(context, "comentario")
    assert "Requiere 100 acciones" not in text
