from __future__ import annotations

from datetime import date

import httpx
import pytest

from options_advisor.broker.schwab_client import SchwabBrokerClient

TODAY = date.today()


class _FakeAuth:
    def get_valid_access_token(self) -> str:
        return "fake-token"


@pytest.fixture
def client():
    return SchwabBrokerClient(auth=_FakeAuth(), risk_free_rate=0.045)


def _mock_get(json_payload):
    def _get(self, path, params=None, headers=None):
        request = httpx.Request("GET", f"https://api.schwabapi.com/marketdata/v1{path}")
        return httpx.Response(200, json=json_payload, request=request)

    return _get


def _raw_contract(**overrides):
    base = {
        "bid": 11.65,
        "ask": 12.15,
        "last": 12.0,
        "volatility": 28.36,
        "delta": 0.537,
        "gamma": 0.016,
        "theta": -0.172,
        "vega": 0.359,
        "rho": 0.122,
        "openInterest": 25201,
        "totalVolume": 5176,
        "strikePrice": 320.0,
        "expirationDate": "2026-08-21T20:00:00.000+00:00",
    }
    base.update(overrides)
    return base


def test_get_quote_parses_real_fields(client, monkeypatch):
    payload = {"AAPL": {"quote": {"lastPrice": 321.1, "bidPrice": 321.1, "askPrice": 321.48}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quote = client.get_quote("AAPL")
    assert quote.last_price == 321.1
    assert quote.bid == 321.1
    assert quote.ask == 321.48


def test_get_quote_index_without_bid_ask_falls_back_to_last_price(client, monkeypatch):
    """Índices ($SPX, $RUT, $NDX, $VIX) no tienen bid/ask — confirmado en vivo. Sin fallback,
    esto rompería con KeyError antes de esta corrección."""
    payload = {"$SPX": {"quote": {"lastPrice": 7449.47, "closePrice": 7408.3}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quote = client.get_quote("$SPX")
    assert quote.last_price == 7449.47
    assert quote.bid == 7449.47
    assert quote.ask == 7449.47


def test_get_quotes_batch_index_without_bid_ask_falls_back_to_last_price(client, monkeypatch):
    payload = {"$VIX": {"quote": {"lastPrice": 17.6}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quotes = client.get_quotes(["$VIX"])
    assert quotes["$VIX"].bid == 17.6
    assert quotes["$VIX"].ask == 17.6


def test_get_price_history_sorts_and_truncates_to_lookback(client, monkeypatch):
    candles = [
        {"datetime": 1700000000000 + i * 86_400_000, "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i, "volume": 1000}
        for i in range(10)
    ]
    monkeypatch.setattr(httpx.Client, "get", _mock_get({"candles": list(reversed(candles))}))
    bars = client.get_price_history("AAPL", lookback_days=3)
    assert len(bars) == 3
    assert bars[0].trade_date < bars[1].trade_date < bars[2].trade_date


def test_option_chain_uses_broker_greeks_when_present(client, monkeypatch):
    payload = {
        "underlyingPrice": 321.1,
        "interestRate": 4.5,
        "dividendYield": 0.5,
        "callExpDateMap": {"2026-08-21:29": {"320.0": [_raw_contract()]}},
        "putExpDateMap": {},
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    chain = client.get_option_chain("AAPL")
    assert len(chain.contracts) == 1
    contract = chain.contracts[0]
    assert contract.greeks.source == "broker"
    assert contract.greeks.delta == 0.537
    assert contract.open_interest == 25201
    assert contract.volume == 5176
    assert contract.implied_volatility == pytest.approx(0.2836)


def test_option_chain_falls_back_to_calculated_greeks_using_live_rate_and_dividend(client, monkeypatch):
    raw = _raw_contract(delta=None, gamma=None, theta=None, vega=None, rho=None)
    payload = {
        "underlyingPrice": 321.1,
        "interestRate": 4.5,
        "dividendYield": 0.5,
        "callExpDateMap": {"2026-08-21:29": {"320.0": [raw]}},
        "putExpDateMap": {},
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    chain = client.get_option_chain("AAPL")
    contract = chain.contracts[0]
    assert contract.greeks.source == "calculated"
    # Sanity: un fallback calculado para un contrato ~ATM da delta razonable (no 0, no 1)
    assert 0.3 < contract.greeks.delta < 0.7


def test_option_chain_falls_back_to_configured_rate_when_schwab_rate_missing(client, monkeypatch):
    """Sin interestRate de Schwab (0 o ausente), usa self.risk_free_rate — mismo comportamiento
    que antes de threadear la tasa en vivo."""
    raw = _raw_contract(delta=None, gamma=None, theta=None, vega=None, rho=None)
    payload_missing_rate = {
        "underlyingPrice": 321.1,
        "callExpDateMap": {"2026-08-21:29": {"320.0": [raw]}},
        "putExpDateMap": {},
    }
    payload_zero_rate = {**payload_missing_rate, "interestRate": 0, "dividendYield": 0}

    for payload in (payload_missing_rate, payload_zero_rate):
        monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
        chain = client.get_option_chain("AAPL")
        assert chain.contracts[0].greeks.source == "calculated"  # no lanza pese a faltar interestRate/dividendYield


def _position(symbol: str, asset_type: str, long_qty: float) -> dict:
    return {"instrument": {"assetType": asset_type, "symbol": symbol}, "longQuantity": long_qty}


def _mock_trader_get(accounts: list[dict], positions_by_hash: dict[str, list[dict]]):
    def _get(self, path, params=None, headers=None):
        request = httpx.Request("GET", f"https://api.schwabapi.com/trader/v1{path}")
        if path == "/accounts/accountNumbers":
            return httpx.Response(200, json=accounts, request=request)
        account_hash = path.removeprefix("/accounts/")
        payload = {"securitiesAccount": {"positions": positions_by_hash.get(account_hash, [])}}
        return httpx.Response(200, json=payload, request=request)

    return _get


def test_get_all_share_positions_sums_equity_across_accounts(client, monkeypatch):
    accounts = [{"accountNumber": "111", "hashValue": "HASH1"}, {"accountNumber": "222", "hashValue": "HASH2"}]
    positions_by_hash = {
        "HASH1": [_position("NVDA", "EQUITY", 300), _position("AAPL", "OPTION", 1)],
        "HASH2": [_position("NVDA", "EQUITY", 50), _position("SOFI", "EQUITY", 1000)],
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_trader_get(accounts, positions_by_hash))

    positions = client.get_all_share_positions()

    assert positions == {"NVDA": 350, "SOFI": 1000}  # suma entre las 2 cuentas, opciones excluidas


def test_get_all_share_positions_empty_when_accounts_call_fails(client, monkeypatch):
    def _boom(self, path, params=None, headers=None):
        raise httpx.ConnectError("no network", request=httpx.Request("GET", "https://api.schwabapi.com/trader/v1/x"))

    monkeypatch.setattr(httpx.Client, "get", _boom)
    assert client.get_all_share_positions() == {}


def test_get_all_share_positions_one_account_failing_does_not_block_others(client, monkeypatch):
    accounts = [{"accountNumber": "111", "hashValue": "HASH1"}, {"accountNumber": "222", "hashValue": "HASH2"}]

    def _get(self, path, params=None, headers=None):
        request = httpx.Request("GET", f"https://api.schwabapi.com/trader/v1{path}")
        if path == "/accounts/accountNumbers":
            return httpx.Response(200, json=accounts, request=request)
        if path == "/accounts/HASH1":
            raise httpx.ConnectError("no network", request=request)
        return httpx.Response(200, json={"securitiesAccount": {"positions": [_position("NVDA", "EQUITY", 300)]}}, request=request)

    monkeypatch.setattr(httpx.Client, "get", _get)
    assert client.get_all_share_positions() == {"NVDA": 300}


def _full_position(
    symbol: str, asset_type: str, long_qty: float = 0, short_qty: float = 0, average_price: float = 0.0,
    market_value: float = 0.0, pnl: float = 0.0, description: str | None = None,
) -> dict:
    return {
        "instrument": {"assetType": asset_type, "symbol": symbol, "description": description},
        "longQuantity": long_qty,
        "shortQuantity": short_qty,
        "averagePrice": average_price,
        "marketValue": market_value,
        "longOpenProfitLoss": pnl,
    }


def test_get_all_positions_returns_long_and_short_across_accounts(client, monkeypatch):
    accounts = [{"accountNumber": "111", "hashValue": "HASH1"}, {"accountNumber": "222", "hashValue": "HASH2"}]
    positions_by_hash = {
        "HASH1": [_full_position("NVDA", "EQUITY", long_qty=300, average_price=209.41, market_value=62370.0, pnl=-453.49, description="NVIDIA CORP")],
        "HASH2": [_full_position("SLV", "OPTION", short_qty=2, average_price=6.71, market_value=-1670.0, pnl=-300.82, description="ISHR SILVER TR PUT")],
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_trader_get(accounts, positions_by_hash))

    positions = client.get_all_positions()

    assert len(positions) == 2
    nvda = next(p for p in positions if p.symbol == "NVDA")
    assert nvda.account_number == "111"
    assert nvda.asset_type == "EQUITY"
    assert nvda.quantity == 300
    assert nvda.average_price == 209.41
    assert nvda.market_value == 62370.0
    assert nvda.unrealized_pnl == -453.49

    slv = next(p for p in positions if p.symbol == "SLV")
    assert slv.quantity == -2  # posición corta: shortQuantity resta
    assert slv.asset_type == "OPTION"


def test_get_all_positions_empty_when_accounts_call_fails(client, monkeypatch):
    def _boom(self, path, params=None, headers=None):
        raise httpx.ConnectError("no network", request=httpx.Request("GET", "https://api.schwabapi.com/trader/v1/x"))

    monkeypatch.setattr(httpx.Client, "get", _boom)
    assert client.get_all_positions() == []


def test_get_all_positions_parses_occ_option_symbol(client, monkeypatch):
    """Símbolo OCC real: 'SLV   260821P00060000' -> SLV, 2026-08-21, put, strike 60.0."""
    accounts = [{"accountNumber": "111", "hashValue": "HASH1"}]
    positions_by_hash = {"HASH1": [_full_position("SLV   260821P00060000", "OPTION", short_qty=2, average_price=6.71)]}
    monkeypatch.setattr(httpx.Client, "get", _mock_trader_get(accounts, positions_by_hash))

    position = client.get_all_positions()[0]
    assert position.underlying_symbol == "SLV"
    assert position.expiration == date(2026, 8, 21)
    assert position.option_type == "put"
    assert position.strike == 60.0


def test_get_all_positions_call_option_symbol_parses_correctly(client, monkeypatch):
    accounts = [{"accountNumber": "111", "hashValue": "HASH1"}]
    positions_by_hash = {"HASH1": [_full_position("AAPL  260117C00320000", "OPTION", long_qty=1, average_price=12.0)]}
    monkeypatch.setattr(httpx.Client, "get", _mock_trader_get(accounts, positions_by_hash))

    position = client.get_all_positions()[0]
    assert position.underlying_symbol == "AAPL"
    assert position.expiration == date(2026, 1, 17)
    assert position.option_type == "call"
    assert position.strike == 320.0


def test_get_all_positions_equity_has_no_option_fields(client, monkeypatch):
    accounts = [{"accountNumber": "111", "hashValue": "HASH1"}]
    positions_by_hash = {"HASH1": [_full_position("NVDA", "EQUITY", long_qty=300, average_price=209.41)]}
    monkeypatch.setattr(httpx.Client, "get", _mock_trader_get(accounts, positions_by_hash))

    position = client.get_all_positions()[0]
    assert position.option_type is None
    assert position.strike is None
    assert position.expiration is None


def test_get_quotes_batch_returns_all_symbols(client, monkeypatch):
    payload = {
        "AAPL": {"quote": {"lastPrice": 321.1, "bidPrice": 321.0, "askPrice": 321.2}},
        "NVDA": {"quote": {"lastPrice": 180.5, "bidPrice": 180.4, "askPrice": 180.6}},
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quotes = client.get_quotes(["AAPL", "NVDA"])
    assert set(quotes.keys()) == {"AAPL", "NVDA"}
    assert quotes["AAPL"].last_price == 321.1


def test_get_quotes_empty_list_returns_empty_dict_without_calling_api(client, monkeypatch):
    calls = []
    monkeypatch.setattr(httpx.Client, "get", lambda *a, **k: calls.append(1))
    assert client.get_quotes([]) == {}
    assert calls == []


def test_get_quotes_returns_empty_dict_on_failure(client, monkeypatch):
    def _boom(self, path, params=None, headers=None):
        raise httpx.ConnectError("no network", request=httpx.Request("GET", "https://api.schwabapi.com/marketdata/v1/x"))

    monkeypatch.setattr(httpx.Client, "get", _boom)
    assert client.get_quotes(["AAPL"]) == {}


def _screen_entry(optionable: bool, price: float, avg_volume: float, high_52w: float | None = None, low_52w: float | None = None) -> dict:
    return {
        "quote": {"lastPrice": price, "52WeekHigh": high_52w, "52WeekLow": low_52w},
        "reference": {"optionable": optionable},
        "fundamental": {"avg10DaysVolume": avg_volume},
    }


def test_screen_universe_filters_by_optionable_price_and_volume(client, monkeypatch):
    payload = {
        "AAPL": _screen_entry(optionable=True, price=200.0, avg_volume=1_000_000, high_52w=250, low_52w=150),
        "PENNY": _screen_entry(optionable=True, price=2.0, avg_volume=1_000_000, high_52w=3, low_52w=1),  # precio muy bajo
        "BRKA": _screen_entry(optionable=True, price=900_000.0, avg_volume=1_000_000, high_52w=950_000, low_52w=800_000),  # muy caro
        "ILLIQUID": _screen_entry(optionable=True, price=200.0, avg_volume=1_000, high_52w=250, low_52w=150),  # sin volumen
        "NOTOPT": _screen_entry(optionable=False, price=200.0, avg_volume=1_000_000, high_52w=250, low_52w=150),  # sin opciones
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    shortlist = client.screen_universe(list(payload.keys()))
    assert shortlist == ["AAPL"]


def test_screen_universe_ranks_by_52_week_range_and_caps_shortlist(client, monkeypatch):
    payload = {
        "LOW_VOL": _screen_entry(optionable=True, price=100.0, avg_volume=1_000_000, high_52w=110, low_52w=90),  # rango 20%
        "HIGH_VOL": _screen_entry(optionable=True, price=100.0, avg_volume=1_000_000, high_52w=180, low_52w=60),  # rango 120%
        "MID_VOL": _screen_entry(optionable=True, price=100.0, avg_volume=1_000_000, high_52w=140, low_52w=80),  # rango 60%
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    shortlist = client.screen_universe(list(payload.keys()), max_shortlist=2)
    assert shortlist == ["HIGH_VOL", "MID_VOL"]  # rankeado desc, tope de 2 excluye LOW_VOL


def test_screen_universe_chunks_large_batches(client, monkeypatch):
    symbols = [f"SYM{i}" for i in range(250)]  # más de 1 batch (tamaño 200)
    calls = []

    def _get(self, path, params=None, headers=None):
        calls.append(len(params["symbols"].split(",")))
        batch_symbols = params["symbols"].split(",")
        payload = {s: _screen_entry(optionable=True, price=100.0, avg_volume=1_000_000, high_52w=110, low_52w=90) for s in batch_symbols}
        request = httpx.Request("GET", f"https://api.schwabapi.com/marketdata/v1{path}")
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx.Client, "get", _get)
    shortlist = client.screen_universe(symbols, max_shortlist=1000)
    assert len(calls) == 2  # 200 + 50
    assert len(shortlist) == 250


def test_screen_universe_one_batch_failing_does_not_block_others(client, monkeypatch):
    symbols = [f"SYM{i}" for i in range(250)]

    def _get(self, path, params=None, headers=None):
        batch_symbols = params["symbols"].split(",")
        if batch_symbols[0] == "SYM0":
            raise httpx.ConnectError("no network", request=httpx.Request("GET", "https://api.schwabapi.com/marketdata/v1/x"))
        payload = {s: _screen_entry(optionable=True, price=100.0, avg_volume=1_000_000, high_52w=110, low_52w=90) for s in batch_symbols}
        request = httpx.Request("GET", f"https://api.schwabapi.com/marketdata/v1{path}")
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx.Client, "get", _get)
    shortlist = client.screen_universe(symbols, max_shortlist=1000)
    assert len(shortlist) == 50  # el primer lote (200) falló, el segundo (50) sobrevive
