"""Tests del armado de la orden REAL de iron condor (4 patas) para Schwab (usuario 2026-08-13: pasar el
condor a real con el mismo cerebro del paper). Es la pieza crítica: un error de dirección en una pata
abriría lo OPUESTO a un condor (riesgo no acotado). Se verifica composición, instrucciones y crédito/débito.
"""

from __future__ import annotations

import pytest

from options_advisor.execution import schwab_orders as so

# Símbolos OCC de ejemplo (SPX 0DTE): put corto 7710 / put largo 7700 (ala) / call corto 7790 / call largo 7800 (ala).
SP = "SPXW  260813P07710000"   # put corto (vende)
LP = "SPXW  260813P07700000"   # put largo / ala de abajo (compra)
SC = "SPXW  260813C07790000"   # call corto (vende)
LC = "SPXW  260813C07800000"   # call largo / ala de arriba (compra)


def test_open_is_net_credit_iron_condor_with_4_legs():
    o = so.build_iron_condor_open(SP, LP, SC, LC, quantity=1, net_credit_limit=1.85)
    assert o["orderType"] == "NET_CREDIT"
    assert o["complexOrderStrategyType"] == "IRON_CONDOR"
    assert o["price"] == "1.85"
    legs = o["orderLegCollection"]
    assert len(legs) == 4
    # composición EXACTA: vender el put corto, comprar el put largo (ala), vender el call corto, comprar el call largo (ala)
    assert [(l["instruction"], l["instrument"]["symbol"]) for l in legs] == [
        ("SELL_TO_OPEN", SP),
        ("BUY_TO_OPEN", LP),
        ("SELL_TO_OPEN", SC),
        ("BUY_TO_OPEN", LC),
    ]
    assert all(l["quantity"] == 1 and l["instrument"]["assetType"] == "OPTION" for l in legs)


def test_open_quantity_scales_all_legs():
    o = so.build_iron_condor_open(SP, LP, SC, LC, quantity=3, net_credit_limit=2.0)
    assert all(l["quantity"] == 3 for l in o["orderLegCollection"])


def test_close_is_net_debit_reverses_every_leg():
    o = so.build_iron_condor_close(SP, LP, SC, LC, quantity=1, net_debit_limit=0.90)
    assert o["orderType"] == "NET_DEBIT"
    assert o["price"] == "0.90"
    legs = o["orderLegCollection"]
    # cerrar = recomprar los cortos, vender las alas (revierte cada pata de la apertura)
    assert [(l["instruction"], l["instrument"]["symbol"]) for l in legs] == [
        ("BUY_TO_CLOSE", SP),
        ("SELL_TO_CLOSE", LP),
        ("BUY_TO_CLOSE", SC),
        ("SELL_TO_CLOSE", LC),
    ]


def test_open_rejects_non_positive_credit():
    # un iron condor SIEMPRE se abre a crédito: 0 o negativo debe fallar (nunca abrir "gratis" o pagando)
    with pytest.raises(ValueError):
        so.build_iron_condor_open(SP, LP, SC, LC, quantity=1, net_credit_limit=0.0)


def test_open_rejects_duplicate_or_missing_symbols():
    with pytest.raises(ValueError):
        so.build_iron_condor_open(SP, SP, SC, LC, quantity=1, net_credit_limit=1.0)  # put largo = put corto
    with pytest.raises(ValueError):
        so.build_iron_condor_open(SP, LP, SC, "", quantity=1, net_credit_limit=1.0)  # falta una pata


def test_open_rejects_bad_quantity():
    with pytest.raises(ValueError):
        so.build_iron_condor_open(SP, LP, SC, LC, quantity=0, net_credit_limit=1.0)
