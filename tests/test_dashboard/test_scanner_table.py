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
