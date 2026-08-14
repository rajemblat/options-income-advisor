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


def test_context_includes_strong_support_numbers():
    """El contexto guarda el soporte fuerte en NÚMEROS (usuario 2026-08-05: "que me diga los
    soportes que usa"), para mostrarlos en la tarjeta y poder puntuar con criterio."""
    result = evaluate_entry("TST", _snapshot(), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), _settings())
    assert isinstance(result.context.get("support_used"), (int, float))
    assert isinstance(result.context.get("supports_daily"), list) and result.context["supports_daily"]
    # el soporte "usado" es uno de los detectados
    assert result.context["support_used"] in result.context["supports_daily"]


def test_fails_when_rsi_out_of_range():
    result = evaluate_entry("TST", _snapshot(rsi_14=55.0), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), _settings())
    assert result.passed is False
    assert any("RSI" in r for r in result.reasons)


def test_fails_when_iv_rank_too_low():
    result = evaluate_entry("TST", _snapshot(iv_rank=40.0), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), _settings())
    assert result.passed is False
    assert any("IV Rank" in r for r in result.reasons)


def test_fails_when_no_strong_support_nearby():
    # Soporte plano LEJOS del precio actual (93): existe un nivel ~79.5 pero no está cerca — el
    # gate de soporte fuerte DIARIO cercano debe rechazarlo (lógica combinada 2026-08-03).
    flat_bars = [
        PriceBar(symbol="TST", trade_date=date(2025, 11, 1) + timedelta(days=i), open=80, high=80.5, low=79.5, close=80, volume=1000)
        for i in range(90)
    ]
    result = evaluate_entry("TST", _snapshot(), _chain(_ELIGIBLE_PUTS), flat_bars, _settings())
    assert result.passed is False
    assert any("soporte fuerte" in r for r in result.reasons)


def test_fails_when_uptrend_not_confirmed():
    # Con confirmación de tendencia activada, precio por debajo de la SMA de tendencia => falla
    # (reemplaza el viejo gate de "precio por debajo de TODAS las SMA").
    result = evaluate_entry(
        "TST", _snapshot(), _chain(_ELIGIBLE_PUTS), _price_history_with_support(),
        _settings(confirm_uptrend=True, trend_sma_period=50),
    )
    assert result.passed is False
    assert any("SMA" in r for r in result.reasons)


def test_earnings_before_expiration_does_not_block_but_flags_event():
    # Cambio de comportamiento (usuario 2026-08-03): earnings antes del vencimiento NO bloquea —
    # se entra igual (aprovechando la vola) pero marcando el evento para pedir más cobertura.
    soon = AS_OF + timedelta(days=1)
    result = evaluate_entry("TST", _snapshot(next_earnings_date=soon), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), _settings())
    assert result.passed is True
    assert result.context["has_event"] is True


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


def test_selects_highest_annualized_return_not_highest_credit():
    # strike 90 / 30 DTE / crédito 1.50 anualiza más (1.50/90*365/30 = 20.3%) que
    # strike 75 / 45 DTE / crédito 1.80 (1.80/75*365/45 = 19.5%), aunque pague MENOS crédito.
    puts = [
        _put(strike=75, dte=45, delta=0.17, mid=1.80),
        _put(strike=90, dte=30, delta=0.15, mid=1.50),
    ]
    result = evaluate_entry("TST", _snapshot(), _chain(puts), _price_history_with_support(), _settings())
    assert result.passed is True
    assert result.contract.strike == 90  # elegido por mayor retorno anualizado, no por crédito
    assert result.context["chosen_theta"] == -0.02  # los griegos quedan registrados para la IA


def test_robot_full_gates_open_path():
    # Camino de APERTURA con TODOS los gates del robot activados (config real): delta dinámica,
    # cobertura, VWAP, liquidez, POP y retorno anualizado >= 40%.
    s = _settings(
        use_dynamic_delta=True, delta_target_volatile=0.15, delta_target_normal=0.20, delta_band=0.05,
        require_coverage=True, coverage_normal=0.08, coverage_volatile=0.10, coverage_far_from_support=0.12,
        volatile_iv_threshold=0.40, require_real_iv_rank=True, require_price_above_vwap=True,
        require_positive_bid=True, min_open_interest=100, min_contract_volume=10, max_bid_ask_spread_pct=0.20,
        min_probability_otm=0.70, min_credit=0.10, min_annualized_return=0.40, require_monthly_support=False,
    )
    snap = _snapshot(iv_atm=0.50, iv_rank=70.0, price=100.0, sma_200=95.0)
    puts = [_put(strike=88, dte=40, delta=0.16, mid=4.50)]  # OTM 12%, delta en banda volátil, anualiza ~47%
    result = evaluate_entry("TST", snap, _chain(puts), _price_history_with_support(low=95, high=105), s,
                            price_above_vwap=True, has_macro_event=False)
    assert result.passed is True, result.reasons
    assert result.contract.strike == 88
    assert result.context["volatile"] is True
    assert result.context["required_coverage_pct"] == 0.12
    assert result.context["chosen_annualized_return"] >= 0.40


def test_robot_rejects_when_price_below_vwap():
    s = _settings(require_price_above_vwap=True)
    result = evaluate_entry("TST", _snapshot(), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), s,
                            price_above_vwap=False)
    assert result.passed is False
    assert any("VWAP" in r for r in result.reasons)


def test_robot_rejects_when_vwap_data_missing():
    s = _settings(require_price_above_vwap=True)
    result = evaluate_entry("TST", _snapshot(), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), s,
                            price_above_vwap=None)
    assert result.passed is False
    assert any("VWAP" in r for r in result.reasons)


def test_soft_scoring_opens_on_best_candidate():
    # Cerebro flexible: no exige delta Y cobertura por separado; puntúa y elige el mejor.
    s = _settings(use_soft_scoring=True, min_entry_score=0.30, target_annualized=0.20,
                  coverage_normal=0.08, coverage_volatile=0.10, score_weight_delta=0.35,
                  score_weight_coverage=0.25, score_weight_return=0.25, score_weight_pop=0.15)
    result = evaluate_entry("TST", _snapshot(), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), s)
    assert result.passed is True
    assert result.contract is not None


def test_soft_scoring_skips_when_below_min_score():
    s = _settings(use_soft_scoring=True, min_entry_score=0.99, coverage_normal=0.08)  # umbral imposible
    result = evaluate_entry("TST", _snapshot(), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), s)
    assert result.passed is False
    assert any("puntaje" in r.lower() for r in result.reasons)


# --- Regla: no entrar en acciones que suben mucho hoy; preferir las que caen (usuario 2026-08-04) ---

def test_skips_when_stock_up_more_than_cap_today():
    result = evaluate_entry(
        "TST", _snapshot(), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), _settings(),
        day_change_pct=3.5,  # sube 3.5% hoy > tope +2%
    )
    assert result.passed is False
    assert any("Sube" in r and "hoy" in r for r in result.reasons)
    assert result.context["day_change_pct"] == 3.5


def test_allows_stock_falling_today():
    result = evaluate_entry(
        "TST", _snapshot(), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), _settings(),
        day_change_pct=-3.0,  # cae 3% hoy → NO se saltea por esta regla
    )
    assert result.passed is True
    assert not any("Sube" in r for r in result.reasons)


def test_allows_stock_up_within_cap():
    result = evaluate_entry(
        "TST", _snapshot(), _chain(_ELIGIBLE_PUTS), _price_history_with_support(), _settings(),
        day_change_pct=1.5,  # sube 1.5% < tope +2% → permitido
    )
    assert result.passed is True


def test_day_change_score_prefers_falling():
    from options_advisor.simulator import rules
    s = _settings()
    falling = rules.day_change_score(-4.0, s)   # cae 4% → ideal
    flat = rules.day_change_score(0.0, s)
    rising = rules.day_change_score(2.0, s)     # en el tope de suba → peor
    assert falling > flat > rising
    assert rising == 0.0 and falling == 1.0


# --- Dimensiones nuevas del cerebro: IV Rank, liquidez, theta (usuario 2026-08-05) ---

def test_candidate_subscores_new_dimensions():
    from options_advisor.simulator import rules
    s = _settings()
    base = {"delta_abs": 0.20, "coverage": 0.10, "annualized": 0.5, "day_change_pct": 0.0,
            "iv_rank": 60, "spread_pct": 0.05, "theta": -0.02, "premium": 1.0}
    subs = rules.candidate_subscores(base, False, s)
    assert set(subs) == set(rules.SCORE_DIMENSIONS)
    # IV Rank más alta -> más puntaje
    hi = rules.candidate_subscores({**base, "iv_rank": 95}, False, s)["iv_rank"]
    lo = rules.candidate_subscores({**base, "iv_rank": 52}, False, s)["iv_rank"]
    assert hi > lo
    # spread más ajustado -> más liquidez
    tight = rules.candidate_subscores({**base, "spread_pct": 0.02}, False, s)["liquidity"]
    wide = rules.candidate_subscores({**base, "spread_pct": 0.09}, False, s)["liquidity"]
    assert tight > wide
    # más theta relativo a la prima -> más puntaje de theta
    strong = rules.candidate_subscores({**base, "theta": -0.03, "premium": 1.0}, False, s)["theta"]
    weak = rules.candidate_subscores({**base, "theta": -0.005, "premium": 1.0}, False, s)["theta"]
    assert strong > weak


def test_score_prefers_higher_iv_rank_all_else_equal():
    from options_advisor.simulator import rules
    s = _settings()
    f_low = {"delta_abs": 0.20, "coverage": 0.10, "annualized": 0.5, "day_change_pct": 0.0,
             "iv_rank": 55, "spread_pct": 0.04, "theta": -0.02, "premium": 1.0}
    f_high = {**f_low, "iv_rank": 90}
    assert rules.score_put_candidate(f_high, False, s) > rules.score_put_candidate(f_low, False, s)


def test_missing_features_are_neutral():
    from options_advisor.simulator import rules
    s = _settings()
    subs = rules.candidate_subscores({"delta_abs": 0.20}, False, s)
    assert subs["iv_rank"] == 0.5 and subs["liquidity"] == 0.5 and subs["theta"] == 0.5
