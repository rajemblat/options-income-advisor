"""El tamaño de la posición sale de la PLATA que traba, no del número del strike.

Usuario 2026-09-08, viendo que WFC abrio un solo contrato: "por el tamano de mi cuenta deberia
abrir minimo 2 de ese monto de tiket".

Tenia razon, y el problema era mas de fondo que WFC. La regla vieja miraba el strike: menos de $30
-> 4 contratos, todo lo demas -> 1. Con eso, ese dia:

    WFC   strike $80   -> 1 contrato, traba $551
    AAPL  strike $250  -> 1 contrato, traba $1.415     (2,6 veces mas grande)
    NCLH  strike $17   -> traba $144                   (diez veces mas chica)

La misma decision del cerebro, apostada con montos incomparables. Y no es solo el tamano: el
aprendizaje compara ganadoras contra perdedoras, y estaba comparando operaciones de $130 con
operaciones de $1.415 como si pesaran lo mismo.
"""

from __future__ import annotations

import pytest

from options_advisor.execution.live_engine import contracts_for_collateral, contracts_for_strike


class _LT:
    """La config real del 2026-09-08."""

    def __init__(self, objetivo=1100.0, techo=4, barato_max=30.0, barato_ctos=4, base=1):
        self.target_collateral_per_position = objetivo
        self.max_contracts_per_order = techo
        self.base_contracts_per_order = base
        self.cheap_strike_max = barato_max
        self.cheap_strike_contracts = barato_ctos


# Los colaterales REALES por contrato, del registro de ordenes de esa semana.
@pytest.mark.parametrize("nombre, margen, strike, esperado", [
    ("WFC", 551.15, 80.0, 2),     # el caso que lo destapo: $1.102
    ("UAL", 622.44, 92.5, 2),     # $1.244
    ("DIS", 809.0, 100.0, 1),     # 1,36 -> 1
    ("COIN", 1023.0, 150.0, 1),
    ("AMZN", 1310.0, 220.0, 1),
    ("AAPL", 1415.0, 250.0, 1),   # ya trababa lo justo con uno
    ("NU", 105.0, 13.0, 4),       # querria 10; el techo lo deja en 4
    ("AAL", 87.0, 12.0, 4),
])
def test_los_tamanos_reales_de_la_semana(nombre, margen, strike, esperado):
    assert contracts_for_collateral(margen, strike, _LT()) == esperado


def test_wfc_pasa_de_uno_a_dos():
    """El caso puntual que el usuario marco, aislado."""
    lt = _LT()
    assert contracts_for_strike(80.0, lt) == 1, "la regla vieja daba 1"
    assert contracts_for_collateral(551.15, 80.0, lt) == 2, "la nueva da 2"


def test_las_posiciones_quedan_parecidas_entre_si():
    """La prueba de fondo: con la regla nueva, lo que traba cada posicion tiene que estar en el mismo
    orden de magnitud. Antes iban de $130 a $1.415 — once veces de diferencia."""
    lt = _LT()
    margenes = [551.15, 622.44, 809.0, 1023.0, 1310.0, 1415.0]
    totales = [m * contracts_for_collateral(m, 100.0, lt) for m in margenes]
    assert max(totales) / min(totales) < 2.0, f"siguen muy dispares: {totales}"


def test_el_techo_por_orden_manda_siempre():
    """Un strike baratisimo pediria decenas de contratos. El techo es el freno duro."""
    assert contracts_for_collateral(20.0, 5.0, _LT(techo=4)) == 4
    assert contracts_for_collateral(20.0, 5.0, _LT(techo=8)) == 8


def test_nunca_pide_menos_de_uno():
    """Aunque un solo contrato ya supere el objetivo, la operacion se hace con uno — no con cero."""
    assert contracts_for_collateral(5000.0, 400.0, _LT()) == 1


def test_sin_objetivo_configurado_manda_la_regla_vieja():
    """Con el objetivo en 0 el comportamiento es exactamente el de antes: nadie que no lo configure
    ve un cambio."""
    lt = _LT(objetivo=0.0)
    assert contracts_for_collateral(551.15, 80.0, lt) == 1     # caro -> base
    assert contracts_for_collateral(105.0, 13.0, lt) == 4      # barato -> la regla de strike


def test_sin_margen_calculable_no_se_inventa_un_tamano():
    """Si no se pudo calcular lo que traba un contrato, se cae a la regla por strike en vez de
    adivinar. Un tamano inventado es plata real puesta a ciegas."""
    lt = _LT()
    assert contracts_for_collateral(None, 13.0, lt) == 4
    assert contracts_for_collateral(0.0, 80.0, lt) == 1


def test_el_redondeo_es_al_mas_cercano_y_explicito():
    """1,4 -> 1 y 1,6 -> 2. Se usa int(x+0.5) y no round(), porque round() manda el .5 al par y sobre
    el tamano de una posicion real esa sutileza seria una sorpresa silenciosa."""
    lt = _LT(objetivo=1000.0)
    assert contracts_for_collateral(700.0, 100.0, lt) == 1     # 1,43
    assert contracts_for_collateral(620.0, 100.0, lt) == 2     # 1,61
    assert contracts_for_collateral(400.0, 100.0, lt) == 3     # 2,50 -> 3, no 2
