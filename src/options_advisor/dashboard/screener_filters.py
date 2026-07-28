from __future__ import annotations

# Sección "Pestaña Screener" (pedido 2026-07-27): buscador de opciones con filtros ajustables
# sobre las filas ya armadas por dashboard/scanner_table.py::build_scanner_rows(). Lógica pura,
# sin Streamlit — testeable sin runtime de UI, mismo patrón que scanner_table.py.

# Umbrales de Volumen/Open Interest — PRIMER PASO documentado como tal (no percentiles reales
# sobre la distribución observada, que sería más riguroso pero requiere más tiempo de
# calibración): volumen de opciones típicamente corre en decenas/cientos por día salvo
# nombres muy líquidos, mientras que Open Interest se acumula con el tiempo y corre en
# cientos/miles incluso en nombres chicos — de ahí escalas distintas para cada uno.
VOLUME_BUCKETS = {
    "Muy bajo": (0, 10),
    "Bajo": (10, 50),
    "Medio": (50, 200),
    "Alto": (200, 1000),
    "Muy alto": (1000, float("inf")),
}
OPEN_INTEREST_BUCKETS = {
    "Muy bajo": (0, 50),
    "Bajo": (50, 500),
    "Medio": (500, 2000),
    "Alto": (2000, 10000),
    "Muy alto": (10000, float("inf")),
}
# Moneyness (%) ya viene con el mismo signo que compute_coverage: positivo = OTM (a favor del
# vendedor). Cortes simétricos alrededor de 0 = ATM.
MONEYNESS_BUCKETS = {
    "Deep ITM": (float("-inf"), -15.0),
    "ITM": (-15.0, -5.0),
    "ATM": (-5.0, 5.0),
    "OTM": (5.0, 15.0),
    "Deep OTM": (15.0, float("inf")),
}
# Delta en valor absoluto (mismo criterio para put/call — 0.25 delta es "0.25 delta" sea cual
# sea el signo).
DELTA_BUCKETS = {
    "Bajo (0-0.25)": (0.0, 0.25),
    "Medio (0.25-0.50)": (0.25, 0.50),
    "Alto (0.50-0.75)": (0.50, 0.75),
    "Muy alto (>0.75)": (0.75, float("inf")),
}


def _bucket_of(value: float | None, buckets: dict[str, tuple[float, float]]) -> str | None:
    if value is None:
        return None
    for name, (lo, hi) in buckets.items():
        if lo <= value < hi:
            return name
    return None


def classify_volume_bucket(volume: float | None) -> str | None:
    return _bucket_of(volume, VOLUME_BUCKETS)


def classify_open_interest_bucket(open_interest: float | None) -> str | None:
    return _bucket_of(open_interest, OPEN_INTEREST_BUCKETS)


def classify_moneyness_bucket(moneyness_pct: float | None) -> str | None:
    return _bucket_of(moneyness_pct, MONEYNESS_BUCKETS)


def classify_delta_bucket(delta: float | None) -> str | None:
    return _bucket_of(abs(delta), DELTA_BUCKETS) if delta is not None else None


def apply_filters(
    rows: list[dict],
    dte_range: tuple[int, int] | None = None,
    strike_range: tuple[float, float] | None = None,
    delta_buckets: list[str] | None = None,
    volume_buckets: list[str] | None = None,
    open_interest_buckets: list[str] | None = None,
    moneyness_buckets: list[str] | None = None,
    instrument_types: list[str] | None = None,
    min_probability_otm: float | None = None,
) -> list[dict]:
    """Filtro combinado (AND de todos los criterios activos) sobre las filas de
    `build_scanner_rows`. Cada filtro es opcional (None/lista vacía = sin restricción en ese
    criterio) — una fila con un dato faltante (None) para un filtro ACTIVO se excluye (no hay
    forma de saber si cumple, se trata igual que "no cumple", nunca se cuela por falta de dato)."""
    result = []
    for row in rows:
        if dte_range is not None:
            dte = row.get("DTE")
            if dte is None or not (dte_range[0] <= dte <= dte_range[1]):
                continue
        if strike_range is not None:
            strike = row.get("Strike")
            if strike is None or not (strike_range[0] <= strike <= strike_range[1]):
                continue
        if delta_buckets:
            if classify_delta_bucket(row.get("Delta")) not in delta_buckets:
                continue
        if volume_buckets:
            if classify_volume_bucket(row.get("Volume")) not in volume_buckets:
                continue
        if open_interest_buckets:
            if classify_open_interest_bucket(row.get("Open Interest")) not in open_interest_buckets:
                continue
        if moneyness_buckets:
            if classify_moneyness_bucket(row.get("Moneyness (%)")) not in moneyness_buckets:
                continue
        if instrument_types:
            if row.get("Instrumento") not in instrument_types:
                continue
        if min_probability_otm is not None:
            prob_otm = row.get("Probabilidad OTM (%)")
            if prob_otm is None or prob_otm < min_probability_otm:
                continue
        result.append(row)
    return result
