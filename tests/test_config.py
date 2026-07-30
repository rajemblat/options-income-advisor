from __future__ import annotations

from options_advisor.config import load_movers_universe

# Universos reales de Market Movers (pedido 2026-07-29, "top 10 real por %" — ver
# dashboard/components.py::cached_movers) — verifica que los 3 archivos cargan y tienen
# tamaños razonables, no que coincidan con un snapshot exacto (los índices se reconstituyen).


def test_load_movers_universe_sp500_has_hundreds_of_real_tickers():
    symbols = load_movers_universe("$SPX")
    assert len(symbols) > 400
    assert "AAPL" in symbols
    assert "MSFT" in symbols


def test_load_movers_universe_nasdaq100_has_around_100_tickers():
    symbols = load_movers_universe("$COMPX")
    assert 90 <= len(symbols) <= 120
    assert "NVDA" in symbols


def test_load_movers_universe_dow30_has_exactly_30_tickers():
    symbols = load_movers_universe("$DJI")
    assert len(symbols) == 30
    assert "AAPL" in symbols
    assert "MMM" in symbols


def test_load_movers_universe_no_duplicate_tickers():
    for index in ("$SPX", "$COMPX", "$DJI"):
        symbols = load_movers_universe(index)
        assert len(symbols) == len(set(symbols)), f"duplicados en {index}"
