from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from options_advisor.broker.base import BrokerClient
from options_advisor.broker.models import PriceBar

logger = logging.getLogger(__name__)

# 5 años de trading ≈ 252 sesiones/año × 5 ≈ 1260 — margen prudente para no cortar de menos
# (confirmado en vivo contra Schwab real: period=5 años devolvió 1255 barras diarias reales
# para AAPL, 2021-07-27 a 2026-07-27).
BACKTEST_LOOKBACK_DAYS = 1300


@dataclass
class HistoricalMoveCheck:
    """Sección 'check histórico' (pedido 2026-07-28, refinado 2026-07-29): de todas las
    ventanas de `window_days` días CALENDARIO posibles en los últimos ~5 años de barras
    diarias reales, cuántas veces el precio se movió al menos tanto como necesitaría moverse
    HOY para llegar al strike de esta alerta puntual. Es análisis HISTÓRICO — lo que pasó
    antes no garantiza que no vuelva a pasar (ni que sí), el mercado puede comportarse
    distinto en el futuro.

    `occurrences` cuenta EVENTOS DISTINTOS, no ventanas técnicas: si el precio toca el nivel y
    se queda ahí (o sigue moviéndose) varios días seguidos, todas las ventanas que se solapan
    viendo esa misma caída/suba se agrupan en 1 solo evento (pedido explícito 2026-07-29 — el
    conteo crudo de ventanas infla el número contando la misma caída una vez por cada punto de
    partida que alcanza a verla). `total_windows` se conserva como referencia interna del
    tamaño de la muestra, pero ya no se expone en el badge."""

    occurrences: int
    total_windows: int
    window_days: int


def historical_move_frequency(
    price_history: list[PriceBar], option_type: str, strike: float, reference_price: float, window_days: int
) -> HistoricalMoveCheck | None:
    """Usa el RANGO de cada barra (low para puts, high para calls) — no solo el cierre — para
    capturar si el precio tocó ese nivel en algún momento dentro de la ventana, no solo al
    cierre del día. None si no hay datos suficientes para evaluar ni una sola ventana completa
    de `window_days`, o si el strike ya está ITM (el movimiento "necesario" sería 0 o negativo,
    "cuántas veces se movió tanto" no tiene sentido para eso).

    `occurrences` agrupa rachas de ventanas consecutivas que cumplen la condición en un solo
    evento: un evento nuevo arranca solo cuando aparece una ventana que cumple justo después de
    una (o más) que no cumplió — es decir, cuando el precio se recupera y vuelve a caer/subir
    en un episodio distinto. Esto evita que una única caída sostenida se cuente decenas de
    veces solo porque muchos puntos de partida distintos alcanzan a verla."""
    if not price_history or reference_price <= 0 or window_days <= 0:
        return None
    required_move_pct = (
        (reference_price - strike) / reference_price if option_type == "put" else (strike - reference_price) / reference_price
    )
    if required_move_pct <= 0:
        return None

    bars = sorted(price_history, key=lambda b: b.trade_date)
    last_date = bars[-1].trade_date

    total_windows = 0
    occurrences = 0
    in_event = False
    for i, start_bar in enumerate(bars):
        window_end = start_bar.trade_date + timedelta(days=window_days)
        if window_end > last_date:
            break  # ventana incompleta cerca del final de los datos disponibles — bars está
            # ordenado ascendente, ninguna ventana posterior va a entrar tampoco — se corta acá.
        start_price = start_bar.close
        if start_price <= 0:
            continue
        extreme = start_bar.low if option_type == "put" else start_bar.high
        for bar in bars[i:]:
            if bar.trade_date > window_end:
                break
            extreme = min(extreme, bar.low) if option_type == "put" else max(extreme, bar.high)
        actual_move_pct = (
            (start_price - extreme) / start_price if option_type == "put" else (extreme - start_price) / start_price
        )
        total_windows += 1
        hit = actual_move_pct >= required_move_pct
        if hit and not in_event:
            occurrences += 1  # arranca una racha nueva — un evento distinto
        in_event = hit

    if total_windows == 0:
        return None
    return HistoricalMoveCheck(occurrences=occurrences, total_windows=total_windows, window_days=window_days)


def compute_historical_move_check(
    broker: BrokerClient, quote_symbol: str, legs: list[dict], underlying_price: float | None, dte: int | None
) -> HistoricalMoveCheck | None:
    """Envoltorio de conveniencia usado por `alerts/engine.py`/`alerts/real_trades.py` al
    generar una alerta: encuentra la pata VENDIDA principal (mismo criterio que
    `alerts/formatting.py::compute_coverage` — la primera con `side="sell"`), pide 5 años de
    historial a Schwab, y corre `historical_move_frequency`. None si falta cualquier dato
    necesario (sin patas vendidas, sin precio/DTE, fallo de red al pedir el historial) — nunca
    rompe al caller, mismo criterio que el resto de datos "opcionales" de una alerta (noticias,
    earnings, dividendo)."""
    if underlying_price is None or dte is None:
        return None
    sold_leg = next((leg for leg in legs if leg.get("side") == "sell"), None)
    if sold_leg is None:
        return None
    try:
        price_history = broker.get_price_history(quote_symbol, lookback_days=BACKTEST_LOOKBACK_DAYS)
    except Exception:
        logger.warning("Fallo al pedir historial de 5 años de %s; se omite el check histórico", quote_symbol, exc_info=True)
        return None
    return historical_move_frequency(price_history, sold_leg["option_type"], sold_leg["strike"], underlying_price, dte)
