from __future__ import annotations

import json

from options_advisor.dashboard.scanner_table import build_scanner_rows


def _candidate_row(**overrides) -> dict:
    defaults = dict(
        symbol="TSLA",
        strategy_type="cash_secured_put",
        expiration_date="2026-08-21",
        underlying_price=330.0,
        delta=-0.25,
        net_premium=550.0,
        max_profit=550.0,
        max_loss=31450.0,
        breakevens_json=json.dumps([314.5]),
        legs_json=json.dumps([{"side": "sell", "option_type": "put", "strike": 320.0, "bid": 5.4, "volume": 50, "open_interest": 500}]),
        probability_of_profit=0.72,
        dte=25,
        annualized_return_pct=25.5,
        iv_rank=68.0,
    )
    defaults.update(overrides)
    return defaults


def test_build_scanner_rows_basic_fields():
    rows = build_scanner_rows([_candidate_row()])
    assert len(rows) == 1
    row = rows[0]
    assert row["Symbol"] == "TSLA"
    assert row["Estrategia"] == "Cash-Secured Put"
    assert row["Price"] == 330.0
    assert row["Strike"] == 320.0
    assert row["Bid"] == 5.4
    assert row["Breakeven"] == 314.5
    assert row["Volume"] == 50
    assert row["Open Interest"] == 500
    assert row["IV Rank"] == 68.0
    assert row["Delta"] == -0.25
    assert row["Rendimiento Anualizado (%)"] == 25.5
    assert row["DTE"] == 25


def test_build_scanner_rows_moneyness_and_be_for_put():
    """Put OTM (strike 320 < precio 330): moneyness positiva (necesita caer 3.03% para llegar
    al strike). Breakeven 314.5 más lejos: %BE mayor (5.0%)."""
    rows = build_scanner_rows([_candidate_row()])
    row = rows[0]
    assert row["Moneyness (%)"] == _rounded((330.0 - 320.0) / 330.0 * 100)
    assert row["%BE"] == _rounded((330.0 - 314.5) / 330.0 * 100)
    assert row["%BE"] > row["Moneyness (%)"]  # breakeven siempre más lejos que el strike (a favor del vendedor)


def _rounded(value):
    return round(value, 2)


def test_build_scanner_rows_moneyness_for_call():
    row = build_scanner_rows(
        [
            _candidate_row(
                strategy_type="covered_call",
                legs_json=json.dumps([{"side": "sell", "option_type": "call", "strike": 340.0, "bid": 3.0, "volume": 20, "open_interest": 200}]),
                breakevens_json=json.dumps([343.0]),
            )
        ]
    )[0]
    # Call OTM (strike 340 > precio 330): necesita subir 3.03% para llegar al strike
    assert row["Moneyness (%)"] == _rounded((340.0 - 330.0) / 330.0 * 100)


def test_build_scanner_rows_return_pct_uses_max_loss_not_annualized():
    row = build_scanner_rows([_candidate_row(net_premium=550.0, max_loss=31450.0)])[0]
    assert row["Return (%)"] == _rounded(550.0 / 31450.0 * 100)


def test_build_scanner_rows_pop_converted_to_percent():
    row = build_scanner_rows([_candidate_row(probability_of_profit=0.72)])[0]
    assert row["POP (%)"] == 72.0


def test_build_scanner_rows_skips_candidate_without_legs():
    rows = build_scanner_rows([_candidate_row(legs_json=None)])
    assert rows == []


def test_build_scanner_rows_skips_candidate_without_underlying_price():
    rows = build_scanner_rows([_candidate_row(underlying_price=None)])
    assert rows == []


def test_build_scanner_rows_handles_missing_max_loss_for_return():
    row = build_scanner_rows([_candidate_row(max_loss=None)])[0]
    assert row["Return (%)"] is None


def test_build_scanner_rows_handles_missing_breakeven():
    row = build_scanner_rows([_candidate_row(breakevens_json=json.dumps([]))])[0]
    assert row["Breakeven"] is None
    assert row["%BE"] is None


def test_build_scanner_rows_multiple_candidates():
    rows = build_scanner_rows([_candidate_row(symbol="TSLA"), _candidate_row(symbol="AAPL")])
    assert [r["Symbol"] for r in rows] == ["TSLA", "AAPL"]


# --- Probabilidad OTM / Instrumento (Sección 'Pestaña Screener', pedido 2026-07-27) ---


def test_build_scanner_rows_probability_otm_none_without_risk_free_rate():
    row = build_scanner_rows([_candidate_row()])[0]
    assert row["Probabilidad OTM (%)"] is None


def test_build_scanner_rows_probability_otm_computed_when_risk_free_rate_given():
    """Leg trae implied_volatility=0.32280 (ver default de OptionContract en el resto de los
    tests de este archivo)."""
    row = build_scanner_rows(
        [
            _candidate_row(
                legs_json=json.dumps(
                    [{"side": "sell", "option_type": "put", "strike": 320.0, "bid": 5.4, "volume": 50, "open_interest": 500, "implied_volatility": 0.30}]
                )
            )
        ],
        risk_free_rate=0.045,
    )[0]
    assert row["Probabilidad OTM (%)"] is not None
    assert 0.0 <= row["Probabilidad OTM (%)"] <= 100.0
    # Probabilidad OTM (contra el strike) siempre <= POP (contra el breakeven) para un put vendido.
    assert row["Probabilidad OTM (%)"] <= row["POP (%)"]


def test_build_scanner_rows_probability_otm_none_without_implied_volatility():
    row = build_scanner_rows(
        [_candidate_row(legs_json=json.dumps([{"side": "sell", "option_type": "put", "strike": 320.0, "bid": 5.4}]))],
        risk_free_rate=0.045,
    )[0]
    assert row["Probabilidad OTM (%)"] is None


def test_build_scanner_rows_instrumento_none_without_instrument_types():
    row = build_scanner_rows([_candidate_row(symbol="TSLA")])[0]
    assert row["Instrumento"] is None


def test_build_scanner_rows_instrumento_from_mapping():
    rows = build_scanner_rows(
        [_candidate_row(symbol="SPY"), _candidate_row(symbol="AAPL")],
        instrument_types={"SPY": "etf", "AAPL": "stock"},
    )
    assert rows[0]["Instrumento"] == "etf"
    assert rows[1]["Instrumento"] == "stock"


def test_build_scanner_rows_instrumento_none_for_symbol_missing_from_mapping():
    row = build_scanner_rows([_candidate_row(symbol="TSLA")], instrument_types={"AAPL": "stock"})[0]
    assert row["Instrumento"] is None


# --- Earnings/FOMC antes del vencimiento (Pestaña Screener, pedido 2026-07-28) ---
# _candidate_row() default expiration_date="2026-08-21".


def test_build_scanner_rows_earnings_true_when_on_or_before_expiration():
    row = build_scanner_rows(
        [_candidate_row(symbol="TSLA")], earnings_by_symbol={"TSLA": "2026-08-10"}
    )[0]
    assert row["Earnings antes del vencimiento"] is True


def test_build_scanner_rows_earnings_true_when_exactly_on_expiration():
    row = build_scanner_rows(
        [_candidate_row(symbol="TSLA")], earnings_by_symbol={"TSLA": "2026-08-21"}
    )[0]
    assert row["Earnings antes del vencimiento"] is True


def test_build_scanner_rows_earnings_false_when_after_expiration():
    row = build_scanner_rows(
        [_candidate_row(symbol="TSLA")], earnings_by_symbol={"TSLA": "2026-09-01"}
    )[0]
    assert row["Earnings antes del vencimiento"] is False


def test_build_scanner_rows_earnings_none_when_unknown():
    row = build_scanner_rows([_candidate_row(symbol="TSLA")], earnings_by_symbol={"TSLA": None})[0]
    assert row["Earnings antes del vencimiento"] is None


def test_build_scanner_rows_earnings_none_without_earnings_by_symbol_at_all():
    row = build_scanner_rows([_candidate_row(symbol="TSLA")])[0]
    assert row["Earnings antes del vencimiento"] is None


def test_build_scanner_rows_earnings_none_for_symbol_missing_from_mapping():
    row = build_scanner_rows([_candidate_row(symbol="TSLA")], earnings_by_symbol={"AAPL": "2026-08-10"})[0]
    assert row["Earnings antes del vencimiento"] is None


def test_build_scanner_rows_fomc_true_when_before_expiration():
    row = build_scanner_rows([_candidate_row(symbol="TSLA")], fed_meeting_date="2026-08-15")[0]
    assert row["FOMC antes del vencimiento"] is True


def test_build_scanner_rows_fomc_false_when_after_expiration():
    row = build_scanner_rows([_candidate_row(symbol="TSLA")], fed_meeting_date="2026-09-15")[0]
    assert row["FOMC antes del vencimiento"] is False


def test_build_scanner_rows_fomc_none_without_fed_meeting_date():
    row = build_scanner_rows([_candidate_row(symbol="TSLA")])[0]
    assert row["FOMC antes del vencimiento"] is None
