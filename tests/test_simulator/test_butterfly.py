from __future__ import annotations

from datetime import date, datetime, timedelta

from options_advisor.broker.models import Greeks, IntradayBar, OptionChain, OptionContract
from options_advisor.config import IntradayButterflySettings
from options_advisor.simulator import butterfly

SPOT = 7600.0
EXP = date(2026, 8, 4)


def _settings(**over) -> IntradayButterflySettings:
    d = dict(distance_threshold_pct=0.0015, breakeven_offset_pct=0.0015, wing_width=5.0, max_collateral=200.0, sma_period=8)
    d.update(over)
    return IntradayButterflySettings(**d)


def _bars(closes: list[float]) -> list[IntradayBar]:
    start = datetime(2026, 8, 4, 10, 0)
    return [
        IntradayBar(symbol="SPX", timestamp=start + timedelta(minutes=i), open=c, high=c, low=c, close=c, volume=100)
        for i, c in enumerate(closes)
    ]


def _opt(otype: str, strike: float, mid: float) -> OptionContract:
    return OptionContract(
        symbol="SPX", option_type=otype, strike=strike, expiration=EXP,
        bid=round(mid - 0.05, 2), ask=round(mid + 0.05, 2), last_price=mid,
        implied_volatility=0.12, open_interest=1000, volume=500,
        greeks=Greeks(delta=0.5 if otype == "call" else -0.5, gamma=0.01, theta=-0.5, vega=0.1, rho=0.01, source="broker"),
    )


def _chain(body_short_mid: float = 3.2, wing_long_mid: float = 1.5) -> OptionChain:
    contracts = []
    for k in range(7580, 7621, 5):  # strikes cada 5 puntos alrededor de 7600
        # el cuerpo (7600) vale más; las alas menos, cuanto más lejos menos
        mid = body_short_mid if k == 7600 else wing_long_mid
        contracts.append(_opt("put", float(k), mid))
        contracts.append(_opt("call", float(k), mid))
    return OptionChain(symbol="SPX", as_of=EXP, underlying_price=SPOT, contracts=contracts)


# ---------------- Señal SMA8 ----------------

def test_signal_revert_down_when_price_above_sma8():
    sig = butterfly.evaluate_signal(_bars([7600] * 7 + [7620]), _settings())
    assert sig.direction == "revert_down"  # subió y se alejó por arriba


def test_signal_revert_up_when_price_below_sma8():
    sig = butterfly.evaluate_signal(_bars([7600] * 7 + [7580]), _settings())
    assert sig.direction == "revert_up"


def test_signal_none_when_close_to_sma8():
    sig = butterfly.evaluate_signal(_bars([7600] * 7 + [7605]), _settings())
    assert sig.direction is None  # dentro del umbral, no opera


def test_signal_none_when_not_enough_bars():
    sig = butterfly.evaluate_signal(_bars([7600, 7601, 7602]), _settings())
    assert sig.direction is None and sig.sma8 is None


# ---------------- Armado del Iron Butterfly ----------------

def test_build_iron_butterfly_respects_risk_cap():
    # offset 0 -> cuerpo en el spot (7600, el strike "caro" del chain de prueba)
    s = _settings(breakeven_offset_pct=0.0)
    build = butterfly.build_iron_butterfly(_chain(), SPOT, "revert_down", s)
    assert build is not None
    assert build.body_strike == 7600
    assert build.put_wing_width == 5 and build.call_wing_width == 5
    # crédito = (3.2+3.2) - (1.5+1.5) = 3.4 -> $340; pérdida máx = 5*100 - 340 = 160 (<= 200)
    assert build.net_credit == 340.0
    assert build.max_loss == 160.0
    assert build.max_loss <= s.max_collateral
    assert len(build.legs) == 4
    # breakevens = 7600 ± 3.4
    assert build.lower_breakeven == 7596.6 and build.upper_breakeven == 7603.4


def test_build_returns_none_when_max_loss_exceeds_cap():
    # Alas caras / poco crédito -> pérdida máxima > 200 -> no se arma
    chain = _chain(body_short_mid=3.0, wing_long_mid=1.7)  # crédito (6-3.4)=2.6 -> 260; maxloss 500-260=240>200
    build = butterfly.build_iron_butterfly(chain, SPOT, "revert_down", _settings(breakeven_offset_pct=0.0))
    assert build is None


def _priced_chain() -> OptionChain:
    # Precios realistas: puts más caros a strikes altos, calls más caros a strikes bajos, así el
    # crédito neto del butterfly es positivo aunque el cuerpo esté corrido del spot.
    contracts = []
    for k in range(7560, 7641, 5):
        put_mid = round(3.0 + (k - 7600) * 0.02, 2)
        call_mid = round(3.0 - (k - 7600) * 0.02, 2)
        contracts.append(_opt("put", float(k), put_mid))
        contracts.append(_opt("call", float(k), call_mid))
    return OptionChain(symbol="SPX", as_of=EXP, underlying_price=SPOT, contracts=contracts)


def test_build_offsets_body_toward_reversion_direction():
    # offset = 0.0015*7600 = 11.4 pts -> cuerpo ~7590 (revert_down) / ~7610 (revert_up).
    # profit_target=0 desactiva el gate de crédito mínimo (este test solo mira el offset del cuerpo).
    s = _settings(breakeven_offset_pct=0.0015, max_collateral=1000.0, profit_target=0.0)
    down = butterfly.build_iron_butterfly(_priced_chain(), SPOT, "revert_down", s)
    up = butterfly.build_iron_butterfly(_priced_chain(), SPOT, "revert_up", s)
    assert down is not None and up is not None
    assert down.body_strike < SPOT   # reversión a la baja -> cuerpo por debajo del spot
    assert up.body_strike > SPOT     # reversión al alza -> cuerpo por encima del spot


# ---------------- Marcado / salida ----------------

def test_close_value_and_unrealized():
    # cadena con cuerpo a 2.0 y alas a 1.0 -> cerrar cuesta (2+2-1-1)*100 = 200
    chain = _chain(body_short_mid=2.0, wing_long_mid=1.0)
    cv = butterfly.butterfly_close_value(chain, 7600.0, 7595.0, 7605.0)
    assert cv == 200.0
    # entré cobrando 340 de crédito -> no realizado = 340 - 200 = 140
    assert butterfly.butterfly_unrealized(340.0, cv) == 140.0


def test_close_value_none_when_leg_missing():
    chain = _chain()
    assert butterfly.butterfly_close_value(chain, 7600.0, 7000.0, 7605.0) is None  # ala inexistente


def test_should_close_profit_target_dollars():
    # Modo dólares (profit_target_pct = 0): cierra a +$50.
    s = _settings()
    s = IntradayButterflySettings(**{**s.model_dump(), "profit_target_pct": 0.0, "profit_target": 50.0, "stop_loss": 70.0})
    assert butterfly.should_close_butterfly(55.0, False, s) == (True, "profit_target")
    assert butterfly.should_close_butterfly(-80.0, False, s) == (True, "stop_loss")
    assert butterfly.should_close_butterfly(10.0, False, s) == (False, None)
    assert butterfly.should_close_butterfly(10.0, True, s)[0] is True  # vencimiento siempre cierra


def test_should_close_profit_target_pct():
    # Modo % del crédito (usuario 2026-08-07): cierra al 30% del crédito en todo momento.
    s = _settings()
    s = IntradayButterflySettings(**{**s.model_dump(), "profit_target_pct": 0.30, "stop_loss": 70.0})
    # crédito $200 → 30% = $60. A +$65 cierra, a +$40 todavía no.
    assert butterfly.should_close_butterfly(65.0, False, s, entry_net_credit=200.0) == (True, "profit_target")
    assert butterfly.should_close_butterfly(40.0, False, s, entry_net_credit=200.0) == (False, None)
    assert butterfly.should_close_butterfly(-80.0, False, s, entry_net_credit=200.0) == (True, "stop_loss")


def test_intrinsic_close_value_at_expiration():
    # spot justo en el cuerpo -> todas las patas valen 0 -> cerrar cuesta 0
    assert butterfly.butterfly_intrinsic_close_value(7600.0, 7600.0, 7595.0, 7605.0) == 0.0
    # spot bien arriba -> short call ITM 20, long call ITM 15 -> (0+20-0-15)*100 = 500 de costo
    assert butterfly.butterfly_intrinsic_close_value(7620.0, 7600.0, 7595.0, 7605.0) == 500.0


def test_build_rejects_when_credit_below_profit_target():
    """No arma un butterfly cuyo crédito máximo no llegue al objetivo (usuario 2026-08-05)."""
    # crédito del fly de prueba = (3.2+3.2)-(1.5+1.5)=3.4 -> $340; con objetivo $400 no debe armar.
    s = _settings(breakeven_offset_pct=0.0, profit_target=400.0, max_collateral=1000.0)
    assert butterfly.build_iron_butterfly(_chain(), SPOT, "revert_down", s) is None
    # con objetivo $50 sí arma
    s2 = _settings(breakeven_offset_pct=0.0, profit_target=50.0, max_collateral=1000.0)
    assert butterfly.build_iron_butterfly(_chain(), SPOT, "revert_down", s2) is not None
