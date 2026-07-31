from __future__ import annotations

from datetime import date, datetime

from options_advisor.broker.mock_client import MockBrokerClient


def test_get_all_share_positions_returns_empty_dict(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    assert client.get_all_share_positions() == {}


def test_get_all_positions_returns_empty_list(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    assert client.get_all_positions() == []


def test_get_recent_filled_orders_returns_empty_list(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    assert client.get_recent_filled_orders(datetime.now()) == []


# -- get_intraday_bars (gráfico de velas + VWAP, 2026-07-31) ------------------------------
# 2026-01-02 (viernes, día hábil NYSE) cae dentro de las 60 sesiones diarias del fixture de
# conftest.py::write_mock_fixtures (arranca 2026-01-01).
_FIXTURE_TRADING_DAY = date(2026, 1, 2)


def test_get_intraday_bars_covers_full_regular_session(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    bars = client.get_intraday_bars("TST", _FIXTURE_TRADING_DAY, interval_minutes=1)
    assert len(bars) == 390  # sesión regular completa, 9:30-16:00 ET a 1 min
    assert bars[0].timestamp < bars[-1].timestamp


def test_get_intraday_bars_open_and_close_match_daily_fixture(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    day_bar = next(b for b in client.get_price_history("TST", 60) if b.trade_date == _FIXTURE_TRADING_DAY)
    bars = client.get_intraday_bars("TST", _FIXTURE_TRADING_DAY)
    assert bars[0].open == day_bar.open
    assert bars[-1].close == day_bar.close
    assert all(day_bar.low <= b.low <= b.high <= day_bar.high for b in bars)


def test_get_intraday_bars_deterministic_across_calls(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    first = client.get_intraday_bars("TST", _FIXTURE_TRADING_DAY)
    second = client.get_intraday_bars("TST", _FIXTURE_TRADING_DAY)
    assert [b.close for b in first] == [b.close for b in second]


def test_get_intraday_bars_respects_interval_minutes(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    bars = client.get_intraday_bars("TST", _FIXTURE_TRADING_DAY, interval_minutes=30)
    assert len(bars) == 13  # 390 min / 30


def test_get_intraday_bars_empty_on_non_trading_day(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    assert client.get_intraday_bars("TST", date(2026, 1, 3)) == []  # sábado


def test_get_intraday_bars_empty_without_daily_fixture_for_date(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    assert client.get_intraday_bars("TST", date(2026, 6, 1)) == []  # fuera de las 60 sesiones del fixture


def test_get_quote_returns_latest_price(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    quote = client.get_quote("TST")
    assert quote.symbol == "TST"
    assert quote.last_price > 0
    assert quote.bid < quote.last_price < quote.ask


def test_get_quote_computes_net_change_from_previous_session(mock_fixtures_dir):
    """Fixture determinista: precio sube +0.1/día (ver conftest.write_mock_fixtures) — el
    último día siempre tiene un net_change positivo conocido vs. el día anterior."""
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    quote = client.get_quote("TST")
    history = client.get_price_history("TST", lookback_days=60)
    previous_close = history[-2].close
    expected_change = round(quote.last_price - previous_close, 4)
    assert quote.net_change == expected_change
    assert quote.net_change_pct == round(expected_change / previous_close * 100, 4)
    assert quote.net_change > 0


def test_get_quote_no_post_market_data_in_mock_mode(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    quote = client.get_quote("TST")
    assert quote.post_market_change_pct is None


def test_get_quotes_skips_symbols_without_fixtures(mock_fixtures_dir):
    """Market Movers (pedido 2026-07-29) cotiza en batch universos grandes (S&P 500 completo)
    que en modo mock casi seguro no tienen fixture propia — un símbolo sin datos no debe tirar
    abajo todo el batch, solo se omite."""
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    quotes = client.get_quotes(["TST", "SYMBOL_WITHOUT_ANY_FIXTURE"])
    assert set(quotes.keys()) == {"TST"}


def test_get_movers_up_returns_only_positive_direction_sorted_desc(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    movers = client.get_movers("$SPX", "PERCENT_CHANGE_UP")
    assert all(m.direction == "up" for m in movers)
    assert all(m.change_pct > 0 for m in movers)
    assert movers == sorted(movers, key=lambda m: m.change_pct, reverse=True)


def test_get_movers_down_returns_only_negative_direction_sorted_asc(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    movers = client.get_movers("$SPX", "PERCENT_CHANGE_DOWN")
    assert all(m.direction == "down" for m in movers)
    assert all(m.change_pct < 0 for m in movers)
    assert movers == sorted(movers, key=lambda m: m.change_pct)


def test_get_movers_volume_sorts_desc_by_total_volume(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    movers = client.get_movers("$SPX", "VOLUME")
    assert movers == sorted(movers, key=lambda m: m.total_volume, reverse=True)


def test_get_price_history_respects_lookback(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    history = client.get_price_history("TST", lookback_days=10)
    assert len(history) == 10
    assert history == sorted(history, key=lambda b: b.trade_date)


def test_get_option_chain_produces_contracts_with_greeks(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    chain = client.get_option_chain("TST", expiration_range_days=(7, 60))
    assert len(chain.contracts) > 0
    for contract in chain.contracts:
        assert contract.greeks.source == "calculated"
        assert contract.bid < contract.ask
        assert contract.implied_volatility > 0


def test_set_as_of_date_changes_resolved_quote(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    early_date = date(2026, 1, 10)
    client.set_as_of_date(early_date)
    quote = client.get_quote("TST")
    assert quote.as_of == early_date


def test_atm_contract_is_closest_to_underlying(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    chain = client.get_option_chain("TST", expiration_range_days=(7, 60))
    atm_put = chain.atm_contract("put")
    distances = [abs(c.strike - chain.underlying_price) for c in chain.contracts if c.option_type == "put"]
    assert abs(atm_put.strike - chain.underlying_price) == min(distances)
