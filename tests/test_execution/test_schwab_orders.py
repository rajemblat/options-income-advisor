"""Tests del armado de órdenes de Schwab. Lo crítico: el símbolo OCC (un error = orden rechazada o
sobre el instrumento equivocado) y que la orden sea SIEMPRE límite."""

from __future__ import annotations

from datetime import date

import pytest

from options_advisor.execution import schwab_orders as so


def test_occ_symbol_basic():
    # AAL put $11 vto 2026-09-18 → 'AAL   260918P00011000'
    sym = so.occ_option_symbol("AAL", date(2026, 9, 18), "P", 11.0)
    assert sym == "AAL   260918P00011000"
    assert len(sym) == 21


def test_occ_symbol_decimal_strike():
    # strike $6.50 → 6500 → '00006500'
    sym = so.occ_option_symbol("NU", date(2026, 12, 18), "P", 6.5)
    assert sym == "NU    261218P00006500"
    assert len(sym) == 21


def test_occ_symbol_high_strike_and_call():
    # SPY call $600 → 600000 → '00600000'
    sym = so.occ_option_symbol("SPY", date(2026, 3, 20), "C", 600.0)
    assert sym == "SPY   260320C00600000"
    assert len(sym) == 21


def test_occ_symbol_rejects_bad_input():
    with pytest.raises(ValueError):
        so.occ_option_symbol("TOOLONGROOT", date(2026, 9, 18), "P", 11.0)
    with pytest.raises(ValueError):
        so.occ_option_symbol("AAL", date(2026, 9, 18), "X", 11.0)
    with pytest.raises(ValueError):
        so.occ_option_symbol("AAL", date(2026, 9, 18), "P", 0.0)


def test_build_sell_put_to_open_payload():
    order = so.build_sell_put_to_open("AAL", date(2026, 9, 18), 11.0, quantity=1, limit_price=1.68)
    assert order["orderType"] == "LIMIT"          # nunca market
    assert order["price"] == "1.68"
    assert order["duration"] == "DAY"
    assert order["orderStrategyType"] == "SINGLE"
    leg = order["orderLegCollection"][0]
    assert leg["instruction"] == so.SELL_TO_OPEN
    assert leg["quantity"] == 1
    assert leg["instrument"] == {"symbol": "AAL   260918P00011000", "assetType": "OPTION"}


def test_build_buy_put_to_close_payload():
    order = so.build_buy_put_to_close("NU", date(2026, 12, 18), 6.5, quantity=2, limit_price=0.70)
    leg = order["orderLegCollection"][0]
    assert leg["instruction"] == so.BUY_TO_CLOSE
    assert leg["quantity"] == 2
    assert order["price"] == "0.70"


def test_build_order_rejects_market_like_and_bad_qty():
    with pytest.raises(ValueError):
        so.build_option_order("AAL   260918P00011000", so.SELL_TO_OPEN, 0, 1.68)   # qty < 1
    with pytest.raises(ValueError):
        so.build_option_order("AAL   260918P00011000", so.SELL_TO_OPEN, 1, 0.0)    # precio ≤ 0
    with pytest.raises(ValueError):
        so.build_option_order("AAL   260918P00011000", "SELL_SHORT", 1, 1.68)      # instrucción no soportada


def test_describe_order_is_readable():
    order = so.build_sell_put_to_open("AAL", date(2026, 9, 18), 11.0, 1, 1.68)
    txt = so.describe_order(order)
    assert "DRY-RUN" in txt and "VENDER para abrir" in txt and "NO enviada" in txt
