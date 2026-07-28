from __future__ import annotations

from options_advisor.dashboard.screener_filters import (
    STRATEGY_GROUP_LABELS,
    apply_filters,
    classify_delta_bucket,
    classify_moneyness_bucket,
    classify_open_interest_bucket,
    classify_volume_bucket,
    filter_by_strategy_group,
)


def _row(**overrides) -> dict:
    defaults = dict(
        Symbol="TSLA",
        DTE=25,
        Strike=320.0,
        Delta=-0.25,
        Volume=50,
        **{"Open Interest": 500, "Moneyness (%)": 8.0, "Instrumento": "stock", "Probabilidad OTM (%)": 70.0},
    )
    defaults.update(overrides)
    return defaults


# --- bucket classifiers ---


def test_classify_volume_bucket_boundaries():
    assert classify_volume_bucket(0) == "Muy bajo"
    assert classify_volume_bucket(9) == "Muy bajo"
    assert classify_volume_bucket(10) == "Bajo"
    assert classify_volume_bucket(49) == "Bajo"
    assert classify_volume_bucket(50) == "Medio"
    assert classify_volume_bucket(999) == "Alto"
    assert classify_volume_bucket(1000) == "Muy alto"
    assert classify_volume_bucket(1_000_000) == "Muy alto"


def test_classify_volume_bucket_none_for_none():
    assert classify_volume_bucket(None) is None


def test_classify_open_interest_bucket_boundaries():
    assert classify_open_interest_bucket(0) == "Muy bajo"
    assert classify_open_interest_bucket(500) == "Medio"
    assert classify_open_interest_bucket(15000) == "Muy alto"


def test_classify_moneyness_bucket_atm_around_zero():
    assert classify_moneyness_bucket(0.0) == "ATM"
    assert classify_moneyness_bucket(4.9) == "ATM"
    assert classify_moneyness_bucket(-4.9) == "ATM"


def test_classify_moneyness_bucket_deep_otm_and_itm():
    assert classify_moneyness_bucket(20.0) == "Deep OTM"
    assert classify_moneyness_bucket(-20.0) == "Deep ITM"


def test_classify_moneyness_bucket_otm_and_itm():
    assert classify_moneyness_bucket(10.0) == "OTM"
    assert classify_moneyness_bucket(-10.0) == "ITM"


def test_classify_delta_bucket_uses_absolute_value():
    """Un put vendido tiene delta NEGATIVO (-0.25) pero se clasifica igual que una call de
    +0.25 — la magnitud es lo que importa para el balde, no el signo."""
    assert classify_delta_bucket(-0.25) == classify_delta_bucket(0.25)
    assert classify_delta_bucket(-0.10) == "Bajo (0-0.25)"
    assert classify_delta_bucket(0.60) == "Alto (0.50-0.75)"
    assert classify_delta_bucket(0.90) == "Muy alto (>0.75)"


def test_classify_delta_bucket_none_for_none():
    assert classify_delta_bucket(None) is None


# --- apply_filters ---


def test_apply_filters_no_filters_returns_all_rows():
    rows = [_row(Symbol="A"), _row(Symbol="B")]
    assert apply_filters(rows) == rows


def test_apply_filters_dte_range():
    rows = [_row(Symbol="A", DTE=10), _row(Symbol="B", DTE=40)]
    result = apply_filters(rows, dte_range=(0, 30))
    assert [r["Symbol"] for r in result] == ["A"]


def test_apply_filters_dte_range_excludes_row_with_missing_dte():
    rows = [_row(Symbol="A", DTE=None)]
    assert apply_filters(rows, dte_range=(0, 30)) == []


def test_apply_filters_strike_range():
    rows = [_row(Symbol="A", Strike=50.0), _row(Symbol="B", Strike=500.0)]
    result = apply_filters(rows, strike_range=(0.0, 100.0))
    assert [r["Symbol"] for r in result] == ["A"]


def test_apply_filters_delta_buckets():
    rows = [_row(Symbol="A", Delta=-0.10), _row(Symbol="B", Delta=-0.60)]
    result = apply_filters(rows, delta_buckets=["Bajo (0-0.25)"])
    assert [r["Symbol"] for r in result] == ["A"]


def test_apply_filters_volume_buckets():
    rows = [_row(Symbol="A", Volume=5), _row(Symbol="B", Volume=5000)]
    result = apply_filters(rows, volume_buckets=["Muy alto"])
    assert [r["Symbol"] for r in result] == ["B"]


def test_apply_filters_open_interest_buckets():
    rows = [_row(Symbol="A", **{"Open Interest": 10}), _row(Symbol="B", **{"Open Interest": 20000})]
    result = apply_filters(rows, open_interest_buckets=["Muy alto"])
    assert [r["Symbol"] for r in result] == ["B"]


def test_apply_filters_moneyness_buckets():
    rows = [_row(Symbol="A", **{"Moneyness (%)": 0.0}), _row(Symbol="B", **{"Moneyness (%)": 20.0})]
    result = apply_filters(rows, moneyness_buckets=["Deep OTM"])
    assert [r["Symbol"] for r in result] == ["B"]


def test_apply_filters_instrument_types():
    rows = [_row(Symbol="A", Instrumento="stock"), _row(Symbol="B", Instrumento="etf")]
    result = apply_filters(rows, instrument_types=["etf"])
    assert [r["Symbol"] for r in result] == ["B"]


def test_apply_filters_instrument_types_excludes_unknown():
    rows = [_row(Symbol="A", Instrumento=None)]
    assert apply_filters(rows, instrument_types=["stock"]) == []


def test_apply_filters_min_probability_otm():
    rows = [_row(Symbol="A", **{"Probabilidad OTM (%)": 50.0}), _row(Symbol="B", **{"Probabilidad OTM (%)": 90.0})]
    result = apply_filters(rows, min_probability_otm=80.0)
    assert [r["Symbol"] for r in result] == ["B"]


def test_apply_filters_min_probability_otm_excludes_missing_data():
    rows = [_row(Symbol="A", **{"Probabilidad OTM (%)": None})]
    assert apply_filters(rows, min_probability_otm=50.0) == []


def test_apply_filters_combines_multiple_criteria_with_and():
    rows = [
        _row(Symbol="MATCH", DTE=25, Delta=-0.20, Instrumento="stock"),
        _row(Symbol="WRONG_DELTA", DTE=25, Delta=-0.80, Instrumento="stock"),
        _row(Symbol="WRONG_INSTRUMENT", DTE=25, Delta=-0.20, Instrumento="etf"),
    ]
    result = apply_filters(rows, dte_range=(0, 30), delta_buckets=["Bajo (0-0.25)"], instrument_types=["stock"])
    assert [r["Symbol"] for r in result] == ["MATCH"]


# --- filter_by_strategy_group (selector Naked Put / Covered Call / Ambas, pedido 2026-07-28) ---


def test_strategy_group_labels_naked_put_covers_both_put_strategies():
    assert STRATEGY_GROUP_LABELS["naked_put"] == ["Cash-Secured Put", "Short Put (Naked)"]


def test_strategy_group_labels_covered_call_is_just_covered_call():
    assert STRATEGY_GROUP_LABELS["covered_call"] == ["Covered Call"]


def test_filter_by_strategy_group_naked_put_includes_both_put_strategies():
    rows = [
        _row(Symbol="CSP", Estrategia="Cash-Secured Put"),
        _row(Symbol="SPN", Estrategia="Short Put (Naked)"),
        _row(Symbol="CC", Estrategia="Covered Call"),
    ]
    result = filter_by_strategy_group(rows, "naked_put")
    assert {r["Symbol"] for r in result} == {"CSP", "SPN"}


def test_filter_by_strategy_group_covered_call_excludes_puts():
    rows = [
        _row(Symbol="CSP", Estrategia="Cash-Secured Put"),
        _row(Symbol="CC", Estrategia="Covered Call"),
    ]
    result = filter_by_strategy_group(rows, "covered_call")
    assert [r["Symbol"] for r in result] == ["CC"]


def test_filter_by_strategy_group_excludes_short_call_naked_from_both_groups():
    """Short Call (Naked) no tiene grupo propio a propósito (perfil de riesgo distinto: sin
    acciones, riesgo no acotado) — no debe aparecer ni en 'naked_put' ni en 'covered_call'."""
    rows = [_row(Symbol="SCN", Estrategia="Short Call (Naked)")]
    assert filter_by_strategy_group(rows, "naked_put") == []
    assert filter_by_strategy_group(rows, "covered_call") == []


def test_filter_by_strategy_group_none_returns_all_rows_unfiltered():
    rows = [_row(Symbol="CSP", Estrategia="Cash-Secured Put"), _row(Symbol="CC", Estrategia="Covered Call")]
    assert filter_by_strategy_group(rows, None) == rows


def test_filter_by_strategy_group_unknown_group_returns_all_rows_unfiltered():
    rows = [_row(Symbol="CSP", Estrategia="Cash-Secured Put")]
    assert filter_by_strategy_group(rows, "not_a_real_group") == rows


# --- exclude_earnings_before_expiration / exclude_fomc_before_expiration (pedido 2026-07-28) ---


def test_apply_filters_excludes_confirmed_earnings_before_expiration():
    rows = [
        _row(Symbol="RISKY", **{"Earnings antes del vencimiento": True}),
        _row(Symbol="SAFE", **{"Earnings antes del vencimiento": False}),
    ]
    result = apply_filters(rows, exclude_earnings_before_expiration=True)
    assert [r["Symbol"] for r in result] == ["SAFE"]


def test_apply_filters_does_not_exclude_unknown_earnings():
    """Dato desconocido (None) no es lo mismo que 'confirmado seguro' — no se excluye, para no
    penalizar candidatos válidos por falta de dato."""
    rows = [_row(Symbol="UNKNOWN", **{"Earnings antes del vencimiento": None})]
    result = apply_filters(rows, exclude_earnings_before_expiration=True)
    assert [r["Symbol"] for r in result] == ["UNKNOWN"]


def test_apply_filters_earnings_filter_off_by_default():
    rows = [_row(Symbol="RISKY", **{"Earnings antes del vencimiento": True})]
    assert apply_filters(rows) == rows


def test_apply_filters_excludes_confirmed_fomc_before_expiration():
    rows = [
        _row(Symbol="RISKY", **{"FOMC antes del vencimiento": True}),
        _row(Symbol="SAFE", **{"FOMC antes del vencimiento": False}),
    ]
    result = apply_filters(rows, exclude_fomc_before_expiration=True)
    assert [r["Symbol"] for r in result] == ["SAFE"]


def test_apply_filters_does_not_exclude_unknown_fomc():
    rows = [_row(Symbol="UNKNOWN", **{"FOMC antes del vencimiento": None})]
    result = apply_filters(rows, exclude_fomc_before_expiration=True)
    assert [r["Symbol"] for r in result] == ["UNKNOWN"]


def test_apply_filters_combines_earnings_and_fomc_filters_with_and():
    rows = [
        _row(Symbol="BOTH_SAFE", **{"Earnings antes del vencimiento": False, "FOMC antes del vencimiento": False}),
        _row(Symbol="RISKY_EARNINGS", **{"Earnings antes del vencimiento": True, "FOMC antes del vencimiento": False}),
        _row(Symbol="RISKY_FOMC", **{"Earnings antes del vencimiento": False, "FOMC antes del vencimiento": True}),
    ]
    result = apply_filters(rows, exclude_earnings_before_expiration=True, exclude_fomc_before_expiration=True)
    assert [r["Symbol"] for r in result] == ["BOTH_SAFE"]
