from __future__ import annotations

from datetime import date

from options_advisor.market_context import earnings


def test_historical_earnings_no_key_returns_list():
    # Sin API key de Finnhub y sin red, debe devolver una lista (posiblemente vacía), nunca romper.
    out = earnings.historical_earnings_dates("META", date(2024, 1, 1), date(2024, 12, 31), None)
    assert isinstance(out, list)


def test_yahoo_helper_graceful():
    out = earnings._yahoo_earnings_dates("ZZZZINVALID", date(2024, 1, 1), date(2024, 2, 1))
    assert isinstance(out, list)
