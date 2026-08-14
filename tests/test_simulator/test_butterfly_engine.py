from __future__ import annotations

from datetime import date, datetime, timedelta

from options_advisor.broker.models import Greeks, IntradayBar, OptionChain, OptionContract
from options_advisor.config import load_settings
from options_advisor.simulator import butterfly_engine
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 8, 4)
SPOT = 7600.0


def _opt(otype: str, strike: float, mid: float, exp: date = AS_OF) -> OptionContract:
    return OptionContract(
        symbol="SPX", option_type=otype, strike=strike, expiration=exp,
        bid=round(mid - 0.05, 2), ask=round(mid + 0.05, 2), last_price=mid,
        implied_volatility=0.12, open_interest=1000, volume=500,
        greeks=Greeks(delta=0.5, gamma=0.01, theta=-0.5, vega=0.1, rho=0.01, source="broker"),
    )


def _chain(center: float = 7620.0, body_mid: float = 3.2, wing_mid: float = 1.5) -> OptionChain:
    """Cadena ancha centrada en `center` (el strike del cuerpo). El cuerpo vale `body_mid`; el
    resto `wing_mid`. `center` = spot de entrada redondeado a múltiplo de 5 (SPX cotiza así)."""
    contracts = []
    for k in range(7560, 7681, 5):
        mid = body_mid if k == center else wing_mid
        contracts.append(_opt("put", float(k), mid))
        contracts.append(_opt("call", float(k), mid))
    return OptionChain(symbol="SPX", as_of=AS_OF, underlying_price=center, contracts=contracts)


def _bars(closes: list[float]) -> list[IntradayBar]:
    start = datetime(2026, 8, 4, 10, 0)
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

    def get_option_chain(self, symbol, expiration_range_days=(7, 60)):
        return self._chain


def _enabled_settings(**over):
    settings = load_settings()
    cfg = settings.intraday_butterfly.model_copy(update={
        "enabled": True, "breakeven_offset_pct": 0.0, "max_collateral": 200.0,
        "profit_target": 50.0, "stop_loss": 70.0, **over,
    })
    return settings.model_copy(update={"intraday_butterfly": cfg})


def test_cycle_opens_on_reversion_signal():
    conn = db.connect(":memory:")
    broker = _FakeBroker(_bars([7600] * 7 + [7620]), _chain())  # subió: revert_down
    butterfly_engine.process_butterfly_cycle(conn, broker, _enabled_settings(), AS_OF)

    open_rows = repo.get_open_butterfly_positions(conn)
    assert len(open_rows) == 1
    row = open_rows[0]
    assert row["direction"] == "revert_down"
    assert row["body_strike"] == 7620  # el cuerpo sigue al spot de la última barra
    assert row["entry_net_credit"] == 340.0
    assert row["max_loss"] == 160.0


def test_cycle_does_nothing_when_disabled():
    conn = db.connect(":memory:")
    broker = _FakeBroker(_bars([7600] * 7 + [7620]), _chain())
    settings = _enabled_settings()
    settings = settings.model_copy(update={"intraday_butterfly": settings.intraday_butterfly.model_copy(update={"enabled": False})})
    butterfly_engine.process_butterfly_cycle(conn, broker, settings, AS_OF)
    assert repo.get_open_butterfly_positions(conn) == []


def test_cycle_no_entry_when_price_near_sma():
    conn = db.connect(":memory:")
    broker = _FakeBroker(_bars([7600] * 8), _chain())  # sin distancia -> sin señal
    butterfly_engine.process_butterfly_cycle(conn, broker, _enabled_settings(), AS_OF)
    assert repo.get_open_butterfly_positions(conn) == []


def test_cycle_respects_max_open_positions():
    conn = db.connect(":memory:")
    broker = _FakeBroker(_bars([7600] * 7 + [7620]), _chain())
    settings = _enabled_settings(max_open_positions=1)
    butterfly_engine.process_butterfly_cycle(conn, broker, settings, AS_OF)
    butterfly_engine.process_butterfly_cycle(conn, broker, settings, AS_OF)  # segundo tick
    assert len(repo.get_open_butterfly_positions(conn)) == 1  # no abre una segunda


def test_cycle_closes_on_profit_target():
    conn = db.connect(":memory:")
    # abrir con crédito 340 (cuerpo 3.2 / alas 1.5)
    broker_open = _FakeBroker(_bars([7600] * 7 + [7620]), _chain(body_mid=3.2, wing_mid=1.5))
    settings = _enabled_settings()
    butterfly_engine.process_butterfly_cycle(conn, broker_open, settings, AS_OF)
    assert len(repo.get_open_butterfly_positions(conn)) == 1

    # ahora el butterfly vale poco (cuerpo 1.0 / alas 0.5 -> cerrar cuesta (2-1)*100=100),
    # no realizado = 340 - 100 = 240 >= profit_target(50) -> cierra ganando. La cadena de marcado
    # mantiene el MISMO cuerpo (7620) que la posición abierta, solo re-precia las patas.
    broker_mark = _FakeBroker(_bars([7600] * 8), _chain(center=7620.0, body_mid=1.0, wing_mid=0.5))
    butterfly_engine.process_butterfly_cycle(conn, broker_mark, settings, AS_OF)

    assert repo.get_open_butterfly_positions(conn) == []
    closed = repo.get_closed_butterfly_positions(conn)
    assert len(closed) == 1
    assert closed[0]["close_reason"] == "profit_target"
    # 240 bruto − comisión ida y vuelta (4 patas × 2 lados × $0.50 = $4.00) = 236 neto (usuario 2026-08-07).
    assert closed[0]["realized_pnl"] == 236.0


def test_performance_stats_aggregate():
    conn = db.connect(":memory:")
    broker_open = _FakeBroker(_bars([7600] * 7 + [7620]), _chain(body_mid=3.2, wing_mid=1.5))
    settings = _enabled_settings()
    butterfly_engine.process_butterfly_cycle(conn, broker_open, settings, AS_OF)
    broker_mark = _FakeBroker(_bars([7600] * 8), _chain(center=7620.0, body_mid=1.0, wing_mid=0.5))
    butterfly_engine.process_butterfly_cycle(conn, broker_mark, settings, AS_OF)

    stats = repo.get_butterfly_performance_stats(conn)
    assert stats["closed_count"] == 1
    assert stats["win_rate_pct"] == 100.0
    # 240 bruto − $4.00 de comisión (4 patas × 2 lados × $0.50) = 236 neto (usuario 2026-08-07).
    assert stats["total_realized_pnl"] == 236.0
