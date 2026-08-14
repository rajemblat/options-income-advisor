from __future__ import annotations

from datetime import date, datetime, timedelta

from options_advisor.broker.models import Greeks, IntradayBar, OptionChain, OptionContract
from options_advisor.config import IntradayCondorSettings
from options_advisor.simulator import iron_condor

SPOT = 7600.0
EXP = date(2026, 8, 5)


def _settings(**over) -> IntradayCondorSettings:
    d = dict(short_delta_max=0.15, wing_width=10.0, profit_target_pct=0.60, stop_loss_dollars=100.0,
             calm_range_pct=0.004, entry_window_start="10:00", entry_window_end="14:00", max_collateral=1000.0)
    d.update(over)
    return IntradayCondorSettings(**d)


def _bars(closes: list[float], hour: int = 10, minute: int = 30) -> list[IntradayBar]:
    start = datetime(2026, 8, 5, hour, minute)
    return [
        IntradayBar(symbol="SPX", timestamp=start + timedelta(minutes=i), open=closes[0], high=max(closes[: i + 1]),
                    low=min(closes[: i + 1]), close=c, volume=100)
        for i, c in enumerate(closes)
    ]


def _opt(otype: str, strike: float, mid: float, delta: float) -> OptionContract:
    return OptionContract(
        symbol="SPX", option_type=otype, strike=strike, expiration=EXP,
        bid=round(mid - 0.05, 2), ask=round(mid + 0.05, 2), last_price=mid,
        implied_volatility=0.12, open_interest=1000, volume=500,
        greeks=Greeks(delta=delta, gamma=0.01, theta=-0.5, vega=0.1, rho=0.01, source="broker"),
    )


def _chain() -> OptionChain:
    contracts = [
        # puts (delta negativo; más cerca del spot = más delta)
        _opt("put", 7540, 2.0, -0.10),
        _opt("put", 7550, 3.0, -0.14),   # mejor prima con delta<=0.15 -> short put
        _opt("put", 7560, 4.0, -0.20),
        _opt("put", 7570, 5.0, -0.28),
        # calls (delta positivo; más cerca del spot = más delta)
        _opt("call", 7630, 5.0, 0.28),
        _opt("call", 7640, 4.0, 0.20),
        _opt("call", 7650, 3.0, 0.14),   # mejor prima con delta<=0.15 -> short call
        _opt("call", 7660, 2.0, 0.10),
    ]
    return OptionChain(symbol="SPX", as_of=EXP, underlying_price=SPOT, contracts=contracts)


# ---------------- Señal (día calmo + ventana) ----------------

def test_signal_calm_and_in_window():
    sig = iron_condor.evaluate_condor_signal(_bars([7600, 7601, 7599, 7600], hour=10, minute=30), _settings())
    assert sig.calm is True and sig.in_window is True


def test_signal_not_calm_when_big_range():
    # rango 7600->7660 = 0.79% > 0.4%
    sig = iron_condor.evaluate_condor_signal(_bars([7600, 7660, 7600], hour=10, minute=30), _settings())
    assert sig.calm is False


def test_signal_out_of_window_early():
    sig = iron_condor.evaluate_condor_signal(_bars([7600, 7601], hour=9, minute=45), _settings())
    assert sig.in_window is False


def test_window_is_compared_in_utc_not_new_york():
    """CANDADO de comportamiento (verificado 2026-08-14): la ventana se compara contra la hora de la
    barra TAL CUAL viene de Schwab, que es UTC. Con la config "10:00–14:00" eso significa, en horario
    de verano (UTC−4), los primeros 30 minutos de la rueda: 09:31 ET (13:31 UTC) entra, 12:00 ET
    (16:00 UTC) no. Es el horario en el que el papel ganó sus 16 condors seguidos, así que este test
    existe para que nadie lo "arregle" sin querer y mueva la estrategia a otro horario.
    Ver `config/settings.yaml::intraday_condor` y el docstring de `evaluate_condor_signal`."""
    from datetime import timezone

    def _utc_bars(h, m):
        start = datetime(2026, 8, 5, h, m, tzinfo=timezone.utc)
        return [
            IntradayBar(symbol="SPX", timestamp=start + timedelta(minutes=i), open=7600.0, high=7601.0,
                        low=7599.0, close=7600.0, volume=100)
            for i in range(3)
        ]

    # 13:31 UTC = 09:31 ET, apenas abrió el mercado (09:30–16:00 ET): DENTRO de la ventana.
    assert iron_condor.evaluate_condor_signal(_utc_bars(13, 31), _settings()).in_window is True
    # 16:00 UTC = 12:00 ET, mediodía de Nueva York: FUERA, aunque el config diga "14:00".
    assert iron_condor.evaluate_condor_signal(_utc_bars(16, 0), _settings()).in_window is False
    # 14:30 UTC = 09:30 ET en INVIERNO (UTC−5): la apertura misma ya cae fuera de la ventana. Cuando
    # cambie la hora en noviembre hay que mover entry_window_end o el condor deja de abrir.
    assert iron_condor.evaluate_condor_signal(_utc_bars(14, 30), _settings()).in_window is False


# ---------------- Armado ----------------

def test_build_iron_condor_picks_best_paying_delta_and_wings():
    build = iron_condor.build_iron_condor(_chain(), SPOT, _settings())
    assert build is not None
    assert build.short_put_strike == 7550 and build.short_call_strike == 7650   # mejor prima con delta<=0.15
    assert build.long_put_strike == 7540 and build.long_call_strike == 7660     # alas de 10 pts
    # crédito = (3+3)-(2+2) = 2.0 -> $200 ; pérdida máx = 10*100 - 200 = 800 (<= 1000)
    assert build.net_credit == 200.0
    assert build.max_loss == 800.0
    assert len(build.legs) == 4
    # breakevens = short strikes ± crédito_ps (2.0)
    assert build.lower_breakeven == 7548.0 and build.upper_breakeven == 7652.0


def test_build_returns_none_when_max_loss_exceeds_cap():
    build = iron_condor.build_iron_condor(_chain(), SPOT, _settings(max_collateral=100.0))
    assert build is None   # pérdida máx 800 > 100


def test_build_returns_none_when_no_delta_candidates():
    # todos los deltas altos -> no hay strikes a delta<=0.15
    s = _settings(short_delta_max=0.05)
    assert iron_condor.build_iron_condor(_chain(), SPOT, s) is None


# ---------------- Marcado / salida ----------------

def test_close_value_and_unrealized():
    chain = OptionChain(symbol="SPX", as_of=EXP, underlying_price=SPOT, contracts=[
        _opt("put", 7550, 1.2, -0.10), _opt("call", 7650, 1.2, 0.10),
        _opt("put", 7540, 0.8, -0.06), _opt("call", 7660, 0.8, 0.06),
    ])
    cv = iron_condor.condor_close_value(chain, 7550, 7650, 7540, 7660)
    assert cv == round(((1.2 + 1.2) - (0.8 + 0.8)) * 100, 2)  # = 80.0
    assert iron_condor.condor_unrealized(200.0, cv) == 120.0


def test_should_close_profit_target_60pct_and_stop_100():
    s = _settings()
    # crédito $200; 60% = $120
    assert iron_condor.should_close_condor(120.0, 200.0, False, s) == (True, "profit_target")
    assert iron_condor.should_close_condor(119.0, 200.0, False, s) == (False, None)
    assert iron_condor.should_close_condor(-100.0, 200.0, False, s) == (True, "stop_loss")
    assert iron_condor.should_close_condor(-99.0, 200.0, False, s) == (False, None)
    assert iron_condor.should_close_condor(0.0, 200.0, True, s)[0] is True   # vencimiento


def test_should_close_early_window_tiered():
    # Usuario 2026-08-07: 40% en los primeros 20 min (para reentrar); 50% después.
    s = _settings(profit_target_pct=0.50, profit_target_early_pct=0.40, early_window_minutes=20.0)
    credit = 200.0  # 40% = $80, 50% = $100
    # Temprano (age 10 min): cierra a +$80 (40%), no necesita esperar al 50%.
    assert iron_condor.should_close_condor(80.0, credit, False, s, age_minutes=10.0) == (True, "profit_target")
    # Temprano pero solo +$70 (35%): todavía no.
    assert iron_condor.should_close_condor(70.0, credit, False, s, age_minutes=10.0) == (False, None)
    # Pasada la ventana (age 40 min): +$80 (40%) NO alcanza, necesita $100 (50%).
    assert iron_condor.should_close_condor(80.0, credit, False, s, age_minutes=40.0) == (False, None)
    assert iron_condor.should_close_condor(100.0, credit, False, s, age_minutes=40.0) == (True, "profit_target")
    # Sin edad (None) usa el objetivo tardío (50%).
    assert iron_condor.should_close_condor(80.0, credit, False, s, age_minutes=None) == (False, None)


def test_intrinsic_close_value_at_expiration():
    # spot dentro del rango (entre short put y short call) -> todas las patas 0 -> cierre 0 (ganancia total)
    assert iron_condor.condor_intrinsic_close_value(7600.0, 7550, 7650, 7540, 7660) == 0.0
    # spot muy arriba -> short call ITM 60, long call ITM 50 -> (60-50)*100 = 1000 de costo
    assert iron_condor.condor_intrinsic_close_value(7710.0, 7550, 7650, 7540, 7660) == 1000.0
