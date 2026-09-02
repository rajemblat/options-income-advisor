"""El condor opera al precio que el mercado OFRECE, no al mid — en real y en papel.

Usuario 2026-09-02, explicando como opera el mismo:

    "cuando yo entro, generalmente entro a la prima que me da pero en limit, no espero el mid,
     porque el SPX maneja muy poca spread entre el ask y el bid"
    "se cierra en limit pero al precio que ofrece, no se espera el mid"

Por que importa, con los numeros de ese dia. El robot armo el condor 7595/7670 y dejo la orden
puesta AL MID a las 09:30. Llenо a las 10:08 — treinta y ocho minutos despues, en un mercado que ya
no era el que la habia justificado — y termino en -$295. En paralelo, el simulador dio esa MISMA
entrada por hecha al instante, tambien al mid, y cobro +$36 a las 09:43.

Mismos strikes. Mismo credito. Resultado opuesto. La diferencia entera era CUANDO llenO.

Al precio ejecutable la orden entra ya. Ese dia eran $165 contra $177.50 al mid: doce dolares y
medio por condor a cambio de que no quede ninguna orden colgada decidiendo sola media hora despues.
Y de yapa, un solo peldano significa CERO reemplazos: sin ids nuevos no hay forma de perder una
orden vieja viva, que fue el otro bug del dia.
"""

from __future__ import annotations

import pytest

from options_advisor.execution import live_condor_engine as lce
from options_advisor.simulator import iron_condor


class _Contrato:
    def __init__(self, otype, strike, bid, ask, delta=0.12):
        self.option_type, self.strike, self.bid, self.ask = otype, strike, bid, ask
        self.delta = delta

    @property
    def mid_price(self):
        return round((self.bid + self.ask) / 2, 4)


# Las puntas REALES del condor 7595/7670 del 2026-09-02, tal como las guardo el detector.
SP = _Contrato("put", 7595, 0.85, 0.90)
LP = _Contrato("put", 7585, 0.60, 0.65)
SC = _Contrato("call", 7670, 3.00, 3.10)
LC = _Contrato("call", 7680, 1.50, 1.55)


def test_la_apertura_va_al_credito_que_paga_el_mercado():
    """(0.85 + 3.00) - (0.65 + 1.55) = 1.65. Al mid daban 1.775 — un precio que nadie paga."""
    escalera = lce._open_credit_ladder(SP, SC, LP, LC)
    assert escalera == [1.65]


def test_el_cierre_va_al_debito_que_cobra_el_mercado():
    """(0.90 + 3.10) - (0.60 + 1.50) = 1.90. Salir cuesta mas que el mid, siempre."""
    assert lce._close_debit_ladder(SP, SC, LP, LC) == [1.90]


def test_la_escalera_tiene_UN_SOLO_peldano():
    """Un peldano = ninguna orden de reemplazo = ningun id nuevo. El 02/09 la escalera hizo
    1.95 -> 1.90 -> 1.85 -> 1.82; el reemplazo a 1.82 salio rechazado, la de 1.85 quedo viva y el
    robot la perdio de vista. Sin reemplazos ese bug no tiene por donde entrar."""
    assert len(lce._open_credit_ladder(SP, SC, LP, LC)) == 1
    assert len(lce._close_debit_ladder(SP, SC, LP, LC)) == 1


def test_los_dos_precios_caen_en_la_grilla_de_5_centavos():
    for escalera in (lce._open_credit_ladder(SP, SC, LP, LC),
                     lce._close_debit_ladder(SP, SC, LP, LC)):
        for precio in escalera:
            assert round(precio * 100) % 5 == 0


def test_se_redondea_siempre_en_contra_nuestra():
    """Credito hacia ABAJO (pedimos un poco menos, llena), debito hacia ARRIBA (ofrecemos un poco
    mas, sale). Nunca al reves: 4 centavos no valen una orden que no entra."""
    sp = _Contrato("put", 7595, 0.87, 0.91)
    lp = _Contrato("put", 7585, 0.60, 0.64)
    sc = _Contrato("call", 7670, 3.01, 3.09)
    lc = _Contrato("call", 7680, 1.50, 1.56)
    credito_exacto = (0.87 + 3.01) - (0.64 + 1.56)      # 1.68
    debito_exacto = (0.91 + 3.09) - (0.60 + 1.50)       # 1.90
    assert lce._open_credit_ladder(sp, sc, lp, lc) == [1.65]   # 1.68 -> abajo
    assert credito_exacto > 1.65
    assert lce._close_debit_ladder(sp, sc, lp, lc) == [1.90]   # ya en la grilla
    assert debito_exacto == 1.90


def test_sin_credito_positivo_no_hay_operacion():
    """Si vendiendo al bid y comprando las alas al ask no queda credito, no hay condor. Al mid
    podria parecer que si — y esa es exactamente la ilusion que se esta sacando del medio."""
    sp = _Contrato("put", 7595, 0.50, 0.90)
    lp = _Contrato("put", 7585, 0.40, 0.95)
    sc = _Contrato("call", 7670, 1.00, 1.40)
    lc = _Contrato("call", 7680, 0.90, 1.30)
    assert lce._credito_realizable(sp, sc, lp, lc) <= 0
    assert lce._open_credit_ladder(sp, sc, lp, lc) == []


def test_el_cierre_nunca_baja_del_piso_de_5_centavos():
    """Una orden de cierre a debito neto <= 0 no la acepta Schwab. Si el condor ya no vale nada, se
    sale por el minimo valido — nunca se deja abierto por ahorrar centavos."""
    sp = _Contrato("put", 7595, 0.00, 0.01)
    lp = _Contrato("put", 7585, 0.00, 0.01)
    sc = _Contrato("call", 7670, 0.00, 0.01)
    lc = _Contrato("call", 7680, 0.00, 0.01)
    assert lce._close_debit_ladder(sp, sc, lp, lc) == [lce._MIN_CLOSE_DEBIT]


# ══════════════════════════════════════════════════════════════════════════════════════════
# El PAPEL usa exactamente los mismos precios — si no, no sirve para decidir nada
# ══════════════════════════════════════════════════════════════════════════════════════════

class _Cadena:
    def __init__(self, contratos):
        self.contracts = contratos


class _Greeks:
    def __init__(self, delta):
        self.delta = delta


class _ContratoConGriegas(_Contrato):
    def __init__(self, otype, strike, bid, ask, delta):
        super().__init__(otype, strike, bid, ask)
        self.greeks = _Greeks(delta)
        self.expiration = None
        self.occ_symbol = f"SPXW  260902{otype[0].upper()}{int(strike * 1000):08d}"


def _cadena_del_dia():
    return _Cadena([
        _ContratoConGriegas("put", 7595, 0.85, 0.90, -0.12),
        _ContratoConGriegas("put", 7585, 0.60, 0.65, -0.10),
        _ContratoConGriegas("call", 7670, 3.00, 3.10, 0.14),
        _ContratoConGriegas("call", 7680, 1.50, 1.55, 0.10),
    ])


def _cfg():
    from options_advisor.config import load_settings
    return load_settings().intraday_condor.model_copy(
        update={"wing_width": 10.0, "short_delta_max": 0.20, "min_credit": 0.0,
                "max_collateral": 1000.0})


def test_el_papel_arma_el_condor_al_credito_realizable():
    """$165, el mismo numero que manda el real. Antes armaba a $177.50 y despues comparaba peras con
    manzanas contra un real que nunca cobro eso."""
    build = iron_condor.build_iron_condor(_cadena_del_dia(), 7638.0, _cfg())
    assert build is not None
    assert build.net_credit == 165.0


def test_el_papel_valua_la_salida_al_precio_real():
    """(0.90 + 3.10) - (0.60 + 1.50) = 1.90 -> $190. Al mid dice $177.50, y esa diferencia de $12.50
    por operacion es la que inflaba las ganancias del simulador."""
    cadena = _cadena_del_dia()
    salida = iron_condor.condor_exit_value(cadena, 7595, 7670, 7585, 7680)
    mid = iron_condor.condor_close_value(cadena, 7595, 7670, 7585, 7680)
    assert salida == pytest.approx(190.0)
    assert mid == pytest.approx(177.5)
    assert salida > mid


def test_el_papel_y_el_real_dicen_el_MISMO_numero():
    """La prueba de fondo: sobre la misma cadena, el credito que el papel anota y el que el real
    manda a Schwab tienen que ser el mismo. Si no, el papel no sirve para decidir si el real anda."""
    cadena = _cadena_del_dia()
    build = iron_condor.build_iron_condor(cadena, 7638.0, _cfg())
    escalera = lce._open_credit_ladder(SP, SC, LP, LC)
    assert build.net_credit == pytest.approx(escalera[0] * 100)
