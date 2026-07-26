from __future__ import annotations

import pytest

from options_advisor.dashboard.compound_interest import project_compound_growth


def test_project_compound_growth_matches_worked_example():
    """Ejemplo del pedido 2026-07-24: $10,000 inicial, 20% anual, $2,000 de aporte anual, 3
    años. Calculado a mano: año1 10000*1.2+2000=14000, año2 14000*1.2+2000=18800,
    año3 18800*1.2+2000=24560."""
    rows = project_compound_growth(initial_capital=10_000, annual_rate_pct=20.0, annual_contribution=2_000, years=3)
    assert [r["value"] for r in rows] == [10_000, 14_000, 18_800, 24_560]
    assert [r["year"] for r in rows] == [0, 1, 2, 3]


def test_project_compound_growth_zero_contribution_is_pure_compounding():
    rows = project_compound_growth(initial_capital=10_000, annual_rate_pct=10.0, annual_contribution=0.0, years=2)
    assert rows[-1]["value"] == pytest.approx(10_000 * 1.1 * 1.1, abs=0.01)


def test_project_compound_growth_zero_rate_is_just_additive():
    rows = project_compound_growth(initial_capital=10_000, annual_rate_pct=0.0, annual_contribution=1_000, years=3)
    assert rows[-1]["value"] == 13_000.0


def test_project_compound_growth_one_year_horizon():
    rows = project_compound_growth(initial_capital=5_000, annual_rate_pct=15.0, annual_contribution=500, years=1)
    assert len(rows) == 2
    assert rows[1]["value"] == pytest.approx(5_000 * 1.15 + 500, abs=0.01)
