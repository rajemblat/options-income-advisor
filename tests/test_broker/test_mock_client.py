from __future__ import annotations

from datetime import date

from options_advisor.broker.mock_client import MockBrokerClient


def test_get_all_share_positions_returns_empty_dict(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    assert client.get_all_share_positions() == {}


def test_get_all_positions_returns_empty_list(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    assert client.get_all_positions() == []


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
