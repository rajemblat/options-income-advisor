from __future__ import annotations

from datetime import date

from options_advisor.broker.models import index_quote_symbol, parse_occ_option_symbol


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


# --- parse_occ_option_symbol (movido desde broker/schwab_client.py 2026-07-28, para
# compartirlo entre el parseo de posiciones y el de patas de órdenes) ---


def test_parse_occ_option_symbol_real_put():
    assert parse_occ_option_symbol("SLV   260821P00060000") == ("SLV", date(2026, 8, 21), "put", 60.0)


def test_parse_occ_option_symbol_real_call():
    assert parse_occ_option_symbol("AAPL  260821C00200000") == ("AAPL", date(2026, 8, 21), "call", 200.0)


def test_parse_occ_option_symbol_strips_root_padding():
    result = parse_occ_option_symbol("HOOD  260904P00075000")
    assert result is not None
    assert result[0] == "HOOD"


def test_parse_occ_option_symbol_none_for_equity_symbol():
    assert parse_occ_option_symbol("AAPL") is None


def test_parse_occ_option_symbol_none_for_invalid_date():
    assert parse_occ_option_symbol("SLV   269921P00060000") is None
