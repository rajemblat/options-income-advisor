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


# Tolerancias del check de "movimiento similar" (pedido 2026-07-29, refinamiento del check
# histórico): en vez de "¿se movió AL MENOS tanto como necesitaría moverse hoy?" (umbral "o
# más"), busca "¿hubo alguna vez un movimiento de MAGNITUD SIMILAR en un PLAZO similar?" —
# banda de tolerancia en ambas dimensiones. ±1 semana de plazo, ±3 puntos porcentuales de
# magnitud — confirmados con el usuario con un ejemplo concreto antes de implementar.
DEFAULT_DAY_TOLERANCE_DAYS = 7
DEFAULT_PCT_TOLERANCE_POINTS = 3.0


@dataclass
class SimilarMoveCheck:
    """Complementa a `HistoricalMoveCheck` (pedido 2026-07-29) — responde una pregunta
    DISTINTA: no "¿esta posición puntual se hubiera visto perforada?" (umbral "o más", ver
    arriba), sino "¿qué tan común es un movimiento de ESTA magnitud, en un plazo similar?"
    (banda de tolerancia: ±`pct_tolerance_points` puntos porcentuales alrededor del % de
    cobertura de hoy, ±`day_tolerance_days` días alrededor del DTE de hoy). Consecuencia
    concreta: un crash mucho más grande que la cobertura de hoy (ej. -40% cuando la alerta
    necesita -14.3%) NO cuenta como "similar" — por eso `bigger_occurrences` lo muestra aparte,
    para no esconder el escenario más peligroso detrás de un número que solo mira "parecidos"."""

    similar_occurrences: int
    bigger_occurrences: int
    window_days: int
    day_tolerance_days: int
    pct_tolerance_points: float


def historical_similar_move_frequency(
    price_history: list[PriceBar],
    option_type: str,
    strike: float,
    reference_price: float,
    window_days: int,
    day_tolerance_days: int = DEFAULT_DAY_TOLERANCE_DAYS,
    pct_tolerance_points: float = DEFAULT_PCT_TOLERANCE_POINTS,
) -> SimilarMoveCheck | None:
    """Para cada punto de partida posible en los últimos ~5 años, revisa TODOS los días entre
    `window_days - day_tolerance_days` y `window_days + day_tolerance_days` después — si en
    ALGÚN punto de ese rango el movimiento acumulado cae dentro de la banda de tolerancia
    porcentual, es un evento "similar". Si nunca entra a la banda pero el movimiento al final
    del rango de días la superó por arriba, es un evento "más grande todavía" (el peor caso: la
    posición de hoy se hubiera visto perforada con margen de sobra). Mismo agrupamiento de
    rachas consecutivas en un solo evento que `historical_move_frequency` — un evento nuevo
    arranca solo después de un punto de partida que NO fue ni similar ni más grande."""
    if not price_history or reference_price <= 0 or window_days <= 0:
        return None
    required_move_pct = (
        (reference_price - strike) / reference_price if option_type == "put" else (strike - reference_price) / reference_price
    )
    if required_move_pct <= 0:
        return None

    tolerance = pct_tolerance_points / 100
    lower_bound = max(0.0, required_move_pct - tolerance)
    upper_bound = required_move_pct + tolerance
    day_min = max(1, window_days - day_tolerance_days)
    day_max = window_days + day_tolerance_days

    bars = sorted(price_history, key=lambda b: b.trade_date)
    last_date = bars[-1].trade_date

    similar_occurrences = 0
    bigger_occurrences = 0
    in_similar_event = False
    in_bigger_event = False
    evaluated_any = False

    for i, start_bar in enumerate(bars):
        day_max_date = start_bar.trade_date + timedelta(days=day_max)
        if day_max_date > last_date:
            break  # no hay suficiente historia posterior para evaluar todo el rango de días —
            # bars está ordenado ascendente, ninguna ventana posterior va a entrar tampoco.
        start_price = start_bar.close
        if start_price <= 0:
            continue
        day_min_date = start_bar.trade_date + timedelta(days=day_min)

        extreme = start_bar.low if option_type == "put" else start_bar.high
        hit_similar = False
        final_move_pct = 0.0
        for bar in bars[i:]:
            if bar.trade_date > day_max_date:
                break
            extreme = min(extreme, bar.low) if option_type == "put" else max(extreme, bar.high)
            if bar.trade_date < day_min_date:
                continue  # todavía no llegamos al piso del rango de días tolerado
            move_pct = (
                (start_price - extreme) / start_price if option_type == "put" else (extreme - start_price) / start_price
            )
            final_move_pct = move_pct
            if lower_bound <= move_pct <= upper_bound:
                hit_similar = True
        evaluated_any = True

        if hit_similar:
            if not in_similar_event:
                similar_occurrences += 1
            in_similar_event, in_bigger_event = True, False
        else:
            in_similar_event = False
            hit_bigger = final_move_pct > upper_bound
            if hit_bigger and not in_bigger_event:
                bigger_occurrences += 1
            in_bigger_event = hit_bigger

    if not evaluated_any:
        return None
    return SimilarMoveCheck(
        similar_occurrences=similar_occurrences,
        bigger_occurrences=bigger_occurrences,
        window_days=window_days,
        day_tolerance_days=day_tolerance_days,
        pct_tolerance_points=pct_tolerance_points,
    )


def compute_historical_checks(
    broker: BrokerClient, quote_symbol: str, legs: list[dict], underlying_price: float | None, dte: int | None
) -> tuple[HistoricalMoveCheck | None, SimilarMoveCheck | None]:
    """Envoltorio de conveniencia usado por `alerts/engine.py`/`alerts/real_trades.py` al
    generar una alerta: encuentra la pata VENDIDA principal (mismo criterio que
    `alerts/formatting.py::compute_coverage` — la primera con `side="sell"`), pide 5 años de
    historial a Schwab UNA sola vez, y corre AMBOS análisis (umbral "o más" y banda de magnitud
    similar) sobre los mismos datos — evita duplicar la llamada a Schwab por tener 2 checks en
    vez de 1. None/None si falta cualquier dato necesario (sin patas vendidas, sin precio/DTE,
    fallo de red al pedir el historial) — nunca rompe al caller, mismo criterio que el resto de
    datos "opcionales" de una alerta (noticias, earnings, dividendo)."""
    if underlying_price is None or dte is None:
        return None, None
    sold_leg = next((leg for leg in legs if leg.get("side") == "sell"), None)
    if sold_leg is None:
        return None, None
    try:
        price_history = broker.get_price_history(quote_symbol, lookback_days=BACKTEST_LOOKBACK_DAYS)
    except Exception:
        logger.warning("Fallo al pedir historial de 5 años de %s; se omiten los checks históricos", quote_symbol, exc_info=True)
        return None, None
    option_type, strike = sold_leg["option_type"], sold_leg["strike"]
    historical = historical_move_frequency(price_history, option_type, strike, underlying_price, dte)
    similar = historical_similar_move_frequency(price_history, option_type, strike, underlying_price, dte)
    return historical, similar
