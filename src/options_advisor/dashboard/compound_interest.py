from __future__ import annotations


def project_compound_growth(initial_capital: float, annual_rate_pct: float, annual_contribution: float, years: int) -> list[dict]:
    """Proyección año por año de interés compuesto con aportes anuales (pedido 2026-07-24).
    Cada aporte se suma al FINAL del año en que se hace (convención de anualidad ordinaria) —
    el aporte del año 1 crece compuesto los `years - 1` años restantes, el del último año no
    crece más. capital_final = capital_inicial*(1+r)^n + aporte_anual*[((1+r)^n - 1)/r].

    Es una PROYECCIÓN bajo supuestos constantes (misma tasa todos los años, aporte fijo) — el
    rendimiento real varía año a año, no es una garantía. Devuelve una fila por año (0 =
    capital inicial, sin aporte todavía) con el valor de la cuenta a fin de ese año."""
    rate = annual_rate_pct / 100
    rows = [{"year": 0, "value": round(initial_capital, 2)}]
    balance = initial_capital
    for year in range(1, years + 1):
        balance = balance * (1 + rate) + annual_contribution
        rows.append({"year": year, "value": round(balance, 2)})
    return rows
