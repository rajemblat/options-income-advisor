"""La ORDEN del roll: dos patas, una sola orden, nunca a débito (usuario 2026-09-09).

Esta es la primera orden del sistema que CIERRA y ABRE al mismo tiempo, y con plata real. Un payload
mal armado no es un bug que se ve en una pantalla: es una operación equivocada en la cuenta.

Lo que se afirma acá, en orden de qué tan caro sale si falla:
  · a DÉBITO no se puede ni construir — la regla del usuario, hecha cumplir en el último lugar
    por el que pasa la orden antes de salir;
  · el orden de las patas: primero se recompra lo viejo, después se vende lo nuevo;
  · las dos patas en UNA orden combinada, para que no exista el instante en que estás descubierto;
  · misma cantidad en las dos patas — rolear menos contratos de los que tenés deja un resto suelto.
"""

from __future__ import annotations

import pytest

from options_advisor.execution.schwab_orders import (
    BUY_TO_CLOSE,
    SELL_TO_OPEN,
    build_roll_put,
    describe_roll,
    occ_option_symbol,
)
from datetime import date

VIEJO = occ_option_symbol("AAL", date(2026, 9, 18), "put", 13.0)
NUEVO = occ_option_symbol("AAL", date(2026, 10, 16), "put", 13.0)


# ─────────────────── la regla que más cuesta si falla ───────────────────

def test_a_debito_no_se_puede_ni_construir():
    """Regla textual del usuario: "débito nunca". Acá es donde se hace cumplir de verdad: aunque
    algo más arriba se equivoque, la orden no llega a existir."""
    with pytest.raises(ValueError, match="débito"):
        build_roll_put(VIEJO, NUEVO, quantity=1, net_credit_limit=-0.25)


def test_a_credito_cero_tampoco():
    with pytest.raises(ValueError):
        build_roll_put(VIEJO, NUEVO, quantity=1, net_credit_limit=0.0)


# ─────────────────── la composición ───────────────────

def test_primero_recompra_lo_viejo_y_despues_vende_lo_nuevo():
    orden = build_roll_put(VIEJO, NUEVO, quantity=1, net_credit_limit=0.35)
    patas = orden["orderLegCollection"]
    assert len(patas) == 2
    assert patas[0]["instruction"] == BUY_TO_CLOSE
    assert patas[0]["instrument"]["symbol"] == VIEJO
    assert patas[1]["instruction"] == SELL_TO_OPEN
    assert patas[1]["instrument"]["symbol"] == NUEVO


def test_va_como_una_sola_orden_a_credito_neto():
    """Combinada, Schwab ejecuta las dos patas o ninguna. Sueltas, entre una y otra hay un instante
    en el que estás descubierto o doblemente vendido."""
    orden = build_roll_put(VIEJO, NUEVO, quantity=1, net_credit_limit=0.35)
    assert orden["orderType"] == "NET_CREDIT"
    assert orden["orderStrategyType"] == "SINGLE"
    assert orden["price"] == "0.35"


def test_las_dos_patas_llevan_la_misma_cantidad():
    """Rolear 3 de 5 contratos dejaría 2 sueltos venciendo, que es justo lo que se quiere evitar."""
    orden = build_roll_put(VIEJO, NUEVO, quantity=5, net_credit_limit=0.35)
    assert [p["quantity"] for p in orden["orderLegCollection"]] == [5, 5]


def test_rolear_al_mismo_contrato_no_es_rolear():
    with pytest.raises(ValueError, match="no es un roll"):
        build_roll_put(VIEJO, VIEJO, quantity=1, net_credit_limit=0.35)


def test_sin_simbolos_no_hay_orden():
    with pytest.raises(ValueError):
        build_roll_put("", NUEVO, quantity=1, net_credit_limit=0.35)


def test_cantidad_invalida():
    with pytest.raises(ValueError):
        build_roll_put(VIEJO, NUEVO, quantity=0, net_credit_limit=0.35)


# ─────────────────── lo que el usuario lee antes de aprobar ───────────────────

def test_la_descripcion_dice_que_se_cierra_que_se_abre_y_cuanto_se_cobra():
    """El usuario aprueba mirando este texto, así que tiene que alcanzar por sí solo."""
    orden = build_roll_put(VIEJO, NUEVO, quantity=5, net_credit_limit=0.35)
    txt = describe_roll(orden, symbol="AAL", strike=13.0)
    assert "AAL" in txt
    assert "13" in txt
    assert VIEJO in txt and NUEVO in txt
    assert "5" in txt
    assert "175" in txt          # 0.35 × 100 × 5 contratos = $175 en total


def test_la_descripcion_funciona_sin_datos_opcionales():
    orden = build_roll_put(VIEJO, NUEVO, quantity=1, net_credit_limit=0.35)
    assert "ROLL" in describe_roll(orden)


# ─────────────────── el redondeo de centavos ───────────────────

def test_el_credito_se_manda_con_dos_decimales():
    """Schwab rechaza precios con más decimales de los que el instrumento admite."""
    orden = build_roll_put(VIEJO, NUEVO, quantity=1, net_credit_limit=0.3456)
    assert orden["price"] == "0.35"
