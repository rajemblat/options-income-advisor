"""Cuando rolear un put corto y a que vencimiento.

Lo pidio el usuario el 2026-09-09 mirando dos AAL de strike $13 que vencian en 9 dias con la accion
en $12.92: dentro del dinero, camino a la asignacion, y el robot sin nada que hacer al respecto.

Su regla, textual: "cuando esta ITM en los 20 dias a expirar, roll semanal si paga, si no mensual,
no mas de 40 dias; el que pague mejor porcentualmente". Y los detalles que definio despues:

    1- el strike SIEMPRE se mantiene
    2- se compara por credito POR DIA
    3- a debito NUNCA; si nada paga dentro de los 40 dias, avisa y decide el
"""

from __future__ import annotations

from datetime import date

import pytest

from options_advisor.execution import roll_rules as rr


class _Cfg:
    def __init__(self, enabled=True, dte_trigger=20, solo_itm=True, max_dte=40, max_rolls=2):
        self.enabled = enabled
        self.dte_trigger = dte_trigger
        self.solo_itm = solo_itm
        self.max_dte = max_dte
        self.max_rolls = max_rolls


# ══════════════════════════════════════════════════════════════════════════════════════════
# Cuando toca rolear
# ══════════════════════════════════════════════════════════════════════════════════════════

def test_el_caso_AAL_del_09_09_dispara_el_roll():
    """AAL strike $13, accion en $12.92, 9 dias para vencer. Es EL caso que origino la funcion."""
    toca, motivo = rr.toca_rolear(spot=12.92, strike=13.0, dte=9, rolls_hechos=0, cfg=_Cfg())
    assert toca is True
    assert "ITM" in motivo and "9 días" in motivo


def test_todavia_lejos_del_vencimiento_no_se_toca():
    """A 37 dias no hay apuro: el put todavia tiene tiempo de volver a estar fuera del dinero."""
    toca, motivo = rr.toca_rolear(spot=12.92, strike=13.0, dte=37, rolls_hechos=0, cfg=_Cfg())
    assert toca is False
    assert "se rolea desde 20" in motivo


def test_fuera_del_dinero_no_se_rolea():
    """Si la accion esta por encima del strike, el put vence sin valor y te quedas la prima entera.
    Rolear ahi seria regalar una ganancia hecha."""
    toca, motivo = rr.toca_rolear(spot=14.50, strike=13.0, dte=5, rolls_hechos=0, cfg=_Cfg())
    assert toca is False
    assert "por encima del strike" in motivo


def test_apagado_no_hace_nada():
    """Nace apagado: es la primera funcion que cierra Y abre posiciones reales sola."""
    toca, motivo = rr.toca_rolear(12.92, 13.0, 9, 0, _Cfg(enabled=False))
    assert toca is False and "apagado" in motivo


def test_el_tope_de_rolls_frena_y_lo_dice():
    """Rolear sin limite convierte una perdida chica en una posicion eterna. Al llegar al tope el
    robot se corre y decide el usuario — y el motivo tiene que explicarlo, no solo negarse."""
    toca, motivo = rr.toca_rolear(12.92, 13.0, 9, rolls_hechos=2, cfg=_Cfg(max_rolls=2))
    assert toca is False
    assert "ya se roleó" in motivo and "lo decidís vos" in motivo


def test_sin_fecha_de_vencimiento_no_se_adivina():
    toca, _ = rr.toca_rolear(12.92, 13.0, None, 0, _Cfg())
    assert toca is False


# ══════════════════════════════════════════════════════════════════════════════════════════
# A que vencimiento: credito POR DIA
# ══════════════════════════════════════════════════════════════════════════════════════════

def _cand(dias_totales, prima_nueva, costo_recompra=0.44, dte_actual=9):
    return rr.evaluar_candidato(costo_recompra, prima_nueva, dte_actual,
                                date(2026, 9, 18), dias_totales)


def test_gana_el_mejor_credito_POR_DIA_y_no_el_de_mas_dolares():
    """El corazon de la regla. El mensual paga MAS dolares y aun asi pierde, porque ata cuatro veces
    mas tiempo. Comparar totales elegiria siempre el plazo largo sin mirar si rinde."""
    semanal = rr.evaluar_candidato(0.44, 0.70, 9, date(2026, 9, 25), 16)   # +$0,26 en 7 dias
    mensual = rr.evaluar_candidato(0.44, 1.10, 9, date(2026, 10, 16), 37)  # +$0,66 en 28 dias
    assert mensual.credito_total > semanal.credito_total, "el mensual paga mas dolares..."
    mejor, motivo = rr.elegir_roll([semanal, mensual], _Cfg())
    assert mejor is semanal, "...pero el semanal rinde mas por dia"
    assert "por día" in motivo


def test_nunca_se_rolea_a_debito():
    """Regla 3 del usuario, textual: "débito nunca". Un roll que cuesta plata es pagar por posponer
    un problema."""
    caro = rr.evaluar_candidato(0.44, 0.30, 9, date(2026, 9, 25), 16)   # cobra menos de lo que cuesta
    assert caro.credito_neto < 0
    mejor, motivo = rr.elegir_roll([caro], _Cfg())
    assert mejor is None
    assert "No se rolea a débito" in motivo


def test_no_pasa_de_los_40_dias():
    """Tope del usuario. Un vencimiento a 60 dias podria pagar muy bien y aun asi queda afuera: no
    quiere quedar atado tanto tiempo."""
    lejos = rr.evaluar_candidato(0.44, 2.00, 9, date(2026, 11, 20), 72)
    assert lejos.credito_neto > 0, "paga muy bien..."
    mejor, motivo = rr.elegir_roll([lejos], _Cfg(max_dte=40))
    assert mejor is None and "pasan los 40 días" in motivo


def test_cuando_ninguno_paga_el_motivo_dice_cuanto_faltaba():
    """"Si no, consultar" — y para poder decidir, el usuario necesita saber cuan lejos estuvo."""
    a = rr.evaluar_candidato(0.44, 0.40, 9, date(2026, 9, 25), 16)
    b = rr.evaluar_candidato(0.44, 0.35, 9, date(2026, 10, 16), 37)
    mejor, motivo = rr.elegir_roll([a, b], _Cfg())
    assert mejor is None
    assert "-4.00" in motivo or "$-4" in motivo, f"tiene que decir cuanto costaria: {motivo}"


def test_a_igual_rendimiento_gana_el_vencimiento_mas_corto():
    """Desempate estable: menos tiempo atado a la misma apuesta y antes se vuelve a decidir."""
    corto = rr.evaluar_candidato(0.40, 0.61, 9, date(2026, 9, 25), 16)    # +0,21 / 7 dias = 0,03
    largo = rr.evaluar_candidato(0.40, 1.03, 9, date(2026, 10, 30), 30)   # +0,63 / 21 dias = 0,03
    assert corto.credito_por_dia == pytest.approx(largo.credito_por_dia, abs=1e-6)
    mejor, _ = rr.elegir_roll([largo, corto], _Cfg())
    assert mejor is corto


def test_un_vencimiento_que_no_agrega_dias_no_es_un_roll():
    """Rolear al mismo dia, o hacia atras, no es rolear."""
    assert rr.evaluar_candidato(0.44, 0.90, 9, date(2026, 9, 18), 9) is None
    assert rr.evaluar_candidato(0.44, 0.90, 9, date(2026, 9, 11), 4) is None


def test_sin_candidatos_lo_dice_en_vez_de_romperse():
    mejor, motivo = rr.elegir_roll([], _Cfg())
    assert mejor is None and "no hay vencimientos" in motivo


# ══════════════════════════════════════════════════════════════════════════════════════════
# Precios ejecutables, no el mid
# ══════════════════════════════════════════════════════════════════════════════════════════

def test_la_cuenta_usa_ask_para_recomprar_y_bid_para_vender():
    """Misma leccion que el condor del 02/09, que costo $295: se recompra pagando el ASK y se vende
    cobrando el BID. Un roll que "pagaba al mid" es un consuelo que no se puede cobrar."""
    ask_viejo, bid_nuevo = 0.48, 0.62
    c = rr.evaluar_candidato(ask_viejo, bid_nuevo, 9, date(2026, 9, 25), 16)
    assert c.credito_neto == pytest.approx(0.14)
    assert c.credito_total == pytest.approx(14.0), "por contrato son 100 acciones"
