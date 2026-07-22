from __future__ import annotations

from datetime import date

from options_advisor.broker.mock_client import MockBrokerClient


def test_get_quote_returns_latest_price(mock_fixtures_dir):
    client = MockBrokerClient(fixtures_dir=mock_fixtures_dir)
    quote = client.get_quote("TST")
    assert quote.symbol == "TST"
    assert quote.last_price > 0
    assert quote.bid < quote.last_price < quote.ask


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
