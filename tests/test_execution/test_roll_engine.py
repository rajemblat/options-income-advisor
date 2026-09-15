"""El detector de rolls: qué propone, qué descarta y por qué (usuario 2026-09-09 / 2026-09-14).

El caso que le dio origen, y que se reproduce acá tal cual: dos AAL de strike $13 que vencían en 9
días con la acción en $12.92.

Lo que se afirma:
  · los símbolos OCC salen de la cadena, NUNCA se arman a mano;
  · los precios son ejecutables: se recompra al ASK y se vende al BID, nunca al mid;
  · a débito nunca, ni siquiera cuando es la única salida;
  · el detector PROPONE: no manda ni aprueba nada;
  · cada descarte viene con su motivo en palabras.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from options_advisor.broker.models import Greeks, OptionChain, OptionContract
from options_advisor.config import RollSettings
from options_advisor.execution import roll_engine
from options_advisor.execution.roll_engine import Descarte, Posicion, Propuesta
from options_advisor.storage import db
from options_advisor.storage import repository as repo

HOY = date(2026, 9, 9)
VIEJA = date(2026, 9, 18)          # 9 días
POSICION = Posicion(open_order_id=1, symbol="AAL", strike=13.0, expiration=VIEJA, contracts=2)


def _cfg(**kw) -> RollSettings:
    base = dict(enabled=True, dte_trigger=20, solo_itm=True, max_dte=40, max_rolls=2)
    base.update(kw)
    return RollSettings(**base)


def _contrato(expiration: date, *, strike=13.0, bid=0.0, ask=0.0, occ="X", tipo="put") -> OptionContract:
    return OptionContract(
        symbol="AAL", occ_symbol=occ, option_type=tipo, strike=strike, expiration=expiration,
        bid=bid, ask=ask, last_price=(bid + ask) / 2 or 0.01, implied_volatility=0.45,
        open_interest=1000, volume=100,
        greeks=Greeks(delta=-0.5, gamma=0.1, theta=-0.02, vega=0.05, rho=0.0, source="calculated"),
    )


def _cadena(contratos, *, spot=12.92) -> OptionChain:
    return OptionChain(symbol="AAL", as_of=HOY, underlying_price=spot, contracts=contratos)


# El escenario de referencia: recomprar el de septiembre cuesta $0.65 (ask), y hay tres
# vencimientos más adelante que pagan distinto.
CADENA_BASE = _cadena([
    _contrato(VIEJA, ask=0.65, bid=0.60, occ="AAL   260918P00013000"),
    _contrato(date(2026, 9, 25), bid=0.72, ask=0.80, occ="AAL   260925P00013000"),   # +7 días
    _contrato(date(2026, 10, 16), bid=1.00, ask=1.10, occ="AAL   261016P00013000"),  # +28 días
])


def test_propone_el_de_mejor_credito_por_dia():
    """+$0.07 en 7 días = $0.010/día · +$0.35 en 28 días = $0.0125/día. Gana el mensual, no por
    pagar más dólares sino por pagar mejor POR DÍA — que es la vara que pidió el usuario."""
    r = roll_engine.evaluar_posicion(POSICION, CADENA_BASE, hoy=HOY, rolls_hechos=0, cfg=_cfg())
    assert isinstance(r, Propuesta)
    assert r.candidato.expiration == date(2026, 10, 16)
    assert r.candidato.credito_neto == pytest.approx(0.35)
    assert r.candidato.dias_agregados == 28
    assert r.costo_recompra == pytest.approx(0.65)
    assert r.prima_nueva == pytest.approx(1.00)


def test_el_semanal_gana_cuando_paga_mejor_por_dia():
    """"Roll semanal si paga, si no mensual". Con el semanal pagando $1.05 el crédito por día lo
    pone adelante, y se elige ese: menos tiempo atado a la misma apuesta."""
    cadena = _cadena([
        _contrato(VIEJA, ask=0.65, bid=0.60, occ="V"),
        _contrato(date(2026, 9, 25), bid=1.05, ask=1.10, occ="S"),    # +$0.40 en 7d = $0.057/día
        _contrato(date(2026, 10, 16), bid=1.50, ask=1.60, occ="M"),   # +$0.85 en 28d = $0.030/día
    ])
    r = roll_engine.evaluar_posicion(POSICION, cadena, hoy=HOY, rolls_hechos=0, cfg=_cfg())
    assert isinstance(r, Propuesta)
    assert r.candidato.expiration == date(2026, 9, 25)
    assert r.occ_nuevo == "S"


def test_usa_el_ask_para_recomprar_y_el_bid_para_vender():
    """La lección del 02/09, que costó $295. Al mid este roll parecería pagar $0.50; ejecutable
    paga $0.35. Si con los números reales no paga, no paga."""
    r = roll_engine.evaluar_posicion(POSICION, CADENA_BASE, hoy=HOY, rolls_hechos=0, cfg=_cfg())
    assert isinstance(r, Propuesta)
    assert r.costo_recompra == pytest.approx(0.65)     # el ASK del viejo, no su mid (0.625)
    assert r.prima_nueva == pytest.approx(1.00)        # el BID del nuevo, no su mid (1.05)


def test_los_occ_salen_de_la_cadena_tal_cual():
    r = roll_engine.evaluar_posicion(POSICION, CADENA_BASE, hoy=HOY, rolls_hechos=0, cfg=_cfg())
    assert r.occ_viejo == "AAL   260918P00013000"
    assert r.occ_nuevo == "AAL   261016P00013000"


def test_sin_occ_no_se_propone():
    """Armar el símbolo a mano es el error más caro que puede cometer este sistema."""
    cadena = _cadena([
        _contrato(VIEJA, ask=0.65, bid=0.60, occ=None),
        _contrato(date(2026, 10, 16), bid=1.00, ask=1.10, occ="M"),
    ])
    r = roll_engine.evaluar_posicion(POSICION, cadena, hoy=HOY, rolls_hechos=0, cfg=_cfg())
    assert isinstance(r, Descarte)
    assert "OCC" in r.motivo


def test_a_debito_nunca():
    """Recomprar cuesta $2.00 y lo más lejos paga $1.20: el roll costaría plata. No se propone, y
    el motivo dice cuánto costaría, para que el usuario decida con el número a la vista."""
    cadena = _cadena([
        _contrato(VIEJA, ask=2.00, bid=1.90, occ="V"),
        _contrato(date(2026, 10, 16), bid=1.20, ask=1.30, occ="M"),
    ])
    r = roll_engine.evaluar_posicion(POSICION, cadena, hoy=HOY, rolls_hechos=0, cfg=_cfg())
    assert isinstance(r, Descarte)
    assert "débito" in r.motivo


def test_no_se_pasa_de_max_dte():
    """El único que paga vence en 70 días. Fuera de los 40, no se propone."""
    cadena = _cadena([
        _contrato(VIEJA, ask=0.65, bid=0.60, occ="V"),
        _contrato(date(2026, 11, 18), bid=2.00, ask=2.10, occ="L"),   # +70 días
    ])
    r = roll_engine.evaluar_posicion(POSICION, cadena, hoy=HOY, rolls_hechos=0, cfg=_cfg())
    assert isinstance(r, Descarte)
    assert "40" in r.motivo


def test_fuera_de_la_ventana_de_dias_no_se_toca():
    lejos = Posicion(1, "AAL", 13.0, date(2026, 11, 20), 2)          # 72 días
    r = roll_engine.evaluar_posicion(lejos, CADENA_BASE, hoy=HOY, rolls_hechos=0, cfg=_cfg())
    assert isinstance(r, Descarte)
    assert "72" in r.motivo


def test_si_esta_otm_no_hay_nada_que_rolear():
    """La acción por encima del strike: el put vence sin valor y rolearlo sería regalar la ganancia."""
    r = roll_engine.evaluar_posicion(
        POSICION, _cadena(CADENA_BASE.contracts, spot=14.50), hoy=HOY, rolls_hechos=0, cfg=_cfg())
    assert isinstance(r, Descarte)
    assert "vence sin valor" in r.motivo


def test_el_tope_de_rolls_frena_y_lo_explica():
    r = roll_engine.evaluar_posicion(POSICION, CADENA_BASE, hoy=HOY, rolls_hechos=2, cfg=_cfg())
    assert isinstance(r, Descarte)
    assert "tope" in r.motivo and "vos" in r.motivo


def test_apagado_no_propone_nada():
    r = roll_engine.evaluar_posicion(POSICION, CADENA_BASE, hoy=HOY, rolls_hechos=0,
                                     cfg=_cfg(enabled=False))
    assert isinstance(r, Descarte)
    assert "apagado" in r.motivo


def test_un_vencimiento_sin_bid_no_es_candidato():
    """Sin comprador no hay crédito que cobrar, por más que el ask diga otra cosa."""
    cadena = _cadena([
        _contrato(VIEJA, ask=0.65, bid=0.60, occ="V"),
        _contrato(date(2026, 9, 25), bid=0.0, ask=3.00, occ="S"),
        _contrato(date(2026, 10, 16), bid=1.00, ask=1.10, occ="M"),
    ])
    r = roll_engine.evaluar_posicion(POSICION, cadena, hoy=HOY, rolls_hechos=0, cfg=_cfg())
    assert isinstance(r, Propuesta)
    assert r.occ_nuevo == "M"


def test_el_strike_no_cambia_nunca():
    """Regla 1 del usuario. Un strike más bajo pagaría más, y el detector ni lo mira."""
    cadena = _cadena([
        _contrato(VIEJA, ask=0.65, bid=0.60, occ="V"),
        _contrato(date(2026, 10, 16), strike=12.0, bid=5.00, ask=5.10, occ="OTRO"),
        _contrato(date(2026, 10, 16), bid=1.00, ask=1.10, occ="M"),
    ])
    r = roll_engine.evaluar_posicion(POSICION, cadena, hoy=HOY, rolls_hechos=0, cfg=_cfg())
    assert isinstance(r, Propuesta)
    assert r.occ_nuevo == "M"
    assert r.posicion.strike == 13.0


def test_los_calls_no_entran():
    cadena = _cadena([
        _contrato(VIEJA, ask=0.65, bid=0.60, occ="V"),
        _contrato(date(2026, 10, 16), bid=9.00, ask=9.10, occ="CALL", tipo="call"),
        _contrato(date(2026, 10, 16), bid=1.00, ask=1.10, occ="M"),
    ])
    r = roll_engine.evaluar_posicion(POSICION, cadena, hoy=HOY, rolls_hechos=0, cfg=_cfg())
    assert r.occ_nuevo == "M"


# ───────────────────── la pasada completa, con base de datos ─────────────────────


@dataclass
class BrokerFalso:
    cadena: OptionChain | None = None
    error: Exception | None = None
    llamadas: int = 0

    def get_option_chain(self, symbol, expiration_range_days=(7, 60)):
        self.llamadas += 1
        if self.error:
            raise self.error
        return self.cadena


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _abrir_posicion(conn, *, symbol="AAL", strike=13.0, expiration=VIEJA, contratos=2) -> int:
    cur = conn.execute(
        "INSERT INTO live_order_log (log_date, log_ts, symbol, action, strike, expiration, "
        "approved, final_contracts, dry_run, sent, order_status, fill_price, filled_contracts) "
        "VALUES (?, ?, ?, 'SELL_TO_OPEN', ?, ?, 1, ?, 0, 1, 'FILLED', 1.20, ?)",
        ("2026-08-20", "2026-08-20T10:00:00", symbol, strike, expiration.isoformat(),
         contratos, contratos),
    )
    conn.commit()
    return cur.lastrowid


def test_la_pasada_guarda_la_propuesta_y_no_manda_nada(conn):
    oid = _abrir_posicion(conn)
    broker = BrokerFalso(cadena=CADENA_BASE)
    res = roll_engine.detectar_rolls(conn, broker, _cfg(), hoy=HOY)
    assert len(res["propuestas"]) == 1
    fila = repo.get_roll_proposal(conn, res["propuestas"][0])
    assert fila["status"] == "pendiente"           # NADIE aprobó nada
    assert fila["schwab_order_id"] is None         # el broker no vio ninguna orden
    assert fila["open_order_id"] == oid
    assert fila["occ_nuevo"] == "AAL   261016P00013000"
    assert fila["contracts"] == 2


def test_la_segunda_pasada_no_duplica_la_propuesta(conn):
    _abrir_posicion(conn)
    broker = BrokerFalso(cadena=CADENA_BASE)
    roll_engine.detectar_rolls(conn, broker, _cfg(), hoy=HOY)
    res = roll_engine.detectar_rolls(conn, broker, _cfg(), hoy=HOY)
    assert res["propuestas"] == []
    assert len(repo.get_roll_proposals(conn)) == 1


def test_apagado_ni_le_pega_al_broker(conn):
    """Con el roll apagado no se gasta una sola llamada a Schwab."""
    _abrir_posicion(conn)
    broker = BrokerFalso(cadena=CADENA_BASE)
    res = roll_engine.detectar_rolls(conn, broker, _cfg(enabled=False), hoy=HOY)
    assert broker.llamadas == 0
    assert res["propuestas"] == []


def test_una_posicion_lejos_del_vencimiento_no_gasta_una_llamada(conn):
    _abrir_posicion(conn, expiration=date(2026, 12, 18))
    broker = BrokerFalso(cadena=CADENA_BASE)
    roll_engine.detectar_rolls(conn, broker, _cfg(), hoy=HOY)
    assert broker.llamadas == 0


def test_si_falla_la_cadena_la_pasada_sigue_viva(conn):
    """Un símbolo que falla no puede tumbar la revisión de los demás."""
    _abrir_posicion(conn)
    broker = BrokerFalso(error=RuntimeError("502 de Schwab"))
    res = roll_engine.detectar_rolls(conn, broker, _cfg(), hoy=HOY)
    assert res["errores"] and "502" in res["errores"][0][1]
    assert res["propuestas"] == []


def test_la_pasada_vence_lo_de_ayer(conn):
    oid = _abrir_posicion(conn)
    from datetime import datetime as _dt
    viejo = repo.insert_roll_proposal(
        conn, open_order_id=oid, symbol="AAL", strike=13.0, contracts=2,
        expiration_vieja=VIEJA, expiration_nueva=date(2026, 10, 16), dte_viejo=10, dte_nuevo=38,
        dias_agregados=28, occ_viejo="V", occ_nuevo="M", costo_recompra=0.65, prima_nueva=1.0,
        credito_neto=0.35, credito_por_dia=0.0125, spot=12.9, motivo="de ayer",
        now=_dt(2026, 9, 8, 11, 0),
    )
    res = roll_engine.detectar_rolls(conn, BrokerFalso(cadena=CADENA_BASE), _cfg(), hoy=HOY)
    assert res["vencidas"] == 1
    assert repo.get_roll_proposal(conn, viejo)["status"] == "vencida"
    assert len(res["propuestas"]) == 1, "vencida la de ayer, se puede proponer de nuevo hoy"


def test_una_posicion_cerrada_no_se_rolea(conn):
    oid = _abrir_posicion(conn)
    conn.execute("UPDATE live_order_log SET closed = 1 WHERE id = ?", (oid,))
    conn.commit()
    res = roll_engine.detectar_rolls(conn, BrokerFalso(cadena=CADENA_BASE), _cfg(), hoy=HOY)
    assert res["propuestas"] == []


def test_el_descarte_llega_con_motivo(conn):
    """El usuario tiene que poder ver POR QUÉ no se propuso — sin eso vuelve el "¿por qué no abrió?"."""
    _abrir_posicion(conn)
    cadena = _cadena([
        _contrato(VIEJA, ask=2.00, bid=1.90, occ="V"),
        _contrato(date(2026, 10, 16), bid=1.20, ask=1.30, occ="M"),
    ])
    res = roll_engine.detectar_rolls(conn, BrokerFalso(cadena=cadena), _cfg(), hoy=HOY)
    assert res["propuestas"] == []
    assert res["descartes"] and "débito" in res["descartes"][0][1]


# ───────── una posición recién rolada descansa hasta mañana (2026-09-15) ─────────


def test_no_se_rolea_dos_veces_el_mismo_dia(conn):
    """Rolear dos veces en un día es pagar el spread dos veces. El 15/09 AAL se roleó a las 12:12
    y de nuevo a las 12:18: el segundo salto cobró $0.19 donde ir directo pagaba $0.38."""
    cur = conn.execute(
        "INSERT INTO live_order_log (log_date, log_ts, symbol, action, strike, expiration, "
        "approved, final_contracts, dry_run, sent, order_status, fill_price, filled_contracts, "
        "roll_of) VALUES (?, ?, 'AAL', 'SELL_TO_OPEN', 13.0, ?, 1, 2, 0, 1, 'FILLED', 1.2, 2, 99)",
        (HOY.isoformat(), f"{HOY.isoformat()}T12:12:00", VIEJA.isoformat()),
    )
    conn.commit()
    broker = BrokerFalso(cadena=CADENA_BASE)
    res = roll_engine.detectar_rolls(conn, broker, _cfg(), hoy=HOY)
    assert res["propuestas"] == []
    assert any("ya se roleó hoy" in m for _, m in res["descartes"])
    assert broker.llamadas == 0, "ni siquiera le pide la cadena a Schwab"


def test_la_de_ayer_si_se_puede_volver_a_rolear(conn):
    from datetime import timedelta
    ayer = (HOY - timedelta(days=1)).isoformat()
    conn.execute(
        "INSERT INTO live_order_log (log_date, log_ts, symbol, action, strike, expiration, "
        "approved, final_contracts, dry_run, sent, order_status, fill_price, filled_contracts, "
        "roll_of) VALUES (?, ?, 'AAL', 'SELL_TO_OPEN', 13.0, ?, 1, 2, 0, 1, 'FILLED', 1.2, 2, 99)",
        (ayer, f"{ayer}T12:12:00", VIEJA.isoformat()),
    )
    conn.commit()
    res = roll_engine.detectar_rolls(conn, BrokerFalso(cadena=CADENA_BASE), _cfg(), hoy=HOY)
    assert len(res["propuestas"]) == 1
