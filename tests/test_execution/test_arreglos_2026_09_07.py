"""Tres arreglos del 2026-09-07, el día que el robot se mudó al servidor.

1. UNA POSICIÓN VIVA POR SÍMBOLO. El tope que había solo miraba el día en curso, así que el robot
   podía volver al mismo subyacente al día siguiente. El 17 y el 18 de agosto entraron dos AAL 13P
   del MISMO vencimiento y quedaron las dos abiertas: la misma apuesta al doble de tamaño, por
   acumulación y no por decisión. El usuario las vio juntas y en rojo en el dashboard.

2. EL 403 DE FINNHUB NO LLENA MÁS EL LOG. `/calendar/economic` es premium; en el plan free devuelve
   403 SIEMPRE. Ya estaba previsto y el fallback (FOMC + FRED) cubre lo mismo, pero se logueaba
   como WARNING con traceback completo en cada corrida — decenas de tracebacks idénticos por día,
   que es justo el ruido que hace que un error de verdad pase desapercibido.

(El tercero, el objetivo de ganancia medido al precio real de salida, se testea junto al stop en
test_condor_arreglos_2026_09_02.py, que es donde vive esa regla.)
"""

from __future__ import annotations

import logging
from datetime import date

import httpx
import pytest

from options_advisor.market_context import economic_calendar
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 9, 7)


# ── 1. una posición viva por símbolo ────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    return db.connect(":memory:")


def _abrir(conn, symbol: str, dia: str, *, strike: float = 13.0, cerrada: bool = False) -> int:
    """Deja una apertura REAL llenada en el log, como las que quedaron vivas en agosto."""
    cur = conn.execute(
        "INSERT INTO live_order_log (log_date, log_ts, symbol, action, strike, expiration, approved,"
        " final_contracts, dry_run, sent, order_status, fill_price, closed)"
        " VALUES (?, ?, ?, 'SELL_TO_OPEN', ?, '2026-09-18', 1, 1, 0, 1, 'FILLED', 0.22, ?)",
        (dia, f"{dia}T11:28:00", symbol, strike, 1 if cerrada else 0),
    )
    conn.commit()
    return cur.lastrowid


def test_cuenta_las_vivas_de_cualquier_dia_no_solo_las_de_hoy(conn):
    """EL caso de los dos AAL: abiertos en días distintos, los dos vivos."""
    _abrir(conn, "AAL", "2026-08-17")
    _abrir(conn, "AAL", "2026-08-18")
    assert repo.count_open_real_puts_for_symbol(conn, "AAL") == 2
    # El chequeo viejo no las veía: ninguna es de hoy.
    assert repo.has_live_committed_order_for_symbol_today(conn, "AAL", AS_OF) is False


def test_una_cerrada_libera_el_lugar(conn):
    _abrir(conn, "AAL", "2026-08-17", cerrada=True)
    _abrir(conn, "AAL", "2026-08-18")
    assert repo.count_open_real_puts_for_symbol(conn, "AAL") == 1


def test_no_mezcla_simbolos(conn):
    _abrir(conn, "AAL", "2026-08-17")
    _abrir(conn, "DIS", "2026-08-27", strike=100.0)
    assert repo.count_open_real_puts_for_symbol(conn, "AAL") == 1
    assert repo.count_open_real_puts_for_symbol(conn, "COIN") == 0


def test_las_de_papel_no_cuentan(conn):
    conn.execute(
        "INSERT INTO live_order_log (log_date, log_ts, symbol, action, strike, expiration, approved,"
        " final_contracts, dry_run, sent, order_status, fill_price, closed)"
        " VALUES ('2026-08-17', '2026-08-17T11:28:00', 'AAL', 'SELL_TO_OPEN', 13.0, '2026-09-18',"
        " 1, 1, 1, 0, NULL, 0.22, 0)")
    conn.commit()
    assert repo.count_open_real_puts_for_symbol(conn, "AAL") == 0


def test_el_settings_real_trae_el_tope_en_uno():
    """Que la config que se despliega tenga la regla ACTIVA, no solo el código que la soporta."""
    from options_advisor.config import load_settings
    assert load_settings().live_trading.max_open_real_per_symbol == 1


# ── 2. el 403 de Finnhub ────────────────────────────────────────────────────────────────────────

class _Resp403:
    status_code = 403

    def raise_for_status(self):
        raise httpx.HTTPStatusError("403 Forbidden", request=None, response=self)  # type: ignore[arg-type]


class _Resp500:
    status_code = 500

    def raise_for_status(self):
        raise httpx.HTTPStatusError("500", request=None, response=self)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_aviso(monkeypatch):
    monkeypatch.setattr(economic_calendar, "_aviso_403_dado", False)


def test_el_403_avisa_una_sola_vez_y_sin_traceback(monkeypatch, caplog):
    monkeypatch.setattr(economic_calendar.httpx, "get", lambda *a, **k: _Resp403())
    monkeypatch.setattr(economic_calendar.fred_client, "get_upcoming_release_dates",
                        lambda *a, **k: [])
    with caplog.at_level(logging.DEBUG, logger=economic_calendar.__name__):
        for _ in range(5):
            economic_calendar.get_upcoming_macro_events("KEY", None, AS_OF, 30)

    avisos = [r for r in caplog.records if "no está incluido en el plan" in r.message]
    assert len(avisos) == 1, f"Avisó {len(avisos)} veces; antes era una por corrida"
    assert avisos[0].levelno == logging.INFO, "Un 403 esperado no es un WARNING"
    assert all(r.exc_info is None for r in caplog.records), "Sigue escribiendo el traceback"


def test_el_403_no_deja_sin_calendario():
    """Lo importante: el dato no se pierde, lo cubre el fallback de FOMC."""
    eventos = economic_calendar._fomc_dates_fallback(date(2026, 9, 7), 30)
    assert eventos and any("FOMC" in e["event"] for e in eventos)


def test_un_error_de_verdad_se_sigue_viendo(monkeypatch, caplog):
    """Un 500 es transitorio y puede significar algo: ese SÍ se loguea cada vez."""
    monkeypatch.setattr(economic_calendar.httpx, "get", lambda *a, **k: _Resp500())
    monkeypatch.setattr(economic_calendar.fred_client, "get_upcoming_release_dates",
                        lambda *a, **k: [])
    with caplog.at_level(logging.DEBUG, logger=economic_calendar.__name__):
        economic_calendar.get_upcoming_macro_events("KEY", None, AS_OF, 30)
        economic_calendar.get_upcoming_macro_events("KEY", None, AS_OF, 30)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


# ── 1b. el motor de verdad se saltea el símbolo ─────────────────────────────────────────────────

class _Result:
    def __init__(self, contract, premium):
        self.contract, self.premium = contract, premium
        self.passed, self.reasons, self.context = True, [], {}


class _Snap:
    def __init__(self, price):
        self.price, self.snapshot_date = price, AS_OF


class _FakeBroker:
    def resolve_account_hash(self, account_number=None):
        return "HASH"

    def place_order(self, account_hash, payload):
        raise AssertionError("AAL ya tiene una posición viva: no debía mandar otra orden")


def _contrato():
    from options_advisor.broker.models import Greeks, OptionContract
    return OptionContract(
        symbol="AAL", option_type="put", strike=13.0, expiration=date(2026, 10, 16),
        bid=1.30, ask=1.70, last_price=1.50, implied_volatility=0.4, open_interest=1000, volume=500,
        greeks=Greeks(delta=-0.25, gamma=0.01, theta=-0.5, vega=0.1, rho=0.01, source="broker"))


def _settings_real(tope: int):
    from options_advisor.config import load_settings
    s = load_settings()
    lt = s.live_trading.model_copy(update={"enabled": True, "dry_run": False,
                                           "max_open_real_per_symbol": tope})
    return s.model_copy(update={"live_trading": lt})


def test_el_motor_no_abre_un_segundo_AAL_si_ya_hay_uno_vivo(conn):
    """EL test del 07/09, con el motor real: hay un AAL vivo de AGOSTO y hoy vuelve a aparecer AAL
    como oportunidad. Con el tope en 1, no sale ninguna orden."""
    from options_advisor.execution import live_engine
    repo.arm_live_today(conn, AS_OF)
    _abrir(conn, "AAL", "2026-08-17")     # la posición vieja, todavía abierta

    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contrato(), 1.50), _Snap(12.0),
                                     _settings_real(1), AS_OF, broker=_FakeBroker())

    filas = repo.get_live_orders_today(conn, AS_OF)
    assert all(f["sent"] == 0 for f in filas), "Registró una orden que no debía existir"
    # Desde el 2026-09-09 el freno además se explica: antes salía en silencio y el usuario no tenía
    # forma de saber por qué el robot había ignorado una oportunidad. No se opera igual que antes;
    # lo que cambia es que ahora queda dicho.
    assert any("posición(es) real(es) abierta(s) de AAL" in (f["reasons"] or "") for f in filas)


def test_con_el_tope_apagado_se_comporta_como_antes(conn):
    """Con 0 la regla no existe: el robot llega hasta el envío, como hacía en agosto."""
    from options_advisor.execution import live_engine
    repo.arm_live_today(conn, AS_OF)
    _abrir(conn, "AAL", "2026-08-17")

    class _BrokerOk(_FakeBroker):
        def place_order(self, account_hash, payload):
            return "O1"

        def get_order(self, account_hash, order_id):
            return {"status": "FILLED", "filledQuantity": 1,
                    "orderActivityCollection": [{"executionLegs": [{"quantity": 1, "price": 1.55}]}]}

    live_engine.maybe_log_live_order(conn, "AAL", _Result(_contrato(), 1.50), _Snap(12.0),
                                     _settings_real(0), AS_OF, broker=_BrokerOk())
    assert repo.get_live_orders_today(conn, AS_OF), "Con el tope apagado tenía que registrar la orden"
