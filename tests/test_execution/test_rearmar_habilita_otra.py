"""Regla del usuario 2026-09-09: "si puede abrir si yo pongo otra vez armar, que sea asi la regla".

Volver a apretar ARMAR el mismo día es un permiso NUEVO, no una continuación del día. Reinicia los
dos frenos del DÍA — el cupo diario y el freno por racha de stop-loss — contándolos desde ese momento.

Lo que NO reinicia (son límites de plata, no el permiso del día) y está afirmado abajo:
  · el tope SEMANAL de órdenes        · el capital comprometido del día
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from options_advisor.storage import db
from options_advisor.storage import repository as repo

HOY = date(2026, 9, 9)
MANANA_9 = datetime(2026, 9, 9, 9, 40, 0)
MEDIODIA = datetime(2026, 9, 9, 12, 0, 0)
TARDE = datetime(2026, 9, 9, 13, 30, 0)


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _abrir_condor(conn, *, entry_ts: datetime, quantity: int = 1) -> int:
    return repo.insert_real_condor_position(
        conn, underlying="SPX", entry_date=entry_ts.date(), expiration_date=date(2026, 9, 9),
        short_put_strike=6400.0, short_call_strike=6500.0, long_put_strike=6390.0,
        long_call_strike=6510.0, short_put_symbol="SPXW_P6400", long_put_symbol="SPXW_P6390",
        short_call_symbol="SPXW_C6500", long_call_symbol="SPXW_C6510", quantity=quantity,
        entry_net_credit=155.0, max_loss=845.0, max_profit=155.0, lower_breakeven=6398.0,
        upper_breakeven=6502.0, entry_spot=6450.0, open_schwab_order_id="1", status="open",
        entry_ts=entry_ts,
    )


# ─────────────────────────── el cupo del día ───────────────────────────

def test_el_cupo_del_dia_se_cuenta_desde_el_ultimo_armado(conn):
    """Con el cupo en 1: se abre uno, el cupo queda lleno; re-armar lo devuelve a cero."""
    repo.arm_condor_live_today(conn, HOY, now=MANANA_9)
    _, marca = repo.condor_rearm_mark(conn, HOY)
    assert repo.count_real_condor_opens_today(conn, HOY, after_id=marca) == 0

    _abrir_condor(conn, entry_ts=MEDIODIA)
    _, marca = repo.condor_rearm_mark(conn, HOY)
    assert repo.count_real_condor_opens_today(conn, HOY, after_id=marca) == 1   # cupo 1/1 → frenado

    repo.arm_condor_live_today(conn, HOY, now=TARDE)                            # el usuario re-arma
    _, marca = repo.condor_rearm_mark(conn, HOY)
    assert repo.count_real_condor_opens_today(conn, HOY, after_id=marca) == 0   # puede abrir otro
    assert repo.count_real_condor_opens_today(conn, HOY) == 1                   # el día sigue sabiendo


def test_lo_abierto_despues_del_rearmado_si_ocupa_el_cupo_de_nuevo(conn):
    """Re-armar no es barra libre: el condor que se abre DESPUÉS vuelve a llenar el cupo."""
    repo.arm_condor_live_today(conn, HOY, now=MANANA_9)
    _abrir_condor(conn, entry_ts=MEDIODIA)
    repo.arm_condor_live_today(conn, HOY, now=TARDE)
    _abrir_condor(conn, entry_ts=datetime(2026, 9, 9, 13, 45, 0))

    _, marca = repo.condor_rearm_mark(conn, HOY)
    assert repo.count_real_condor_opens_today(conn, HOY, after_id=marca) == 1   # 1/1 otra vez


def test_sin_marca_del_dia_cuenta_todo_como_siempre(conn):
    """Compatibilidad: una marca de AYER no puede borrar el cupo de hoy."""
    repo.arm_condor_live_today(conn, date(2026, 9, 8), now=datetime(2026, 9, 8, 9, 40))
    _abrir_condor(conn, entry_ts=MEDIODIA)
    ts, marca = repo.condor_rearm_mark(conn, HOY)
    assert (ts, marca) == (None, 0)
    assert repo.count_real_condor_opens_today(conn, HOY, after_id=marca) == 1


# ─────────────────── el freno por racha de stop-loss ───────────────────

def test_el_rearmado_limpia_la_racha_de_stop_loss(conn):
    """El freno del día por stop-loss se cuenta desde el re-armado (usuario 2026-09-09)."""
    repo.arm_condor_live_today(conn, HOY, now=MANANA_9)
    pid = _abrir_condor(conn, entry_ts=MANANA_9)
    repo.close_real_condor_position(
        conn, pid, close_date=HOY, close_value=250.0, close_reason="stop_loss",
        realized_pnl=-95.0, close_ts=datetime(2026, 9, 9, 11, 24, 10),
    )
    ts, _ = repo.condor_rearm_mark(conn, HOY)
    assert repo.real_condor_consecutive_stop_losses_today(conn, HOY, since_ts=ts) == 1   # frena

    repo.arm_condor_live_today(conn, HOY, now=TARDE)                                     # re-arma
    ts, _ = repo.condor_rearm_mark(conn, HOY)
    assert repo.real_condor_consecutive_stop_losses_today(conn, HOY, since_ts=ts) == 0   # ya no frena
    assert repo.real_condor_consecutive_stop_losses_today(conn, HOY) == 1                # el día lo recuerda


def test_un_stop_loss_nuevo_vuelve_a_frenar_despues_del_rearmado(conn):
    """Re-armar no apaga el stop: si vuelve a stopear DESPUÉS, frena igual que siempre."""
    repo.arm_condor_live_today(conn, HOY, now=MANANA_9)
    pid1 = _abrir_condor(conn, entry_ts=MANANA_9)
    repo.close_real_condor_position(
        conn, pid1, close_date=HOY, close_value=250.0, close_reason="stop_loss",
        realized_pnl=-95.0, close_ts=datetime(2026, 9, 9, 11, 24, 10),
    )
    repo.arm_condor_live_today(conn, HOY, now=TARDE)
    pid2 = _abrir_condor(conn, entry_ts=datetime(2026, 9, 9, 13, 40))
    repo.close_real_condor_position(
        conn, pid2, close_date=HOY, close_value=260.0, close_reason="stop_loss",
        realized_pnl=-100.0, close_ts=datetime(2026, 9, 9, 15, 10, 0),
    )
    ts, _ = repo.condor_rearm_mark(conn, HOY)
    assert repo.real_condor_consecutive_stop_losses_today(conn, HOY, since_ts=ts) == 1


def test_una_ganancia_posterior_corta_la_racha_igual_que_siempre(conn):
    repo.arm_condor_live_today(conn, HOY, now=MANANA_9)
    pid1 = _abrir_condor(conn, entry_ts=MANANA_9)
    repo.close_real_condor_position(
        conn, pid1, close_date=HOY, close_value=250.0, close_reason="stop_loss",
        realized_pnl=-95.0, close_ts=datetime(2026, 9, 9, 11, 0, 0),
    )
    pid2 = _abrir_condor(conn, entry_ts=datetime(2026, 9, 9, 11, 30))
    repo.close_real_condor_position(
        conn, pid2, close_date=HOY, close_value=40.0, close_reason="take_profit",
        realized_pnl=115.0, close_ts=datetime(2026, 9, 9, 12, 30, 0),
    )
    ts, _ = repo.condor_rearm_mark(conn, HOY)
    assert repo.real_condor_consecutive_stop_losses_today(conn, HOY, since_ts=ts) == 0


# ─────────────────────── los naked (mismo criterio) ───────────────────────

def _log_naked(conn, *, day: date, ts: datetime) -> int:
    return repo.insert_live_order_log(
        conn, log_date=day, log_ts=ts, symbol="WFC", action="SELL_TO_OPEN", strike=70.0,
        expiration="2026-10-17", approved=True, final_contracts=1, start_limit_price=0.85,
        collateral=1100.0, dry_run=False, reasons="", payload_json="{}", ladder_json="[]",
        bid=0.84, ask=0.86, sent=True,
    )


def test_los_naked_tambien_recuperan_el_cupo_al_re_armar(conn):
    repo.arm_live_today(conn, HOY, now=MANANA_9)
    _log_naked(conn, day=HOY, ts=MEDIODIA)
    _, marca = repo.live_rearm_mark(conn, HOY)
    assert repo.count_live_approved_opens_today(conn, HOY, after_id=marca) == 1

    repo.arm_live_today(conn, HOY, now=TARDE)
    _, marca = repo.live_rearm_mark(conn, HOY)
    assert repo.count_live_approved_opens_today(conn, HOY, after_id=marca) == 0
    assert repo.count_live_approved_opens_today(conn, HOY) == 1


def test_el_tope_semanal_y_el_capital_NO_se_resetean_al_re_armar(conn):
    """Límite explícito de la regla: re-armar devuelve el permiso del DÍA, no relaja el riesgo."""
    repo.arm_live_today(conn, HOY, now=MANANA_9)
    _log_naked(conn, day=HOY, ts=MEDIODIA)
    repo.arm_live_today(conn, HOY, now=TARDE)

    assert repo.count_live_approved_opens_this_week(conn, HOY) == 1
    assert repo.sum_live_collateral_today(conn, HOY) == pytest.approx(1100.0)
