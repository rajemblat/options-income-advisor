from __future__ import annotations

from datetime import date, timedelta

from options_advisor.broker.models import PriceBar
from options_advisor.strategy.backtest import compute_historical_checks, historical_move_frequency, historical_similar_move_frequency

BASE = date(2024, 1, 1)


def _bar(day_offset: int, close: float, low: float | None = None, high: float | None = None) -> PriceBar:
    return PriceBar(
        symbol="TST",
        trade_date=BASE + timedelta(days=day_offset),
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=1000,
    )


def _flat_series(days: int, price: float = 100.0) -> list[PriceBar]:
    return [_bar(i, price) for i in range(days)]


def test_historical_move_frequency_never_occurs_returns_zero_occurrences():
    """Precio siempre plano en 100 — nunca cae 10% en ninguna ventana de 5 días."""
    bars = _flat_series(30)
    result = historical_move_frequency(bars, "put", strike=90.0, reference_price=100.0, window_days=5)
    assert result is not None
    assert result.occurrences == 0
    assert result.total_windows > 0


def test_historical_move_frequency_detects_dip_within_window():
    """Un solo día con low=85 dentro de la ventana de 5 días desde el día 0 — esa ventana
    (y las que también alcanzan a incluir el día 3) deben contar como ocurrencia."""
    bars = _flat_series(30)
    bars[3] = _bar(3, close=99.0, low=85.0)  # cae a 85 intradía, cierra en 99
    result = historical_move_frequency(bars, "put", strike=90.0, reference_price=100.0, window_days=5)
    assert result is not None
    assert result.occurrences > 0


def test_historical_move_frequency_uses_low_not_close_for_puts():
    """El cierre nunca baja de 100, pero el LOW intradía sí toca 85 — debe contarse igual (se
    usa el rango de la barra, no solo el cierre, pedido explícito)."""
    bars = _flat_series(10)
    bars[2] = _bar(2, close=100.0, low=85.0)  # cierra sin cambios, pero tocó 85 intradía
    result = historical_move_frequency(bars, "put", strike=90.0, reference_price=100.0, window_days=5)
    assert result is not None
    assert result.occurrences >= 1


def test_historical_move_frequency_uses_high_not_close_for_calls():
    bars = _flat_series(10)
    bars[2] = _bar(2, close=100.0, high=115.0)  # cierra sin cambios, pero tocó 115 intradía
    result = historical_move_frequency(bars, "call", strike=110.0, reference_price=100.0, window_days=5)
    assert result is not None
    assert result.occurrences >= 1


def test_historical_move_frequency_call_direction_is_upward():
    """Para calls, una CAÍDA no cuenta como ocurrencia — solo una SUBA hasta el strike."""
    bars = _flat_series(10)
    bars[2] = _bar(2, close=100.0, low=50.0)  # caída fuerte, pero es un CALL — no debería contar
    result = historical_move_frequency(bars, "call", strike=110.0, reference_price=100.0, window_days=5)
    assert result is not None
    assert result.occurrences == 0


def test_historical_move_frequency_none_when_strike_already_itm_for_put():
    """Strike por ENCIMA del precio actual en un put vendido: ya está ITM, el movimiento
    "necesario" sería negativo — no tiene sentido medir "cuántas veces se movió tanto"."""
    bars = _flat_series(10)
    assert historical_move_frequency(bars, "put", strike=110.0, reference_price=100.0, window_days=5) is None


def test_historical_move_frequency_none_when_strike_already_itm_for_call():
    bars = _flat_series(10)
    assert historical_move_frequency(bars, "call", strike=90.0, reference_price=100.0, window_days=5) is None


def test_historical_move_frequency_none_for_empty_history():
    assert historical_move_frequency([], "put", strike=90.0, reference_price=100.0, window_days=5) is None


def test_historical_move_frequency_none_when_no_complete_window_fits():
    """Serie más corta que la ventana pedida — ninguna ventana completa cabe."""
    bars = _flat_series(3)
    assert historical_move_frequency(bars, "put", strike=90.0, reference_price=100.0, window_days=45) is None


def test_historical_move_frequency_excludes_incomplete_trailing_windows():
    """Serie de 10 días (offsets 0..9), ventana de 5 — el último día de inicio válido es el 4
    (termina en el día 9, el último dato disponible); desde el día 5 en adelante la ventana
    terminaría en el día 10+, que no existe, así que se excluyen del total."""
    bars = _flat_series(10)  # días 0..9
    result = historical_move_frequency(bars, "put", strike=90.0, reference_price=100.0, window_days=5)
    assert result is not None
    assert result.total_windows == 5  # días de inicio válidos: 0,1,2,3,4


def test_historical_move_frequency_window_boundary_is_inclusive():
    """Una caída exactamente en window_end (día 5 de una ventana que arranca en día 0, 5 días)
    debe contarse — el límite superior de la ventana es inclusive."""
    bars = _flat_series(10)
    bars[5] = _bar(5, close=100.0, low=85.0)
    result = historical_move_frequency(bars, "put", strike=90.0, reference_price=100.0, window_days=5)
    assert result is not None
    # la ventana que arranca en el día 0 (termina exactamente en el día 5) debe contar la caída
    assert result.occurrences >= 1


def test_historical_move_frequency_handles_weekend_gaps_in_calendar_days():
    """Datos reales tienen huecos de fin de semana — la ventana se mide en días CALENDARIO
    (coincidiendo con cómo se define DTE en el resto de la app), no en cantidad de barras."""
    # Barras solo de lunes a viernes (offsets 0-4), salteando el fin de semana (5,6), retomando
    # el lunes siguiente (7) — igual que datos reales de Schwab.
    bars = [_bar(i, 100.0) for i in [0, 1, 2, 3, 4, 7, 8, 9, 10, 11]]
    bars[5] = _bar(7, close=100.0, low=85.0)  # cae el "lunes" (día calendario 7)
    result = historical_move_frequency(bars, "put", strike=90.0, reference_price=100.0, window_days=7)
    assert result is not None
    assert result.occurrences >= 1


def test_historical_move_frequency_zero_reference_price_returns_none():
    bars = _flat_series(10)
    assert historical_move_frequency(bars, "put", strike=90.0, reference_price=0.0, window_days=5) is None


def test_historical_move_frequency_zero_window_days_returns_none():
    bars = _flat_series(10)
    assert historical_move_frequency(bars, "put", strike=90.0, reference_price=100.0, window_days=0) is None


# --- agrupamiento de rachas en eventos distintos (pedido 2026-07-29) ---


def test_historical_move_frequency_groups_one_dip_seen_by_many_overlapping_windows_as_one_event():
    """Una sola caída puede ser "vista" por varios puntos de partida distintos (cualquier
    ventana que alcance a incluir ese día) — eso debe contar como 1 evento real, no una
    ocurrencia por cada ventana que la ve."""
    bars = _flat_series(30)
    bars[10] = _bar(10, close=99.0, low=80.0)  # una única caída puntual
    result = historical_move_frequency(bars, "put", strike=90.0, reference_price=100.0, window_days=5)
    assert result is not None
    assert result.occurrences == 1
    # el crudo de ventanas afectadas es mayor a 1 (varias ventanas distintas alcanzan a ver el
    # mismo día 10) — confirma que sí había algo que agrupar, no que la caída solo afectó 1 ventana.
    hits = sum(
        1
        for start in range(0, 30 - 5)
        if any(bars[d].low <= 80.0 for d in range(start, min(start + 6, 30)))
    )
    assert hits > 1


def test_historical_move_frequency_counts_two_separated_dips_as_two_events():
    """Dos caídas reales, separadas por un tramo suficientemente largo sin caídas, deben
    contarse como 2 eventos distintos — no fusionarse en uno solo."""
    bars = _flat_series(60)
    bars[5] = _bar(5, close=99.0, low=80.0)
    bars[40] = _bar(40, close=99.0, low=80.0)
    result = historical_move_frequency(bars, "put", strike=90.0, reference_price=100.0, window_days=5)
    assert result is not None
    assert result.occurrences == 2


def test_historical_move_frequency_sustained_multi_day_move_is_still_one_event():
    """Si el precio se queda varios días seguidos por debajo del nivel (no un solo día), sigue
    siendo 1 evento — no uno por día."""
    bars = _flat_series(30)
    for offset in (10, 11, 12, 13):
        bars[offset] = _bar(offset, close=81.0, low=80.0)
    result = historical_move_frequency(bars, "put", strike=90.0, reference_price=100.0, window_days=5)
    assert result is not None
    assert result.occurrences == 1


# --- compute_historical_checks (wrapper usado por engine.py/real_trades.py, pide el
# historial UNA sola vez y corre ambos análisis: umbral "o más" + banda de magnitud similar) ---


class _FakeBrokerForBacktest:
    def __init__(self, bars: list[PriceBar] | None = None, raises: bool = False):
        self._bars = bars or _flat_series(400)
        self._raises = raises

    def get_price_history(self, symbol: str, lookback_days: int) -> list[PriceBar]:
        if self._raises:
            raise RuntimeError("fallo simulado de Schwab")
        return self._bars[-lookback_days:]


def test_compute_historical_checks_picks_first_sell_leg():
    bars = _flat_series(400)
    bars[10] = _bar(10, close=100.0, low=80.0)
    broker = _FakeBrokerForBacktest(bars)
    legs = [
        {"side": "buy", "option_type": "put", "strike": 70.0},  # pata comprada, debe ignorarse
        {"side": "sell", "option_type": "put", "strike": 90.0},
    ]
    historical, similar = compute_historical_checks(broker, "TST", legs, underlying_price=100.0, dte=5)
    assert historical is not None
    assert historical.occurrences >= 1
    assert similar is not None


def test_compute_historical_checks_none_without_sell_leg():
    broker = _FakeBrokerForBacktest()
    legs = [{"side": "buy", "option_type": "put", "strike": 90.0}]
    assert compute_historical_checks(broker, "TST", legs, underlying_price=100.0, dte=5) == (None, None)


def test_compute_historical_checks_none_without_underlying_price():
    broker = _FakeBrokerForBacktest()
    legs = [{"side": "sell", "option_type": "put", "strike": 90.0}]
    assert compute_historical_checks(broker, "TST", legs, underlying_price=None, dte=5) == (None, None)


def test_compute_historical_checks_none_without_dte():
    broker = _FakeBrokerForBacktest()
    legs = [{"side": "sell", "option_type": "put", "strike": 90.0}]
    assert compute_historical_checks(broker, "TST", legs, underlying_price=100.0, dte=None) == (None, None)


def test_compute_historical_checks_none_when_broker_raises():
    broker = _FakeBrokerForBacktest(raises=True)
    legs = [{"side": "sell", "option_type": "put", "strike": 90.0}]
    assert compute_historical_checks(broker, "TST", legs, underlying_price=100.0, dte=5) == (None, None)


def test_compute_historical_checks_empty_legs_returns_none():
    broker = _FakeBrokerForBacktest()
    assert compute_historical_checks(broker, "TST", [], underlying_price=100.0, dte=5) == (None, None)


def test_compute_historical_checks_fetches_price_history_only_once():
    """Antes había 2 wrappers separados, cada uno pidiendo el historial por su cuenta — ahora
    debe ser UNA sola llamada a Schwab para ambos análisis."""
    calls = []

    class _CountingBroker(_FakeBrokerForBacktest):
        def get_price_history(self, symbol, lookback_days):
            calls.append(symbol)
            return super().get_price_history(symbol, lookback_days)

    broker = _CountingBroker()
    legs = [{"side": "sell", "option_type": "put", "strike": 90.0}]
    compute_historical_checks(broker, "TST", legs, underlying_price=100.0, dte=5)
    assert len(calls) == 1


# --- historical_similar_move_frequency (pedido 2026-07-29: refina el check histórico con una
# banda de tolerancia de plazo Y magnitud, en vez de un umbral "o más") ---


def test_historical_similar_move_frequency_counts_move_within_band():
    """Caída del 15% (dentro de la banda 11.3%-17.3% para un requerido de 14.3%) — cuenta como
    "similar", no como "más grande". La caída tiene que caer dentro de [start+38, start+52]
    días para que la entrada del día 0 la vea (ventana de 45 días ±7 de tolerancia) — día 45
    cae justo en el medio de ese rango."""
    bars = _flat_series(120)
    bars[45] = _bar(45, close=100.0, low=85.0)  # 15% de caída
    result = historical_similar_move_frequency(bars, "put", strike=85.7, reference_price=100.0, window_days=45)
    assert result is not None
    assert result.similar_occurrences == 1
    assert result.bigger_occurrences == 0


def test_historical_similar_move_frequency_big_crash_counts_as_bigger_not_similar():
    """Caída del 40% (muy por encima de la banda 11.3%-17.3% para un requerido de 14.3%) — NO
    debe contar como "similar" (sería engañoso, un evento mucho peor que "parecido"), pero SÍ
    debe aparecer en bigger_occurrences (el escenario más peligroso, mostrado aparte)."""
    bars = _flat_series(120)
    bars[45] = _bar(45, close=100.0, low=60.0)  # 40% de caída
    result = historical_similar_move_frequency(bars, "put", strike=85.7, reference_price=100.0, window_days=45)
    assert result is not None
    assert result.similar_occurrences == 0
    assert result.bigger_occurrences == 1


def test_historical_similar_move_frequency_small_move_counts_as_neither():
    """Caída del 5% (por debajo de la banda 11.3%-17.3%) — ni similar ni más grande."""
    bars = _flat_series(120)
    bars[45] = _bar(45, close=100.0, low=95.0)  # 5% de caída
    result = historical_similar_move_frequency(bars, "put", strike=85.7, reference_price=100.0, window_days=45)
    assert result is not None
    assert result.similar_occurrences == 0
    assert result.bigger_occurrences == 0


def test_historical_similar_move_frequency_respects_day_tolerance():
    """Una caída que solo se ve completa varios días DESPUÉS del window_days exacto (pero
    dentro del margen de ±day_tolerance_days) debe contar igual — antes (umbral exacto) se
    hubiera perdido este caso."""
    bars = _flat_series(120)
    bars[48] = _bar(48, close=100.0, low=85.0)  # día 48, fuera de una ventana de EXACTAMENTE 45
    result = historical_similar_move_frequency(
        bars, "put", strike=85.7, reference_price=100.0, window_days=45, day_tolerance_days=7
    )
    assert result is not None
    assert result.similar_occurrences == 1


def test_historical_similar_move_frequency_groups_consecutive_similar_events():
    bars = _flat_series(150)
    for offset in (45, 46, 47):
        bars[offset] = _bar(offset, close=85.0, low=85.0)  # caída sostenida ~15%
    result = historical_similar_move_frequency(bars, "put", strike=85.7, reference_price=100.0, window_days=45)
    assert result is not None
    assert result.similar_occurrences == 1


def test_historical_similar_move_frequency_none_when_strike_already_itm():
    bars = _flat_series(120)
    assert historical_similar_move_frequency(bars, "put", strike=110.0, reference_price=100.0, window_days=45) is None


def test_historical_similar_move_frequency_none_for_empty_history():
    assert historical_similar_move_frequency([], "put", strike=90.0, reference_price=100.0, window_days=45) is None


def test_historical_similar_move_frequency_none_when_insufficient_trailing_data():
    """Necesita datos hasta window_days + day_tolerance_days después del último punto de
    partida posible — con muy pocos días de historia, ninguna ventana se puede evaluar."""
    bars = _flat_series(10)
    assert historical_similar_move_frequency(bars, "put", strike=85.7, reference_price=100.0, window_days=45) is None
