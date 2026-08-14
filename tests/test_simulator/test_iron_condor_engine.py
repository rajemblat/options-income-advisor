from __future__ import annotations

from datetime import date, datetime, timedelta

from options_advisor.broker.models import Greeks, IntradayBar, OptionChain, OptionContract
from options_advisor.config import load_settings
from options_advisor.simulator import iron_condor_engine
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 8, 4)
SPOT = 7600.0


def _opt(otype: str, strike: float, mid: float, delta: float, exp: date = AS_OF) -> OptionContract:
    return OptionContract(
        symbol="SPX", option_type=otype, strike=strike, expiration=exp,
        bid=round(mid - 0.05, 2), ask=round(mid + 0.05, 2), last_price=mid,
        implied_volatility=0.12, open_interest=1000, volume=500,
        greeks=Greeks(delta=delta, gamma=0.01, theta=-0.5, vega=0.1, rho=0.01, source="broker"),
    )


def _chain() -> OptionChain:
    """Cadena OTM a delta chico: el short put 7500 y el short call 7700 pagan más (mid 2.0);
    el resto 1.0. Alas a 10 puntos presentes (7490 / 7710)."""
    contracts = []
    for k in range(7480, 7721, 10):
        put_mid = 2.0 if k == 7500 else 1.0
        call_mid = 2.0 if k == 7700 else 1.0
        contracts.append(_opt("put", float(k), put_mid, delta=-0.10))
        contracts.append(_opt("call", float(k), call_mid, delta=0.10))
    return OptionChain(symbol="SPX", as_of=AS_OF, underlying_price=SPOT, contracts=contracts)


def _calm_bars() -> list[IntradayBar]:
    """Día calmo dentro de la ventana (arranca 10:00): rango minúsculo, muy por debajo de 0.4%."""
    start = datetime(2026, 8, 4, 10, 0)
    closes = [7600.0, 7601.0, 7600.5, 7600.0]
    return [
        IntradayBar(symbol="SPX", timestamp=start + timedelta(minutes=i), open=c, high=c, low=c, close=c, volume=100)
        for i, c in enumerate(closes)
    ]


class _FakeBroker:
    def __init__(self, bars, chain):
        self._bars = bars
        self._chain = chain

    def get_intraday_bars(self, symbol, session_date, interval_minutes=1):
        return self._bars

    def get_option_chain(self, symbol, expiration_range_days=(0, 2)):
        return self._chain


def _settings(**over):
    settings = load_settings()
    cfg = settings.intraday_condor.model_copy(update={
        "enabled": True, "underlying": "SPX", "max_collateral": 1000.0, "min_credit": 0.0, **over,
    })
    return settings.model_copy(update={"intraday_condor": cfg})


def test_cycle_opens_condor_on_calm_day():
    conn = db.connect(":memory:")
    broker = _FakeBroker(_calm_bars(), _chain())
    iron_condor_engine.process_condor_cycle(conn, broker, _settings(), AS_OF)
    open_rows = repo.get_open_condor_positions(conn)
    assert len(open_rows) == 1
    assert open_rows[0]["short_put_strike"] == 7500
    assert open_rows[0]["short_call_strike"] == 7700


def test_cycle_does_not_open_when_condor_paused():
    conn = db.connect(":memory:")
    repo.set_condor_paused(conn, True)
    broker = _FakeBroker(_calm_bars(), _chain())
    iron_condor_engine.process_condor_cycle(conn, broker, _settings(), AS_OF)
    assert repo.get_open_condor_positions(conn) == []


def test_cycle_does_not_open_when_all_paused():
    conn = db.connect(":memory:")
    repo.set_all_paused(conn, True)
    broker = _FakeBroker(_calm_bars(), _chain())
    iron_condor_engine.process_condor_cycle(conn, broker, _settings(), AS_OF)
    assert repo.get_open_condor_positions(conn) == []


def test_unlimited_daily_cap_keeps_opening():
    """max_per_day=0 = sin tope diario: sigue abriendo tick tras tick hasta el máx. simultáneo."""
    conn = db.connect(":memory:")
    broker = _FakeBroker(_calm_bars(), _chain())
    settings = _settings(max_per_day=0, max_open_positions=5)
    for _ in range(3):
        iron_condor_engine.process_condor_cycle(conn, broker, settings, AS_OF)
    assert len(repo.get_open_condor_positions(conn)) == 3


def test_open_positions_cap_still_applies_when_daily_unlimited():
    """Aunque el tope diario sea ilimitado, el freno de abiertos-a-la-vez sigue valiendo."""
    conn = db.connect(":memory:")
    broker = _FakeBroker(_calm_bars(), _chain())
    settings = _settings(max_per_day=0, max_open_positions=2)
    for _ in range(4):
        iron_condor_engine.process_condor_cycle(conn, broker, settings, AS_OF)
    assert len(repo.get_open_condor_positions(conn)) == 2


# --------------- Freno del día por racha de stop-loss (usuario 2026-08-08) ---------------

def _insert_closed_condor(conn, close_reason, close_ts):
    pid = repo.insert_condor_position(
        conn, underlying="SPX", entry_date=AS_OF, expiration_date=AS_OF,
        short_put_strike=7500, short_call_strike=7700, long_put_strike=7490, long_call_strike=7710,
        entry_net_credit=2.0, max_loss=800.0, max_profit=200.0, lower_breakeven=7480.0,
        upper_breakeven=7720.0, entry_spot=SPOT, entry_ts=datetime(2026, 8, 4, 10, 0),
    )
    repo.close_condor_position(conn, pid, AS_OF, close_value=3.0, close_reason=close_reason,
                               realized_pnl=-100.0, close_ts=close_ts)
    return pid


def test_condor_stop_loss_streak_counter():
    conn = db.connect(":memory:")
    _insert_closed_condor(conn, "stop_loss", datetime(2026, 8, 4, 10, 5))
    _insert_closed_condor(conn, "stop_loss", datetime(2026, 8, 4, 10, 10))
    assert repo.condor_consecutive_stop_losses_today(conn, AS_OF) == 2
    # Una ganancia DESPUÉS corta la racha (se cuenta desde la más reciente hacia atrás).
    _insert_closed_condor(conn, "profit_target", datetime(2026, 8, 4, 10, 15))
    assert repo.condor_consecutive_stop_losses_today(conn, AS_OF) == 0
    # Y no cuenta lo de otros días.
    assert repo.condor_consecutive_stop_losses_today(conn, date(2026, 8, 5)) == 0


def test_cycle_halts_after_two_consecutive_stop_losses():
    conn = db.connect(":memory:")
    _insert_closed_condor(conn, "stop_loss", datetime(2026, 8, 4, 10, 5))
    _insert_closed_condor(conn, "stop_loss", datetime(2026, 8, 4, 10, 10))
    broker = _FakeBroker(_calm_bars(), _chain())
    iron_condor_engine.process_condor_cycle(conn, broker, _settings(stop_loss_streak_halt=2), AS_OF)
    assert repo.get_open_condor_positions(conn) == []   # frenado: no abre pese al día calmo


def test_cycle_reopens_when_streak_broken_by_win():
    conn = db.connect(":memory:")
    _insert_closed_condor(conn, "stop_loss", datetime(2026, 8, 4, 10, 5))
    _insert_closed_condor(conn, "profit_target", datetime(2026, 8, 4, 10, 10))
    _insert_closed_condor(conn, "stop_loss", datetime(2026, 8, 4, 10, 15))   # racha actual = 1 < 2
    broker = _FakeBroker(_calm_bars(), _chain())
    iron_condor_engine.process_condor_cycle(conn, broker, _settings(stop_loss_streak_halt=2), AS_OF)
    assert len(repo.get_open_condor_positions(conn)) == 1   # abre normal


def test_cycle_halt_disabled_when_streak_zero():
    conn = db.connect(":memory:")
    _insert_closed_condor(conn, "stop_loss", datetime(2026, 8, 4, 10, 5))
    _insert_closed_condor(conn, "stop_loss", datetime(2026, 8, 4, 10, 10))
    broker = _FakeBroker(_calm_bars(), _chain())
    iron_condor_engine.process_condor_cycle(conn, broker, _settings(stop_loss_streak_halt=0), AS_OF)
    assert len(repo.get_open_condor_positions(conn)) == 1   # 0 = sin freno


def test_resume_all_clears_every_pause_flag():
    conn = db.connect(":memory:")
    repo.set_all_paused(conn, True)
    repo.set_puts_paused(conn, True)
    repo.set_condor_paused(conn, True)
    repo.set_butterfly_paused(conn, True)
    repo.resume_all(conn)
    assert repo.is_all_paused(conn) is False
    assert repo.is_puts_paused(conn) is False
    assert repo.is_condor_paused(conn) is False
    assert repo.is_butterfly_paused(conn) is False
