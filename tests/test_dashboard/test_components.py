from __future__ import annotations

from datetime import date

from options_advisor.broker.models import Mover, Quote
from options_advisor.dashboard.components import (
    CRITICAL,
    GOOD,
    MOVERS_MIN_SIGNIFICANT_PCT,
    WARNING,
    _capital_at_risk_caveat_html,
    _historical_move_caveat_html,
    _movers_from_quotes,
    _similar_move_caveat_html,
    classify_volatility_level,
    filter_by_date_range,
    filter_significant_movers,
    build_trade_table_rows,
    group_roll_pairs,
    primary_trade_for_group,
    split_gainers_losers,
)


def _mover(symbol: str, change_pct: float) -> Mover:
    direction = "up" if change_pct >= 0 else "down"
    return Mover(symbol=symbol, description=symbol, last_price=100.0, change_pct=change_pct, direction=direction, total_volume=1000)


def test_capital_at_risk_critical_when_max_loss_exceeds_capital():
    html = _capital_at_risk_caveat_html(590_000.0, 50_000.0)
    assert "Esta posición sola arriesga $590,000.00" in html
    assert "1180%" in html
    assert "Muy por encima de tu tamaño de cuenta" in html


def test_capital_at_risk_warning_above_quarter_of_capital():
    html = _capital_at_risk_caveat_html(15_000.0, 50_000.0)
    assert "Riesgo real" in html
    assert "30%" in html


def test_capital_at_risk_empty_when_small_relative_to_capital():
    assert _capital_at_risk_caveat_html(500.0, 50_000.0) == ""


def test_capital_at_risk_empty_for_unbounded_loss():
    assert _capital_at_risk_caveat_html(float("inf"), 50_000.0) == ""


def test_capital_at_risk_empty_without_capital_available():
    assert _capital_at_risk_caveat_html(590_000.0, None) == ""
    assert _capital_at_risk_caveat_html(590_000.0, 0.0) == ""


def test_capital_at_risk_empty_without_max_loss():
    assert _capital_at_risk_caveat_html(None, 50_000.0) == ""


# --- classify_volatility_level (semáforo de volatilidad basado en VIX) ---


def test_classify_volatility_low_below_15():
    label, color = classify_volatility_level(12.3)
    assert label == "Volatilidad baja"
    assert color == GOOD


def test_classify_volatility_normal_between_15_and_25():
    label, color = classify_volatility_level(18.5)
    assert label == "Volatilidad normal"
    assert color == WARNING


def test_classify_volatility_high_above_25():
    label, color = classify_volatility_level(31.0)
    assert label == "Volatilidad alta"
    assert color == CRITICAL


def test_classify_volatility_boundary_exactly_15_is_normal_not_low():
    label, _ = classify_volatility_level(15.0)
    assert label == "Volatilidad normal"


def test_classify_volatility_boundary_exactly_25_is_alta_not_normal():
    label, _ = classify_volatility_level(25.0)
    assert label == "Volatilidad alta"


# --- split_gainers_losers (bug real 2026-07-28: mismas empresas en Ganadoras Y Perdedoras) ---


def test_split_gainers_losers_separates_by_sign():
    movers = [_mover("NVDA", -0.46), _mover("MDT", 2.68), _mover("GLW", -15.28), _mover("PYPL", 0.55)]
    gainers, losers = split_gainers_losers(movers)
    assert [m.symbol for m in gainers] == ["MDT", "PYPL"]
    assert [m.symbol for m in losers] == ["GLW", "NVDA"]


def test_split_gainers_losers_no_symbol_appears_in_both():
    movers = [_mover("A", 1.0), _mover("B", -1.0), _mover("C", 5.0), _mover("D", -5.0)]
    gainers, losers = split_gainers_losers(movers)
    assert set(m.symbol for m in gainers).isdisjoint(m.symbol for m in losers)


def test_split_gainers_losers_gainers_sorted_descending():
    movers = [_mover("A", 1.0), _mover("B", 5.0), _mover("C", 2.5)]
    gainers, _ = split_gainers_losers(movers)
    assert [m.symbol for m in gainers] == ["B", "C", "A"]


def test_split_gainers_losers_losers_sorted_most_negative_first():
    movers = [_mover("A", -1.0), _mover("B", -5.0), _mover("C", -2.5)]
    _, losers = split_gainers_losers(movers)
    assert [m.symbol for m in losers] == ["B", "C", "A"]


def test_split_gainers_losers_zero_change_excluded_from_both():
    movers = [_mover("FLAT", 0.0), _mover("UP", 1.0), _mover("DOWN", -1.0)]
    gainers, losers = split_gainers_losers(movers)
    assert "FLAT" not in [m.symbol for m in gainers]
    assert "FLAT" not in [m.symbol for m in losers]


def test_split_gainers_losers_empty_input():
    assert split_gainers_losers([]) == ([], [])


# --- _historical_move_caveat_html ("check histórico", pedido 2026-07-28) ---


def test_historical_move_caveat_empty_without_data():
    assert _historical_move_caveat_html(None, None, 45) == ""


def test_historical_move_caveat_empty_without_total_windows():
    assert _historical_move_caveat_html(0, 0, 45) == ""  # total_windows=0 no debería persistirse, pero por las dudas


def test_historical_move_caveat_zero_occurrences_shows_green_check():
    html = _historical_move_caveat_html(0, 1250, 45)
    assert "Nunca tocó este nivel" in html
    assert GOOD in html


def test_historical_move_caveat_nonzero_occurrences_shows_simple_count():
    html = _historical_move_caveat_html(8, 1250, 45)
    assert "el precio tocó este nivel 8 veces" in html
    assert "ventanas" not in html
    assert "%" not in html
    assert WARNING in html


def test_historical_move_caveat_singular_for_one_occurrence():
    html = _historical_move_caveat_html(1, 1250, 45)
    assert "el precio tocó este nivel 1 vez" in html
    assert "1 veces" not in html


def test_historical_move_caveat_always_clarifies_its_historical_not_a_guarantee():
    """Aclaración explícita pedida por el usuario — tiene que aparecer en AMBOS casos (0
    ocurrencias y con ocurrencias), no solo en uno."""
    assert "no garantiza el futuro" in _historical_move_caveat_html(0, 1250, 45)
    assert "no garantiza el futuro" in _historical_move_caveat_html(8, 1250, 45)
    assert "histórico" in _historical_move_caveat_html(0, 1250, 45)
    assert "histórico" in _historical_move_caveat_html(8, 1250, 45)


# --- _similar_move_caveat_html (refinamiento del check histórico, pedido 2026-07-29: banda de
# tolerancia de plazo/magnitud, mostrado APARTE del badge de arriba, no en su reemplazo) ---


def test_similar_move_caveat_empty_without_data():
    assert _similar_move_caveat_html(None, None) == ""


def test_similar_move_caveat_zero_shows_green_check():
    html = _similar_move_caveat_html(0, 0)
    assert GOOD in html
    assert "pasó 0 veces" in html


def test_similar_move_caveat_shows_similar_count():
    html = _similar_move_caveat_html(4, 0)
    assert "pasó 4 veces" in html
    assert WARNING in html
    assert "AÚN MÁS GRANDE" not in html  # sin crashes mayores, no debe mencionarlos


def test_similar_move_caveat_singular_for_one_similar_occurrence():
    html = _similar_move_caveat_html(1, 0)
    assert "pasó 1 vez" in html
    assert "1 veces" not in html


def test_similar_move_caveat_mentions_bigger_crashes_when_present():
    """El escenario más peligroso (crashes más grandes que la banda "similar") no debe quedar
    escondido — pedido explícito del usuario tras la aclaración de que la banda de tolerancia
    deja afuera los crashes grandes."""
    html = _similar_move_caveat_html(2, 3)
    assert "pasó 2 veces" in html
    assert "hubo 3 veces" in html
    assert "AÚN MÁS GRANDE" in html


def test_similar_move_caveat_singular_for_one_bigger_occurrence():
    html = _similar_move_caveat_html(0, 1)
    assert "hubo 1 vez" in html
    assert "1 veces una caída" not in html


def test_similar_move_caveat_always_clarifies_its_historical_not_a_guarantee():
    assert "no garantiza el futuro" in _similar_move_caveat_html(0, 0)
    assert "no garantiza el futuro" in _similar_move_caveat_html(4, 2)
    assert "histórico" in _similar_move_caveat_html(0, 0)
    assert "histórico" in _similar_move_caveat_html(4, 2)


def test_similar_move_caveat_mentions_tolerance_bands():
    html = _similar_move_caveat_html(1, 0)
    assert "±3 puntos" in html
    assert "±7 días" in html


# --- filter_by_date_range (filtro de rango compartido por Operaciones y Alertas, 2026-07-29) ---


def _trade(trade_date: str, symbol: str = "TST") -> dict:
    return {"trade_date": trade_date, "symbol": symbol}


TODAY = date(2026, 7, 29)


def test_filter_by_date_range_todo_returns_everything_unfiltered():
    trades = [_trade("2026-07-29"), _trade("2026-01-01"), _trade("2020-05-05")]
    assert filter_by_date_range(trades, "Todo", TODAY) == trades


def test_filter_by_date_range_hoy_excludes_previous_days():
    trades = [_trade("2026-07-29"), _trade("2026-07-28")]
    result = filter_by_date_range(trades, "Hoy", TODAY)
    assert result == [_trade("2026-07-29")]


def test_filter_by_date_range_ultima_semana_boundary_is_inclusive():
    """7 días exactos atrás debe quedar INCLUIDO (cutoff = today - 7, comparación >=)."""
    trades = [_trade("2026-07-22"), _trade("2026-07-21")]
    result = filter_by_date_range(trades, "Última semana", TODAY)
    assert result == [_trade("2026-07-22")]


def test_filter_by_date_range_ultimos_15_dias():
    trades = [_trade("2026-07-14"), _trade("2026-07-13")]
    result = filter_by_date_range(trades, "Últimos 15 días", TODAY)
    assert result == [_trade("2026-07-14")]


def test_filter_by_date_range_ultimo_mes():
    trades = [_trade("2026-06-29"), _trade("2026-06-28")]
    result = filter_by_date_range(trades, "Último mes", TODAY)
    assert result == [_trade("2026-06-29")]


def test_filter_by_date_range_empty_input_returns_empty():
    assert filter_by_date_range([], "Hoy", TODAY) == []


def test_filter_by_date_range_preserves_original_order():
    """No debe reordenar — `trades` ya viene ordenado DESC por trade_ts desde el repo."""
    trades = [_trade("2026-07-29", "B"), _trade("2026-07-29", "A"), _trade("2026-07-28", "C")]
    result = filter_by_date_range(trades, "Hoy", TODAY)
    assert [t["symbol"] for t in result] == ["B", "A"]


def test_filter_by_date_range_supports_a_different_date_field():
    """Pestaña Alertas reusa esta misma función pasando `date_field="alert_date"` en vez del
    default `"trade_date"` de Operaciones — ver 1_alertas.py."""
    alerts = [{"alert_date": "2026-07-29", "symbol": "A"}, {"alert_date": "2026-07-28", "symbol": "B"}]
    result = filter_by_date_range(alerts, "Hoy", TODAY, date_field="alert_date")
    assert result == [{"alert_date": "2026-07-29", "symbol": "A"}]


# --- _movers_from_quotes / filter_significant_movers (Market Movers "top 10 real por %",
# pedido 2026-07-29 — reemplaza el ranking por volumen de /movers de Schwab, que siempre
# devuelve las mismas 10 acciones sin importar sort/frequency, confirmado en vivo) ---


def _quote(symbol: str, change_pct: float, description: str | None = "SOME COMPANY", volume: int | None = 1000) -> Quote:
    return Quote(
        symbol=symbol, as_of=date(2026, 7, 29), last_price=100.0, bid=99.9, ask=100.1,
        net_change_pct=change_pct, description=description, total_volume=volume,
    )


def test_movers_from_quotes_converts_each_quote():
    quotes = {"AAPL": _quote("AAPL", 1.5, description="APPLE INC", volume=5_000_000)}
    movers = _movers_from_quotes(quotes)
    assert len(movers) == 1
    assert movers[0].symbol == "AAPL"
    assert movers[0].description == "APPLE INC"
    assert movers[0].change_pct == 1.5
    assert movers[0].direction == "up"
    assert movers[0].total_volume == 5_000_000


def test_movers_from_quotes_direction_down_for_negative_change():
    quotes = {"XYZ": _quote("XYZ", -0.5)}
    assert _movers_from_quotes(quotes)[0].direction == "down"


def test_movers_from_quotes_falls_back_to_symbol_without_description():
    """El batch de /quotes puede no traer reference.description para algún símbolo puntual —
    no debe dejar la fila sin nombre para mostrar."""
    quotes = {"XYZ": _quote("XYZ", 1.0, description=None)}
    assert _movers_from_quotes(quotes)[0].description == "XYZ"


def test_movers_from_quotes_zero_volume_when_missing():
    quotes = {"XYZ": _quote("XYZ", 1.0, volume=None)}
    assert _movers_from_quotes(quotes)[0].total_volume == 0


def test_filter_significant_movers_excludes_below_threshold():
    """Bug real reportado 2026-07-29: un blue-chip de alto volumen que apenas se movió (ej.
    AAPL +0.02%) no debería calificar como "mover" real solo por existir en el universo."""
    movers = [_mover("AAPL", 0.02), _mover("TSLA", -0.01), _mover("NVDA", 3.5)]
    result = filter_significant_movers(movers)
    assert [m.symbol for m in result] == ["NVDA"]


def test_filter_significant_movers_boundary_is_inclusive():
    movers = [_mover("X", MOVERS_MIN_SIGNIFICANT_PCT), _mover("Y", -MOVERS_MIN_SIGNIFICANT_PCT)]
    result = filter_significant_movers(movers)
    assert {m.symbol for m in result} == {"X", "Y"}


def test_filter_significant_movers_can_return_fewer_than_all_when_universe_is_quiet():
    """Índices chicos (Dow, 30 componentes) pueden no tener 10 movimientos significativos un
    día tranquilo — debe devolver MENOS, no rellenar con ruido para forzar un número."""
    movers = [_mover("A", 0.1), _mover("B", -0.2), _mover("C", 0.3)]
    assert filter_significant_movers(movers) == []


# --- group_roll_pairs (rolls en Pestaña Operaciones, pedido 2026-07-30 — cambio de alcance
# sobre la Fase 1 anterior, ahora SÍ se muestran) ---


def _trade_row(id_: int, order_id: int | None, leg_role: str | None, symbol: str = "SOFI") -> dict:
    return {"id": id_, "order_id": order_id, "leg_role": leg_role, "symbol": symbol, "trade_date": "2026-07-30"}


def test_group_roll_pairs_normal_openings_stay_as_singletons():
    """Comportamiento de siempre para aperturas comunes (leg_role=None) — cada una su propio
    grupo de 1 sola fila, sin cambios."""
    trades = [_trade_row(1, 100, None), _trade_row(2, 101, None)]
    groups = group_roll_pairs(trades)
    assert groups == [[trades[0]], [trades[1]]]


def test_group_roll_pairs_combines_closed_and_opened_same_order():
    trades = [_trade_row(1, 500, "roll_opened"), _trade_row(2, 500, "roll_closed")]
    groups = group_roll_pairs(trades)
    assert len(groups) == 1
    assert {t["id"] for t in groups[0]} == {1, 2}


def test_group_roll_pairs_does_not_mix_different_orders():
    trades = [_trade_row(1, 500, "roll_opened"), _trade_row(2, 500, "roll_closed"), _trade_row(3, 600, "roll_opened"), _trade_row(4, 600, "roll_closed")]
    groups = group_roll_pairs(trades)
    assert len(groups) == 2
    assert {t["id"] for t in groups[0]} == {1, 2}
    assert {t["id"] for t in groups[1]} == {3, 4}


def test_group_roll_pairs_preserves_order_of_first_appearance():
    trades = [_trade_row(1, 100, None), _trade_row(2, 500, "roll_opened"), _trade_row(3, 500, "roll_closed"), _trade_row(4, 101, None)]
    groups = group_roll_pairs(trades)
    assert [g[0]["id"] for g in groups] == [1, 2, 4]  # el roll aparece en la posición de su 1er elemento


def test_group_roll_pairs_generalizes_to_multi_leg_roll():
    """Un roll de varias patas (ej. rolar un Iron Condor completo) — todas comparten order_id,
    todas caen en el mismo grupo, sin lógica especial nueva."""
    trades = [
        _trade_row(1, 700, "roll_closed"), _trade_row(2, 700, "roll_closed"),
        _trade_row(3, 700, "roll_opened"), _trade_row(4, 700, "roll_opened"),
    ]
    groups = group_roll_pairs(trades)
    assert len(groups) == 1
    assert len(groups[0]) == 4


# --- primary_trade_for_group (pedido 2026-07-30) ---


def test_primary_trade_for_group_single_trade_is_itself():
    trade = _trade_row(1, 100, None)
    assert primary_trade_for_group([trade]) is trade


def test_primary_trade_for_group_roll_picks_the_opened_leg():
    closed = _trade_row(1, 500, "roll_closed")
    opened = _trade_row(2, 500, "roll_opened")
    assert primary_trade_for_group([closed, opened]) is opened
    assert primary_trade_for_group([opened, closed]) is opened  # sin importar el orden


# --- build_trade_table_rows (vista de tabla plana de Operaciones, pedido 2026-07-30) ---


def _table_trade(
    id_: int, occ_symbol: str, leg_role: str | None, symbol: str = "SOFI",
    entry_price: float | None = 4.38, net_premium: float | None = 876.0, strategy_type: str = "cash_secured_put",
) -> dict:
    return {
        "id": id_, "occ_symbol": occ_symbol, "leg_role": leg_role, "symbol": symbol,
        "trade_ts": "2026-07-30T10:39:20", "entry_price": entry_price, "strategy_type": strategy_type,
        "net_premium": net_premium,
    }


def _table_quote(occ_symbol: str, last_price: float) -> Quote:
    return Quote(symbol=occ_symbol, as_of=date(2026, 7, 30), last_price=last_price, bid=last_price, ask=last_price)


def test_build_trade_table_rows_normal_opening_is_one_compact_row():
    trade = _table_trade(1, "SOFI  260918P00021000", None)
    quotes = {"SOFI  260918P00021000": _table_quote("SOFI  260918P00021000", 3.10)}
    rows = build_trade_table_rows([[trade]], quotes, {"SOFI": 45.0})
    assert len(rows) == 1
    assert rows[0]["Now"] == "$3.10"
    assert rows[0]["Orig"] == "$4.38"
    assert rows[0]["IVR"] == "45"
    assert rows[0]["Action"] == "Apertura"
    assert rows[0]["Description"] == "Cash-Secured Put"


def test_build_trade_table_rows_missing_quote_is_blank_not_a_crash():
    """String vacío, no None ni NaN: en esta versión de Streamlit, st.dataframe muestra el
    texto literal "None" tanto para None como para NaN en una columna numérica (con o sin
    `format` en column_config, confirmado con una repro mínima) — bug real encontrado
    2026-07-31 verificando en vivo. La única forma de que se vea en blanco es no usar una
    columna numérica para estos valores."""
    trade = _table_trade(1, "SOFI  260918P00021000", None)
    rows = build_trade_table_rows([[trade]], {}, {})
    assert rows[0]["Now"] == ""
    assert rows[0]["IVR"] == ""


def test_build_trade_table_rows_roll_is_a_single_compact_row_using_the_opened_leg():
    """Corrección de diseño 2026-07-30: un roll (2 filas en la base, cerrada + nueva) es UNA
    sola fila compacta en la tabla — misma fila simple que una apertura común, usando los datos
    de la pata NUEVA (la posición activa). La pata cerrada no aporta nada a esta fila; solo se
    ve en el detalle expandido al hacer clic (render_roll_group)."""
    closed = _table_trade(1, "SOFI  260821P00021000", "roll_closed", entry_price=4.15, net_premium=None, strategy_type="roll_closed_leg")
    opened = _table_trade(2, "SOFI  260918P00021000", "roll_opened", entry_price=4.38, net_premium=876.0, strategy_type="cash_secured_put")
    quotes = {"SOFI  260918P00021000": _table_quote("SOFI  260918P00021000", 3.10)}
    rows = build_trade_table_rows([[closed, opened]], quotes, {"SOFI": 45.0})
    assert len(rows) == 1  # NO 2 filas
    assert rows[0]["Now"] == "$3.10"
    assert rows[0]["Orig"] == "$4.38"
    assert rows[0]["Price"] == "$876.00"
    assert rows[0]["Action"] == "Roll"
    assert rows[0]["Description"] == "Cash-Secured Put"


def test_build_trade_table_rows_action_is_roll_even_if_closed_leg_missing():
    """Caso borde: si la pata cerrada no llegó a persistirse (ej. símbolo OCC no reconocido),
    la fila igual debe decir "Roll" — se basa en el leg_role de la pata representativa, no en
    cuántas filas tiene el grupo."""
    opened = _table_trade(1, "SOFI  260918P00021000", "roll_opened")
    rows = build_trade_table_rows([[opened]], {}, {})
    assert rows[0]["Action"] == "Roll"


def test_build_trade_table_rows_multiple_groups_produce_multiple_rows():
    trade_a = _table_trade(1, "SOFI  260918P00021000", None, symbol="SOFI")
    trade_b = _table_trade(2, "AAPL  260918P00200000", None, symbol="AAPL")
    rows = build_trade_table_rows([[trade_a], [trade_b]], {}, {})
    assert [r["Symbol"] for r in rows] == ["SOFI", "AAPL"]
