from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from options_advisor.broker.models import OptionChain, OptionContract, PriceBar
from options_advisor.config import SimulatorSettings
from options_advisor.indicators import levels
from options_advisor.storage.models import IndicatorSnapshot

# Simulador de Trading Automático (paper trading, pedido 2026-08-02): los 8 criterios de
# entrada de Naked Put (único alcance inicial), evaluados sobre lo que el pipeline diario ya
# calculó (indicators/pipeline.py::analyze_symbol) — sin pedir nada nuevo al broker más que la
# cadena de opciones, que también ya viene del análisis diario.


@dataclass
class EntryEvaluation:
    """Resultado de evaluar los 8 criterios para un símbolo en un día dado. `reasons` acumula
    TODOS los criterios que fallaron (no solo el primero) — pensado para loguear/mostrar por
    qué un símbolo no calificó hoy, no solo que no calificó."""

    symbol: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    contract: OptionContract | None = None
    premium: float | None = None


def _check_strong_support(price_history: list[PriceBar], current_price: float, settings: SimulatorSettings) -> bool:
    """Criterio 1: soporte fuerte (cluster con >=2 toques) en semanal Y diario, con el precio
    actual dentro de la banda de distancia configurada por encima de cada uno."""
    if current_price <= 0:
        return False
    daily_supports, _ = levels.find_strong_support_resistance(price_history, current_price)
    weekly_supports, _ = levels.find_weekly_strong_support_resistance(price_history, current_price)
    daily_ok = any((current_price - s) / current_price <= settings.support_max_distance_pct for s in daily_supports)
    weekly_ok = any((current_price - s) / current_price <= settings.weekly_support_max_distance_pct for s in weekly_supports)
    return daily_ok and weekly_ok


def _check_rsi(rsi: float | None, settings: SimulatorSettings) -> bool:
    """Criterio 2: RSI dentro del rango configurado (default 30-40)."""
    if rsi is None:
        return False
    lo, hi = settings.rsi_range
    return lo <= rsi <= hi


def _check_iv_rank(iv_rank: float | None, settings: SimulatorSettings) -> bool:
    """Criterios 3 y 7: IV Rank > umbral. IV Percentile se confirmó con el usuario (2026-08-02)
    como el mismo concepto que IV Rank en este motor — no hay un chequeo separado."""
    return iv_rank is not None and iv_rank > settings.iv_rank_min


def _check_below_smas(price: float, snapshot: IndicatorSnapshot, settings: SimulatorSettings) -> bool:
    """Criterio 8: precio por debajo de CADA SMA de referencia (8/20/50 por defecto), con al
    menos `sma_min_distance_pct` de distancia — "lejos", no solo cruzado por poco."""
    sma_by_period = {8: snapshot.sma_8, 20: snapshot.sma_20, 50: snapshot.sma_50}
    for period in settings.sma_periods:
        sma = sma_by_period.get(period)
        if sma is None or sma <= 0:
            return False
        if (sma - price) / sma < settings.sma_min_distance_pct:
            return False
    return True


def _no_earnings_before_expiration(expiration: date, next_earnings_date: date | None) -> bool:
    """Criterio 4, como gate DURO (a diferencia del filtro opcional del Screener,
    dashboard/screener_filters.py): earnings desconocido no bloquea (no hay forma de saber si
    es seguro), earnings confirmado que cae en o antes del vencimiento sí."""
    if next_earnings_date is None:
        return True
    return next_earnings_date > expiration


def _dte_and_delta_eligible_puts(chain: OptionChain, as_of: date, settings: SimulatorSettings) -> list[OptionContract]:
    """Criterios 5 y 6: puts entre `dte_range` DTE con delta absoluto por debajo de `max_delta`,
    de CUALQUIER vencimiento en la ventana — la comparación de "mejor prima" (criterio 5) se
    hace después, sobre todo este universo a la vez, no solo dentro de un vencimiento."""
    min_dte, max_dte = settings.dte_range
    result = []
    for ct in chain.contracts:
        if ct.option_type != "put":
            continue
        dte = (ct.expiration - as_of).days
        if not (min_dte <= dte <= max_dte):
            continue
        if abs(ct.greeks.delta) >= settings.max_delta:
            continue
        result.append(ct)
    return result


def evaluate_entry(
    symbol: str,
    snapshot: IndicatorSnapshot,
    chain: OptionChain,
    price_history: list[PriceBar],
    settings: SimulatorSettings,
) -> EntryEvaluation:
    """Evalúa los 8 criterios de entrada de Naked Put para `symbol` en el día de `snapshot`.
    `passed=True` solo si TODOS se cumplen, con el contrato de MAYOR prima entre todos los que
    cumplen DTE/delta/sin-earnings (criterio 5, "mejor prima" — confirmado con el usuario
    2026-08-02: mayor crédito en dólares, no el más cercano al delta objetivo)."""
    reasons: list[str] = []
    if not _check_strong_support(price_history, snapshot.price, settings):
        reasons.append("Sin soporte fuerte (semanal y diario) cerca del precio actual")
    if not _check_rsi(snapshot.rsi_14, settings):
        reasons.append(f"RSI fuera de {settings.rsi_range} (actual: {snapshot.rsi_14})")
    if not _check_iv_rank(snapshot.iv_rank, settings):
        reasons.append(f"IV Rank <= {settings.iv_rank_min} (actual: {snapshot.iv_rank})")
    if not _check_below_smas(snapshot.price, snapshot, settings):
        reasons.append(f"Precio no está >= {settings.sma_min_distance_pct:.0%} por debajo de cada SMA de referencia")

    dte_delta_ok = _dte_and_delta_eligible_puts(chain, snapshot.snapshot_date, settings)
    if not dte_delta_ok:
        reasons.append(f"Sin puts en {settings.dte_range} DTE con delta < {settings.max_delta}")
        return EntryEvaluation(symbol=symbol, passed=False, reasons=reasons)

    no_earnings = [ct for ct in dte_delta_ok if _no_earnings_before_expiration(ct.expiration, snapshot.next_earnings_date)]
    if not no_earnings:
        reasons.append("Earnings antes del vencimiento en todos los contratos elegibles")
        return EntryEvaluation(symbol=symbol, passed=False, reasons=reasons)

    best = max(no_earnings, key=lambda ct: ct.mid_price)
    if reasons:
        return EntryEvaluation(symbol=symbol, passed=False, reasons=reasons)
    return EntryEvaluation(symbol=symbol, passed=True, contract=best, premium=best.mid_price)
