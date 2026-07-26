from __future__ import annotations

from options_advisor.broker.models import index_quote_symbol


def test_index_quote_symbol_maps_known_index_roots():
    assert index_quote_symbol("RUT") == "$RUT"
    assert index_quote_symbol("RUTW") == "$RUT"
    assert index_quote_symbol("NDX") == "$NDX"
    assert index_quote_symbol("NDXW") == "$NDX"
    assert index_quote_symbol("SPX") == "$SPX"
    assert index_quote_symbol("SPXW") == "$SPX"
    assert index_quote_symbol("VIX") == "$VIX"


def test_index_quote_symbol_passes_through_equity_symbols_unchanged():
    assert index_quote_symbol("AAPL") == "AAPL"
    assert index_quote_symbol("QQQ") == "QQQ"
