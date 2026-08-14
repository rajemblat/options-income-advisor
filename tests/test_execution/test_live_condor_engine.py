"""Tests de las COMPUERTAS del motor de Iron Condor real (execution/live_condor_engine) y de los
ayudantes de escalera de precio. Lo crítico: con dinero real, NADA se manda salvo que TODO esté prendido
(sistema real ON, condor live_enabled, sin kill, autorizado hoy, con cupo). El broker acá EXPLOTA si lo
tocan — así el test falla fuerte si el motor intenta operar cuando no debe."""

from __future__ import annotations

import tempfile
from datetime import date

import pytest

from options_advisor.config import load_settings
from options_advisor.execution import live_condor_engine as lce
from options_advisor.storage import db, repository as repo


class ExplodingBroker:
    """Cualquier acceso a la red = fallo del test (no debería tocarse si las compuertas frenan)."""
    def place_order(self, *a, **k):
        raise AssertionError("place_order NO debería llamarse con las compuertas cerradas")

    def resolve_account_hash(self, *a, **k):
        raise AssertionError("resolve_account_hash NO debería llamarse")

    def get_intraday_bars(self, *a, **k):
        raise AssertionError("get_intraday_bars NO debería llamarse")

    def get_option_chain(self, *a, **k):
        raise AssertionError("get_option_chain NO debería llamarse")


def _conn():
    return db.connect(tempfile.mktemp(suffix=".db"))


def _force(s, *, condor_live, real_enabled, dry_run):
    object.__setattr__(s.intraday_condor, "live_enabled", condor_live)
    object.__setattr__(s.live_trading, "enabled", real_enabled)
    object.__setattr__(s.live_trading, "dry_run", dry_run)
    return s


def test_off_by_default_never_touches_broker():
    """Con el condor real APAGADO (live_enabled=false), el ciclo real es un no-op total."""
    conn = _conn()
    s = _force(load_settings(), condor_live=False, real_enabled=True, dry_run=False)
    lce.process_real_condor_cycle(conn, ExplodingBroker(), s, date.today())  # no debe explotar


def test_master_off_never_touches_broker():
    """Aun con el condor live_enabled=True, si el trading real está en dry-run o apagado, no opera."""
    conn = _conn()
    s = _force(load_settings(), condor_live=True, real_enabled=False, dry_run=True)
    lce.process_real_condor_cycle(conn, ExplodingBroker(), s, date.today())


def test_condor_active_helper_requires_all_masters():
    conn = _conn()
    # Condor apagado → inactivo, pase lo que pase con el master.
    assert lce._real_condor_active(conn, _force(load_settings(), condor_live=False, real_enabled=True, dry_run=False)) is False
    # Condor prendido pero master en dry-run → inactivo.
    assert lce._real_condor_active(conn, _force(load_settings(), condor_live=True, real_enabled=True, dry_run=True)) is False
    # Condor prendido pero master apagado → inactivo.
    assert lce._real_condor_active(conn, _force(load_settings(), condor_live=True, real_enabled=False, dry_run=False)) is False
    # TODO prendido (y sin kill switch) → activo.
    assert lce._real_condor_active(conn, _force(load_settings(), condor_live=True, real_enabled=True, dry_run=False)) is True


def test_open_credit_ladder_starts_high_walks_to_mid():
    class C:
        def __init__(self, bid, ask): self.bid, self.ask = bid, ask
    # short put/call ricos, alas más baratas → crédito neto positivo.
    sp, sc = C(3.0, 3.4), C(3.0, 3.4)
    lp, lc = C(1.0, 1.2), C(1.0, 1.2)
    ladder = lce._open_credit_ladder(sp, sc, lp, lc)
    assert ladder, "debería haber escalera con crédito positivo"
    # Arranca pidiendo MÁS crédito y baja: primer peldaño >= último (mid).
    assert ladder[0] >= ladder[-1]
    assert min(ladder) > 0


def test_close_debit_ladder_has_floor():
    class C:
        def __init__(self, bid, ask): self.bid, self.ask = bid, ask
    # Condor casi sin valor → débito ~0; la escalera nunca baja del piso mínimo válido.
    sp, sc = C(0.0, 0.05), C(0.0, 0.05)
    lp, lc = C(0.0, 0.05), C(0.0, 0.05)
    ladder = lce._close_debit_ladder(sp, sc, lp, lc)
    assert ladder
    assert min(ladder) >= lce._MIN_CLOSE_DEBIT
