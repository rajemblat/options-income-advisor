"""La prima tiene que pagar la EXPOSICIÓN: nada de vender un AAPL de $250 por $26.

Caso real del 2026-09-04, con dinero de verdad. El robot vendió un put de AAPL strike 250 —21% abajo
del precio, delta 0.018— por $0.26 de prima: $26 cobrados contra $25.000 de exposición de asignación
por 42 días. Usuario: "en una exposición de AAPL no puede solo tener una prima de 26, mínimo debe ser
250".

Ningún filtro lo frenó. `min_credit` es un piso en dólares por acción ($0.10) que le pide lo mismo a
un AAL de $13 que a un AAPL de $250. Y el cerebro flexible no tiene piso: puntúa por equilibrio, y en
ese put la cobertura, el POP y el theta sacaron 1.000 perfecto JUSTAMENTE porque el strike estaba
lejísimos — taparon el 0.000 del delta y el total dio 0.642 contra un mínimo de 0.40.

El piso nuevo es proporcional al strike, así escala solo con el tamaño de la exposición. Se verifica
en las dos capas: el cerebro (que directamente no lo elige) y el guardián (que no lo manda).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from options_advisor.broker.models import Greeks, OptionChain, OptionContract
from options_advisor.config import load_settings
from options_advisor.execution import live_guard
from options_advisor.simulator import entry_rules

AS_OF = date(2026, 9, 4)
PRECIO_AAPL = 318.0


def _put(strike: float, bid: float, delta: float, dte: int = 42) -> OptionContract:
    return OptionContract(
        symbol=f"AAPL  261016P{round(strike * 1000):08d}",
        option_type="put", strike=strike, expiration=AS_OF + timedelta(days=dte),
        bid=bid, ask=round(bid + 0.02, 2), last_price=round(bid + 0.01, 2),
        implied_volatility=0.2556, open_interest=5000, volume=800,
        greeks=Greeks(delta=delta, gamma=0.001, theta=-0.02, vega=0.048, rho=0.01, source="broker"),
    )


def _cadena(contratos) -> OptionChain:
    return OptionChain(symbol="AAPL", as_of=AS_OF, underlying_price=PRECIO_AAPL, contracts=contratos)


def _settings(**over):
    s = load_settings().simulator
    return s.model_copy(update=over) if over else s


# ── el cerebro no lo elige ──────────────────────────────────────────────────────────────────────

def test_el_aapl_de_26_dolares_ya_no_se_elige():
    """EL caso del 04/09. Con el piso del 1%, un AAPL 250 necesita $2.50 por acción ($250)."""
    cadena = _cadena([_put(250.0, 0.26, -0.018)])
    elegido, motivo = entry_rules._select_put_scored(
        cadena, AS_OF, volatile=False, underlying_price=PRECIO_AAPL,
        settings=_settings(min_premium_pct_of_strike=0.01))
    assert elegido is None, "Volvió a elegir el put que cobra $26 por $25.000 de exposición"
    assert "prima" in (motivo or "").lower()


def test_el_mismo_strike_si_pagara_bien_si_se_elige():
    """No es una regla contra los strikes lejanos: es contra las primas que no pagan."""
    cadena = _cadena([_put(250.0, 2.60, -0.018)])   # $260 de prima, arriba del piso de $250
    elegido, _ = entry_rules._select_put_scored(
        cadena, AS_OF, volatile=False, underlying_price=PRECIO_AAPL,
        settings=_settings(min_premium_pct_of_strike=0.01))
    assert elegido is not None and elegido.strike == 250.0


def test_entre_varios_descarta_solo_los_que_no_pagan():
    cadena = _cadena([
        _put(250.0, 0.26, -0.018),   # $26  → fuera
        _put(280.0, 1.50, -0.08),    # $150 → fuera (piso $280)
        _put(300.0, 4.20, -0.20),    # $420 → entra (piso $300)
    ])
    elegido, _ = entry_rules._select_put_scored(
        cadena, AS_OF, volatile=False, underlying_price=PRECIO_AAPL,
        settings=_settings(min_premium_pct_of_strike=0.01))
    assert elegido is not None and elegido.strike == 300.0


def test_si_ninguno_paga_no_abre_nada():
    """"Si no paga nada no la abran" — con el techo de soporte empujando lejos, la respuesta
    correcta es no operar, no conformarse con el menos malo."""
    cadena = _cadena([_put(250.0, 0.26, -0.018), _put(255.0, 0.31, -0.02), _put(260.0, 0.40, -0.03)])
    elegido, motivo = entry_rules._select_put_scored(
        cadena, AS_OF, volatile=False, underlying_price=PRECIO_AAPL,
        settings=_settings(min_premium_pct_of_strike=0.01))
    assert elegido is None
    assert "3 descartados por prima" in (motivo or "")


def test_el_piso_escala_con_la_exposicion():
    """El 1% pide $250 en un AAPL de $250 y $13 en un AAL de $13 — la misma exigencia relativa."""
    barato = OptionChain(symbol="AAL", as_of=AS_OF, underlying_price=15.0, contracts=[
        OptionContract(symbol="AAL   261016P00013000", option_type="put", strike=13.0,
                       expiration=AS_OF + timedelta(days=42), bid=0.22, ask=0.24, last_price=0.23,
                       implied_volatility=0.45, open_interest=5000, volume=800,
                       greeks=Greeks(delta=-0.20, gamma=0.01, theta=-0.01, vega=0.02, rho=0.01,
                                     source="broker"))])
    elegido, _ = entry_rules._select_put_scored(
        barato, AS_OF, volatile=True, underlying_price=15.0,
        settings=_settings(min_premium_pct_of_strike=0.01))
    assert elegido is not None, "$22 en un strike de $13 SÍ paga la exposición (piso $13)"


def test_apagado_se_comporta_como_antes():
    cadena = _cadena([_put(250.0, 0.26, -0.018)])
    elegido, _ = entry_rules._select_put_scored(
        cadena, AS_OF, volatile=False, underlying_price=PRECIO_AAPL,
        settings=_settings(min_premium_pct_of_strike=0.0))
    assert elegido is not None, "Con el piso en 0 no debe cambiar nada del comportamiento viejo"


# ── el guardián no lo manda ─────────────────────────────────────────────────────────────────────

def _limites(**over):
    base = dict(enabled=True, dry_run=False, kill_switch=False, require_manual_arm=False,
                max_contracts_per_order=4, max_notional_per_order=40000.0, max_orders_per_day=5,
                max_total_deployed=50000.0, max_underlying_price=700.0,
                min_premium_pct_of_strike=0.01)
    base.update(over)
    return live_guard.LiveLimits(**base)


def _orden(strike: float, limite: float, contratos: int = 1):
    return live_guard.IntendedOrder(
        symbol="AAPL", action=live_guard.ACTION_OPEN, option_type="PUT", strike=strike,
        expiration="2026-10-16", requested_contracts=contratos, limit_price=limite,
        underlying_price=PRECIO_AAPL, collateral_per_contract=1414.56)


def _cuenta():
    return live_guard.AccountSnapshot(cash=50000.0, equity=50000.0)


def _dia():
    return live_guard.DayState(orders_today=0, deployed_today=0.0, orders_this_week=0)


def test_el_guardian_rechaza_la_prima_que_no_paga():
    dec = live_guard.evaluate(_orden(250.0, 0.26), _cuenta(), _limites(), _dia(), armed=True)
    assert dec.rejected
    assert any("no paga la exposición" in r for r in dec.reasons), dec.reasons


def test_el_guardian_aprueba_cuando_la_prima_alcanza():
    dec = live_guard.evaluate(_orden(250.0, 2.60), _cuenta(), _limites(), _dia(), armed=True)
    assert dec.approved and dec.final_contracts == 1


def test_el_guardian_rechaza_en_vez_de_recortar_contratos():
    """Bajar contratos no arregla una prima floja: prima y exposición escalan igual."""
    dec = live_guard.evaluate(_orden(250.0, 0.26, contratos=4), _cuenta(), _limites(), _dia(), armed=True)
    assert dec.rejected and dec.final_contracts == 0


def test_el_cierre_nunca_se_frena_por_el_piso():
    """Siempre hay que poder SALIR: el piso es una regla de apertura, no de cierre."""
    cierre = live_guard.IntendedOrder(
        symbol="AAPL", action=live_guard.ACTION_CLOSE, option_type="PUT", strike=250.0,
        expiration="2026-10-16", requested_contracts=1, limit_price=0.02,
        underlying_price=PRECIO_AAPL, collateral_per_contract=1414.56)
    dec = live_guard.evaluate(cierre, _cuenta(), _limites(), _dia(), armed=True)
    assert dec.approved, "Un cierre barato es lo que QUEREMOS: nunca se bloquea la salida"


def test_apagado_el_guardian_deja_pasar():
    dec = live_guard.evaluate(_orden(250.0, 0.26), _cuenta(), _limites(min_premium_pct_of_strike=0.0),
                              _dia(), armed=True)
    assert dec.approved


@pytest.mark.parametrize("strike,prima,pasa", [
    (250.0, 2.50, True),    # justo en el piso
    (250.0, 2.49, False),   # un centavo abajo
    (150.0, 3.24, True),    # el COIN real del 04/09: $324 sobre un piso de $150
    (170.0, 0.69, False),   # el NVDA real de agosto: $69 sobre un piso de $170
])
def test_casos_reales_de_la_bitacora(strike, prima, pasa):
    dec = live_guard.evaluate(_orden(strike, prima), _cuenta(), _limites(), _dia(), armed=True)
    assert dec.approved is pasa
