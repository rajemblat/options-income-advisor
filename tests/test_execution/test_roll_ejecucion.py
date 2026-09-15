"""Ejecutar un roll aprobado: qué se manda, qué se anota y qué frena (usuario 2026-09-14/15).

Esto es lo que mueve plata de verdad, así que lo que se afirma acá es corto y concreto:
  · se manda UNA orden combinada, a crédito, con los OCC guardados y al precio APROBADO;
  · el precio no se camina: el usuario aprobó un número y se ejecuta ese;
  · si llena, el libro queda cerrado de un lado y abierto del otro, sin agujeros;
  · la posición nueva NO gasta el cupo del día — el roll es defensivo;
  · el kill switch, el mercado cerrado y la máquina equivocada frenan todo;
  · y si no llena, se cancela: una orden de hoy no se ejecuta mañana.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pytest

from options_advisor.config import Settings, load_settings
from options_advisor.execution import roll_engine
from options_advisor.storage import db
from options_advisor.storage import repository as repo

HOY = date(2026, 9, 15)
AHORA = datetime(2026, 9, 15, 11, 30)
VIEJA = date(2026, 9, 18)
NUEVA = date(2026, 10, 16)


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture()
def settings() -> Settings:
    s = load_settings()
    s.roll.enabled = True
    s.live_trading.enabled = True
    s.live_trading.real_machine_hostname = ""      # sin candado de máquina en los tests
    return s


@dataclass
class BrokerFalso:
    estados: list[str] = field(default_factory=lambda: ["FILLED"])
    info_extra: dict = field(default_factory=dict)
    ordenes: list[dict] = field(default_factory=list)
    canceladas: list[str] = field(default_factory=list)
    falla_al_mandar: Exception | None = None

    def resolve_account_hash(self, numero=None):
        return "HASH123"

    def place_order(self, account_hash, payload):
        if self.falla_al_mandar:
            raise self.falla_al_mandar
        self.ordenes.append(payload)
        return "OID-1"

    def get_order(self, account_hash, order_id):
        estado = self.estados[0] if len(self.estados) == 1 else self.estados.pop(0)
        return {"status": estado, **self.info_extra}

    def cancel_order(self, account_hash, order_id):
        self.canceladas.append(order_id)


def _abrir(conn, *, strike=13.0, contratos=2, fill=1.20, colateral=2600.0) -> int:
    cur = conn.execute(
        "INSERT INTO live_order_log (log_date, log_ts, symbol, action, strike, expiration, approved,"
        " final_contracts, collateral, dry_run, sent, order_status, fill_price, filled_contracts) "
        "VALUES ('2026-08-20', '2026-08-20T10:00:00', 'AAL', 'SELL_TO_OPEN', ?, ?, 1, ?, ?, 0, 1, "
        "'FILLED', ?, ?)",
        (strike, VIEJA.isoformat(), contratos, colateral, fill, contratos),
    )
    conn.commit()
    return cur.lastrowid


def _proponer_y_aprobar(conn, open_id, *, credito=0.35, contratos=2) -> int:
    pid = repo.insert_roll_proposal(
        conn, open_order_id=open_id, symbol="AAL", strike=13.0, contracts=contratos,
        expiration_vieja=VIEJA, expiration_nueva=NUEVA, dte_viejo=3, dte_nuevo=31,
        dias_agregados=28, occ_viejo="AAL   260918P00013000", occ_nuevo="AAL   261016P00013000",
        costo_recompra=0.65, prima_nueva=round(0.65 + credito, 4), credito_neto=credito,
        credito_por_dia=round(credito / 28, 6), spot=12.92, motivo="ITM a 3 días",
        candidatos=[{"expiration": NUEVA.isoformat(), "dte": 31, "dias_agregados": 28,
                     "credito_neto": credito, "credito_total": credito * 100,
                     "credito_por_dia": credito / 28, "prima_nueva": 0.65 + credito,
                     "occ": "AAL   261016P00013000", "recomendado": True}],
        now=AHORA,
    )
    repo.aprobar_roll(conn, pid, now=AHORA)
    return pid


def _ejecutar(conn, broker, settings):
    return roll_engine.ejecutar_rolls_aprobados(
        conn, broker, settings, now=AHORA, espera_segundos=4, sleep=lambda s: None,
        clock=iter([0, 1, 2, 3, 4, 5, 6]).__next__,
    )


# ───────────────────────── la orden que se manda ─────────────────────────


def test_manda_una_sola_orden_combinada_a_credito(conn, settings, monkeypatch):
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda *a, **k: "abierto")
    pid = _proponer_y_aprobar(conn, _abrir(conn))
    broker = BrokerFalso()
    _ejecutar(conn, broker, settings)

    assert len(broker.ordenes) == 1, "una sola orden: nunca la recompra y la venta por separado"
    orden = broker.ordenes[0]
    assert orden["orderType"] == "NET_CREDIT"
    assert orden["price"] == "0.35", "se manda el precio APROBADO, no uno caminado"
    patas = orden["orderLegCollection"]
    assert len(patas) == 2
    assert patas[0]["instruction"] == "BUY_TO_CLOSE"
    assert patas[0]["instrument"]["symbol"] == "AAL   260918P00013000"
    assert patas[1]["instruction"] == "SELL_TO_OPEN"
    assert patas[1]["instrument"]["symbol"] == "AAL   261016P00013000"
    assert repo.get_roll_proposal(conn, pid)["status"] == "enviada"


def test_si_llena_el_libro_queda_cerrado_de_un_lado_y_abierto_del_otro(conn, settings, monkeypatch):
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda *a, **k: "abierto")
    viejo = _abrir(conn)
    _proponer_y_aprobar(conn, viejo)
    _ejecutar(conn, BrokerFalso(), settings)

    cerrada = conn.execute("SELECT * FROM live_order_log WHERE id = ?", (viejo,)).fetchone()
    assert cerrada["closed"] == 1
    assert cerrada["close_reason"] == "roll"
    assert cerrada["close_fill_price"] == pytest.approx(0.65)
    assert cerrada["realized_pnl"] == pytest.approx((1.20 - 0.65) * 100 * 2)

    abiertas = repo.get_open_real_put_positions(conn)
    assert len(abiertas) == 1
    nueva = abiertas[0]
    assert nueva["id"] != viejo
    assert nueva["expiration"] == NUEVA.isoformat()
    assert nueva["strike"] == 13.0, "el strike NO cambia"
    assert nueva["filled_contracts"] == 2
    assert nueva["fill_price"] == pytest.approx(1.00)      # 0.65 de recompra + 0.35 de crédito
    assert nueva["roll_of"] == viejo


def test_la_posicion_nueva_no_gasta_el_cupo_del_dia(conn, settings, monkeypatch):
    """Usuario 2026-09-14: el roll es defensivo, no riesgo nuevo. Si gastara cupo, rolear le sacaría
    al robot su orden del día — al revés de lo que el roll intenta hacer."""
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda *a, **k: "abierto")
    antes = repo.count_live_approved_opens_today(conn, HOY)
    _proponer_y_aprobar(conn, _abrir(conn))
    _ejecutar(conn, BrokerFalso(), settings)
    assert repo.count_live_approved_opens_today(conn, HOY) == antes
    assert repo.count_live_approved_opens_this_week(conn, HOY) == antes


def test_se_anota_el_credito_real_no_el_limite(conn, settings, monkeypatch):
    """El 03/09 el condor se mandó a $1.65 y Schwab llenó a $1.75. El precio de ejecución no se
    estima: se pregunta."""
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda *a, **k: "abierto")
    pid = _proponer_y_aprobar(conn, _abrir(conn))
    broker = BrokerFalso(info_extra={
        "orderLegCollection": [{"legId": 1, "instruction": "BUY_TO_CLOSE"},
                               {"legId": 2, "instruction": "SELL_TO_OPEN"}],
        "orderActivityCollection": [{"executionLegs": [
            {"legId": 1, "price": 0.60, "quantity": 2},
            {"legId": 2, "price": 1.05, "quantity": 2},
        ]}],
    })
    _ejecutar(conn, broker, settings)
    assert repo.get_roll_proposal(conn, pid)["credito_real"] == pytest.approx(0.45)


def test_sin_ejecuciones_publicadas_se_usa_el_limite_y_se_dice(conn, settings, monkeypatch):
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda *a, **k: "abierto")
    pid = _proponer_y_aprobar(conn, _abrir(conn))
    _ejecutar(conn, BrokerFalso(), settings)
    fila = repo.get_roll_proposal(conn, pid)
    assert fila["credito_real"] == pytest.approx(0.35)
    assert "limite" in fila["result_note"]


def test_si_no_llena_se_cancela(conn, settings, monkeypatch):
    """Dejarla puesta significaría ejecutar mañana, con otro mercado, algo que se aprobó hoy."""
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda *a, **k: "abierto")
    viejo = _abrir(conn)
    pid = _proponer_y_aprobar(conn, viejo)
    broker = BrokerFalso(estados=["WORKING"])
    _ejecutar(conn, broker, settings)
    assert broker.canceladas == ["OID-1"]
    assert repo.get_roll_proposal(conn, pid)["status"] == "error"
    assert conn.execute("SELECT closed FROM live_order_log WHERE id = ?", (viejo,)).fetchone()[0] in (0, None)


def test_si_schwab_la_rechaza_queda_el_motivo(conn, settings, monkeypatch):
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda *a, **k: "abierto")
    pid = _proponer_y_aprobar(conn, _abrir(conn))
    broker = BrokerFalso(estados=["REJECTED"],
                         info_extra={"statusDescription": "This order may result in an oversold position"})
    _ejecutar(conn, broker, settings)
    fila = repo.get_roll_proposal(conn, pid)
    assert fila["status"] == "error"
    assert "REJECTED" in fila["result_note"] and "oversold" in fila["result_note"]


# ───────────────────────── los frenos ─────────────────────────


def test_el_kill_switch_frena(conn, settings, monkeypatch):
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda *a, **k: "abierto")
    pid = _proponer_y_aprobar(conn, _abrir(conn))
    repo.set_live_kill_switch(conn, True)
    broker = BrokerFalso()
    res = _ejecutar(conn, broker, settings)
    assert broker.ordenes == []
    assert "kill switch" in res["freno"]
    assert repo.get_roll_proposal(conn, pid)["status"] == "aprobada", "sigue aprobada, no se pierde"


def test_con_el_mercado_cerrado_no_se_manda(conn, settings, monkeypatch):
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda *a, **k: "cerrado")
    _proponer_y_aprobar(conn, _abrir(conn))
    broker = BrokerFalso()
    res = _ejecutar(conn, broker, settings)
    assert broker.ordenes == []
    assert "cerrado" in res["freno"]


def test_en_otra_maquina_no_se_manda(conn, settings, monkeypatch):
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda *a, **k: "abierto")
    settings.live_trading.real_machine_hostname = "otra-maquina-que-no-existe"
    _proponer_y_aprobar(conn, _abrir(conn))
    broker = BrokerFalso()
    res = _ejecutar(conn, broker, settings)
    assert broker.ordenes == []
    assert "máquina" in res["freno"]


def test_con_el_roll_apagado_no_se_manda(conn, settings, monkeypatch):
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda *a, **k: "abierto")
    settings.roll.enabled = False
    _proponer_y_aprobar(conn, _abrir(conn))
    broker = BrokerFalso()
    res = _ejecutar(conn, broker, settings)
    assert broker.ordenes == []
    assert "apagado" in res["freno"]


def test_una_pendiente_no_se_manda_sola(conn, settings, monkeypatch):
    """Sin el clic del usuario no sale nada. Es toda la premisa del diseño."""
    monkeypatch.setattr("options_advisor.scheduler.market_calendar.market_session", lambda *a, **k: "abierto")
    repo.insert_roll_proposal(
        conn, open_order_id=_abrir(conn), symbol="AAL", strike=13.0, contracts=2,
        expiration_vieja=VIEJA, expiration_nueva=NUEVA, dte_viejo=3, dte_nuevo=31,
        dias_agregados=28, occ_viejo="V", occ_nuevo="N", costo_recompra=0.65, prima_nueva=1.0,
        credito_neto=0.35, credito_por_dia=0.0125, spot=12.9, motivo="x", now=AHORA,
    )
    broker = BrokerFalso()
    _ejecutar(conn, broker, settings)
    assert broker.ordenes == []


# ───────────────────────── el menú ─────────────────────────


def test_el_menu_se_guarda_y_se_puede_elegir_otro(conn):
    oid = _abrir(conn)
    menu = [
        {"expiration": "2026-10-16", "dte": 31, "dias_agregados": 28, "credito_neto": 0.35,
         "credito_total": 35.0, "credito_por_dia": 0.0125, "prima_nueva": 1.00,
         "occ": "OCT", "recomendado": True},
        {"expiration": "2026-12-18", "dte": 94, "dias_agregados": 91, "credito_neto": 0.90,
         "credito_total": 90.0, "credito_por_dia": 0.0099, "prima_nueva": 1.55,
         "occ": "DIC", "recomendado": False},
    ]
    pid = repo.insert_roll_proposal(
        conn, open_order_id=oid, symbol="AAL", strike=13.0, contracts=2,
        expiration_vieja=VIEJA, expiration_nueva=date(2026, 10, 16), dte_viejo=3, dte_nuevo=31,
        dias_agregados=28, occ_viejo="V", occ_nuevo="OCT", costo_recompra=0.65, prima_nueva=1.0,
        credito_neto=0.35, credito_por_dia=0.0125, spot=12.9, motivo="x", candidatos=menu, now=AHORA,
    )
    assert len(repo.candidatos_del_roll(conn, pid)) == 2

    assert repo.elegir_candidato_del_roll(conn, pid, "2026-12-18") is True
    fila = repo.get_roll_proposal(conn, pid)
    assert fila["expiration_nueva"] == "2026-12-18"
    assert fila["occ_nuevo"] == "DIC"
    assert fila["credito_neto"] == pytest.approx(0.90)
    assert fila["prima_nueva"] == pytest.approx(1.55)
    assert fila["dias_agregados"] == 91


def test_no_se_puede_elegir_un_vencimiento_que_no_esta_en_el_menu(conn):
    """Un vencimiento fuera del menú no pasó por las reglas: ni crédito > 0 ni el tope de días."""
    pid = repo.insert_roll_proposal(
        conn, open_order_id=_abrir(conn), symbol="AAL", strike=13.0, contracts=2,
        expiration_vieja=VIEJA, expiration_nueva=NUEVA, dte_viejo=3, dte_nuevo=31,
        dias_agregados=28, occ_viejo="V", occ_nuevo="OCT", costo_recompra=0.65, prima_nueva=1.0,
        credito_neto=0.35, credito_por_dia=0.0125, spot=12.9, motivo="x",
        candidatos=[{"expiration": "2026-10-16", "dte": 31, "dias_agregados": 28,
                     "credito_neto": 0.35, "credito_total": 35.0, "credito_por_dia": 0.0125,
                     "prima_nueva": 1.0, "occ": "OCT", "recomendado": True}],
        now=AHORA,
    )
    assert repo.elegir_candidato_del_roll(conn, pid, "2027-01-15") is False
    assert repo.get_roll_proposal(conn, pid)["expiration_nueva"] == NUEVA.isoformat()


def test_una_aprobada_ya_no_se_puede_cambiar(conn):
    pid = _proponer_y_aprobar(conn, _abrir(conn))
    assert repo.elegir_candidato_del_roll(conn, pid, NUEVA.isoformat()) is False
