from __future__ import annotations

import pytest

from options_advisor.dashboard.inflation_simulator import project_inflation_scenarios


def test_rate_equal_to_inflation_preserves_real_value():
    """Si la tasa nominal iguala exactamente la inflación, el poder adquisitivo real no cambia
    (ni gana ni pierde) en ningún año."""
    result = project_inflation_scenarios(1_000.0, inflation_rate_pct=10.0, scenario_rates={"Banco": 10.0}, years=3)
    rows = result["Banco"]
    assert all(r["real_value"] == 1_000.0 for r in rows)
    assert all(r["real_change_pct"] == 0.0 for r in rows)


def test_zero_nominal_rate_loses_purchasing_power_to_inflation():
    """Dinero sin invertir (0% nominal) con inflación positiva: el valor nominal no se mueve,
    pero el real cae año a año."""
    result = project_inflation_scenarios(1_000.0, inflation_rate_pct=5.0, scenario_rates={"Sin invertir": 0.0}, years=2)
    rows = result["Sin invertir"]
    assert [r["nominal_value"] for r in rows] == [1_000.0, 1_000.0, 1_000.0]
    assert rows[1]["real_value"] == pytest.approx(1_000.0 / 1.05, abs=0.01)
    assert rows[2]["real_value"] == pytest.approx(1_000.0 / 1.05**2, abs=0.01)
    assert rows[1]["real_change_pct"] < 0
    assert rows[2]["real_change_pct"] < rows[1]["real_change_pct"]  # empeora con el tiempo


def test_rate_above_inflation_gains_real_purchasing_power():
    result = project_inflation_scenarios(1_000.0, inflation_rate_pct=3.0, scenario_rates={"Alternativa": 10.0}, years=1)
    row = result["Alternativa"][1]
    assert row["nominal_value"] == pytest.approx(1_100.0, abs=0.01)
    assert row["real_value"] == pytest.approx(1_100.0 / 1.03, abs=0.01)
    assert row["real_change_pct"] > 0


def test_multiple_scenarios_computed_independently():
    result = project_inflation_scenarios(
        1_000.0, inflation_rate_pct=5.0, scenario_rates={"Sin invertir": 0.0, "Banco": 4.0, "Alternativa": 10.0}, years=1
    )
    assert set(result.keys()) == {"Sin invertir", "Banco", "Alternativa"}
    assert result["Sin invertir"][1]["real_change_pct"] < result["Banco"][1]["real_change_pct"] < result["Alternativa"][1]["real_change_pct"]


def test_year_zero_row_equals_initial_capital_in_every_scenario():
    result = project_inflation_scenarios(5_000.0, inflation_rate_pct=4.0, scenario_rates={"Sin invertir": 0.0, "Banco": 6.0}, years=5)
    for rows in result.values():
        assert rows[0] == {"year": 0, "nominal_value": 5_000.0, "real_value": 5_000.0, "real_change_pct": 0.0}


def test_zero_initial_capital_does_not_divide_by_zero():
    result = project_inflation_scenarios(0.0, inflation_rate_pct=5.0, scenario_rates={"Banco": 4.0}, years=2)
    assert all(r["real_change_pct"] == 0.0 for r in result["Banco"])
