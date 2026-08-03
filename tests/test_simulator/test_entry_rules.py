from __future__ import annotations

from datetime import date, datetime, timedelta

from options_advisor.broker.models import Greeks, OptionChain, OptionContract, PriceBar
from options_advisor.config import SimulatorSettings
from options_advisor.simulator.entry_rules import evaluate_entry
from options_advisor.storage.models import IndicatorSnapshot

AS_OF = date(2026, 3, 2)  # lunes
UNDERLYING_PRICE = 93.0


def _settings(**overrides) -> SimulatorSettings:
    defaults = dict(
        enabled=True,
        initial_capital=100_000.0,
        max_position_pct=0.10,
        profit_target_pct=0.30,
        dte_range=(30, 45),
        max_delta=0.18,
        rsi_range=(30.0, 40.0),
        iv_rank_min=50.0,
        iv_percentile_min=50.0,
        support_max_distance_pct=0.05,
        weekly_support_max_distance_pct=0.09,
        sma_periods=[8, 20, 50],
        sma_min_distance_pct=0.03,
    )
    defaults.update(overrides)
    return SimulatorSettings(**defaults)


def _snapshot(**overrides) -> IndicatorSnapshot:
    defaults = dict(
        symbol="TST",
        snapshot_date=AS_OF,
        snapshot_ts=datetime.combine(AS_OF, datetime.min.time()),
        price=UNDERLYING_PRICE,
        iv_rank=65.0,
        iv_rank_source="implied_volatility",
        rsi_14=35.0,
        sma_8=UNDERLYING_PRICE * 1.05,
        sma_20=UNDERLYING_PRICE * 1.06,
        sma_50=UNDERLYING_PRICE * 1.08,
        next_earnings_date=None,
    )
    defaults.update(overrides)
    return IndicatorSnapshot(**defaults)


def _price_history_with_support(low: float = 90.0, high: float = 100.0, days: int = 90) -> list[PriceBar]:
    """Patrón repetido cada 5 barras que toca `low` muchas veces — soporte fuerte tanto en
    velas diarias como en las semanales resampleadas a partir de las mismas."""
    pattern = [(high, low), (high + 5, high - 5), (high, low), (high - 2, low + 2), (high, low)]
    start = date(2025, 11, 1)
    bars = []
    for i in range(days):
        h, l = pattern[i % len(pattern)]
        bars.append(
            PriceBar(
                symbol="TST",
                trade_date=start + timedelta(days=i),
                open=(h + l) / 2,
                high=h,
                low=l,
                close=(h + l) / 2,
                volume=1000,
            )
        )
    return bars


def _put(strike: float, dte: int, delta: float, mid: float) -> OptionContract:
    half_spread = 0.05
    return OptionContract(
        symbol="TST",
        option_type="put",
        strike=strike,
        expiration=AS_OF + timedelta(days=dte),
        bid=round(mid - half_spread, 2),
        ask=round(mid + half_spread, 2),
        last_price=mid,
        implied_volatility=0.30,
        open_interest=500,
        volume=50,
        greeks=Greeks(delta=-delta, gamma=0.01, theta=-0.02, vega=0.05, rho=0.01, source="calculated"),
    )


def _chain(contracts: list[OptionContract]) -> OptionChain:
    return OptionChain(symbol="TST", as_of=AS_OF, underlying_price=UNDERLYING_PRICE, contracts=contracts)


# Puts elegibles por delta/DTE (delta < 0.18, 30-45 DTE) con distinta prima — el de mayor
# prima (strike 75, DTE 45, mid 1.80) debe ser el elegido si el resto de criterios pasa.
_ELIGIBLE_PUTS = [
    _put(strike=65, dte=30, delta=0.05, mid=0.40),
    _put(strike=70, dte=35, delta=0.10, mid=0.90),
    _put(strike=75, dte=45, delta=0.17, mid=1.80),
]
# Fuera de rango a propósito: DTE muy corto, delta demasiado alto (>=0.18).
_INELIGIBLE_PUTS = [
    _put(strike=90, dte=20, delta=0.30, mid=3.50),
    _put(strike=88, dte=35, delta=0.25, mid=2.80),
]


def test_passes_all_criteria_and_picks_highest_premium():
    result = evaluate_entry("TST", _snapshot(), _chain(_ELIGIBLE_PUTS + _INELIGIBLE_PUTS), _price_history_with_support(), _settings())
    assert result.passed is True
    assert result.reasons == []
    assert result.contract is not None
    assert result.contract.strike == 75
    assert result.premium == 1.80


def test_fails_when_rsi_out_of_range():
    result = evaluate_entry("TST", _snapshot(rsi_14=55.0), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), _settings())
    assert result.passed is False
    assert any("RSI" in r for r in result.reasons)


def test_fails_when_iv_rank_too_low():
    result = evaluate_entry("TST", _snapshot(iv_rank=40.0), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), _settings())
    assert result.passed is False
    assert any("IV Rank" in r for r in result.reasons)


def test_fails_when_no_strong_support_nearby():
    # Historial plano, sin ningún soporte "probado" cerca del precio actual.
    flat_bars = [
        PriceBar(symbol="TST", trade_date=date(2025, 11, 1) + timedelta(days=i), open=93, high=93.5, low=92.5, close=93, volume=1000)
        for i in range(90)
    ]
    result = evaluate_entry("TST", _snapshot(), _chain(_ELIGIBLE_PUTS), flat_bars, _settings())
    assert result.passed is False
    assert any("soporte fuerte" in r for r in result.reasons)


def test_fails_when_price_not_below_all_smas():
    result = evaluate_entry("TST", _snapshot(sma_8=UNDERLYING_PRICE * 0.90), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), _settings())
    assert result.passed is False
    assert any("SMA" in r for r in result.reasons)


def test_fails_when_earnings_before_all_eligible_expirations():
    far_earnings = AS_OF + timedelta(days=60)  # después de TODAS las expiraciones elegibles
    result = evaluate_entry("TST", _snapshot(next_earnings_date=far_earnings - timedelta(days=59)), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), _settings())
    # earnings mañana (antes de cualquier vencimiento 30-45 DTE) bloquea todos los candidatos
    assert result.passed is False
    assert any("Earnings" in r for r in result.reasons)


def test_unknown_earnings_date_does_not_block():
    result = evaluate_entry("TST", _snapshot(next_earnings_date=None), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), _settings())
    assert result.passed is True


def test_fails_when_no_eligible_contracts_by_dte_or_delta():
    result = evaluate_entry("TST", _snapshot(), _chain(_INELIGIBLE_PUTS), _price_history_with_support(), _settings())
    assert result.passed is False
    assert any("DTE" in r for r in result.reasons)


def test_accumulates_multiple_failure_reasons():
    result = evaluate_entry("TST", _snapshot(rsi_14=80.0, iv_rank=10.0), _chain(_INELIGIBLE_PUTS), _price_history_with_support(), _settings())
    assert result.passed is False
    assert len(result.reasons) >= 2
