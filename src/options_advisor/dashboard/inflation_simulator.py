from __future__ import annotations


def project_inflation_scenarios(
    initial_capital: float,
    inflation_rate_pct: float,
    scenario_rates: dict[str, float],
    years: int,
) -> dict[str, list[dict]]:
    """Proyección año por año de poder adquisitivo real bajo inflación constante (pedido
    2026-07-26), una serie por escenario en `scenario_rates` (ej. {"Sin invertir": 0.0,
    "Banco": 4.0, "Alternativa": 10.0}).

    `nominal_value` es el valor de la cuenta en dólares corrientes (interés compuesto simple).
    `real_value` lo deflacta dividiendo por la inflación compuesta del mismo período — no la
    aproximación "tasa nominal - inflación" (válida solo para tasas chicas), sino el cálculo
    exacto: real = nominal / (1+inflación)^años, equivalente a componer la tasa real de Fisher
    (1+nominal)/(1+inflación) - 1 año a año. `real_change_pct` es la ganancia/pérdida de poder
    adquisitivo acumulada vs. el capital inicial, en %.

    Es una PROYECCIÓN bajo tasas constantes, no una predicción real — la inflación y el
    rendimiento de cualquier inversión varían año a año."""
    inflation = inflation_rate_pct / 100
    result: dict[str, list[dict]] = {}
    for name, rate_pct in scenario_rates.items():
        rate = rate_pct / 100
        rows = []
        for year in range(0, years + 1):
            nominal_value = initial_capital * (1 + rate) ** year
            real_value = nominal_value / (1 + inflation) ** year
            real_change_pct = (real_value - initial_capital) / initial_capital * 100 if initial_capital else 0.0
            rows.append(
                {
                    "year": year,
                    "nominal_value": round(nominal_value, 2),
                    "real_value": round(real_value, 2),
                    "real_change_pct": round(real_change_pct, 4),
                }
            )
        result[name] = rows
    return result
