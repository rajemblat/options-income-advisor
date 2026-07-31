from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from options_advisor.broker.schwab_client import SchwabBrokerClient, _parse_filled_order

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


def test_get_quote_parses_description_and_total_volume(client, monkeypatch):
    """Sumado 2026-07-29 para Market Movers (top 10 real por %, ver dashboard/components.py::
    cached_movers) — ya venían en la respuesta de /quotes sin costo extra."""
    payload = {
        "AAPL": {
            "quote": {"lastPrice": 321.1, "bidPrice": 321.1, "askPrice": 321.48, "totalVolume": 56_090_840},
            "reference": {"description": "APPLE INC"},
        }
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quote = client.get_quote("AAPL")
    assert quote.description == "APPLE INC"
    assert quote.total_volume == 56_090_840


def test_get_quote_description_and_total_volume_none_when_missing(client, monkeypatch):
    payload = {"AAPL": {"quote": {"lastPrice": 321.1, "bidPrice": 321.1, "askPrice": 321.48}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quote = client.get_quote("AAPL")
    assert quote.description is None
    assert quote.total_volume is None


def test_get_quote_parses_next_ex_dividend_date_when_div_ex_date_is_future(client, monkeypatch):
    future = (TODAY + timedelta(days=30)).isoformat()
    further = (TODAY + timedelta(days=120)).isoformat()
    payload = {
        "JNJ": {
            "quote": {"lastPrice": 263.25, "bidPrice": 263.0, "askPrice": 263.34},
            "fundamental": {"divExDate": f"{future}T00:00:00Z", "nextDivExDate": f"{further}T00:00:00Z"},
        }
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quote = client.get_quote("JNJ")
    assert quote.next_ex_dividend_date == TODAY + timedelta(days=30)


def test_get_quote_falls_back_to_next_div_ex_date_when_div_ex_date_is_past(client, monkeypatch):
    """Encontrado con datos reales: QQQ tenía `divExDate` en el pasado (ciclo ya pagado) y
    `nextDivExDate` con la fecha realmente próxima — `divExDate` no es siempre la futura."""
    past = (TODAY - timedelta(days=30)).isoformat()
    future = (TODAY + timedelta(days=60)).isoformat()
    payload = {
        "QQQ": {
            "quote": {"lastPrice": 684.63, "bidPrice": 684.5, "askPrice": 684.8},
            "fundamental": {"divExDate": f"{past}T00:00:00Z", "nextDivExDate": f"{future}T00:00:00Z"},
        }
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quote = client.get_quote("QQQ")
    assert quote.next_ex_dividend_date == TODAY + timedelta(days=60)


def test_get_quote_none_when_both_dividend_dates_are_past(client, monkeypatch):
    past1 = (TODAY - timedelta(days=90)).isoformat()
    past2 = (TODAY - timedelta(days=30)).isoformat()
    payload = {
        "OLD": {
            "quote": {"lastPrice": 10.0, "bidPrice": 9.9, "askPrice": 10.1},
            "fundamental": {"divExDate": f"{past1}T00:00:00Z", "nextDivExDate": f"{past2}T00:00:00Z"},
        }
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quote = client.get_quote("OLD")
    assert quote.next_ex_dividend_date is None


def test_get_quote_no_dividend_data_returns_none(client, monkeypatch):
    payload = {"NVDA": {"quote": {"lastPrice": 180.0, "bidPrice": 179.9, "askPrice": 180.1}, "fundamental": {}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quote = client.get_quote("NVDA")
    assert quote.next_ex_dividend_date is None


def test_get_quote_parses_net_change_fields(client, monkeypatch):
    payload = {
        "AAPL": {
            "quote": {
                "lastPrice": 321.1,
                "bidPrice": 321.0,
                "askPrice": 321.2,
                "netChange": 2.35,
                "netPercentChange": 0.7371,
                "postMarketPercentChange": -0.12,
            }
        }
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quote = client.get_quote("AAPL")
    assert quote.net_change == 2.35
    assert quote.net_change_pct == 0.7371
    assert quote.post_market_change_pct == -0.12


def test_get_quote_no_post_market_change_when_key_absent(client, monkeypatch):
    """`postMarketPercentChange` no viene en la respuesta fuera de sesión extendida — debe
    quedar en None, no en 0.0 (0.0 significaría "sin variación", distinto de "no aplica")."""
    payload = {"AAPL": {"quote": {"lastPrice": 321.1, "bidPrice": 321.0, "askPrice": 321.2}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quote = client.get_quote("AAPL")
    assert quote.net_change == 0.0
    assert quote.net_change_pct == 0.0
    assert quote.post_market_change_pct is None


# --- instrument_type (Sección 'Pestaña Screener', pedido 2026-07-27) ---


def test_get_quote_classifies_etf(client, monkeypatch):
    """Shape real confirmado en vivo 2026-07-27: SPY -> assetMainType=EQUITY, assetSubType=ETF."""
    payload = {"SPY": {"assetMainType": "EQUITY", "assetSubType": "ETF", "quote": {"lastPrice": 680.0, "bidPrice": 679.9, "askPrice": 680.1}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    assert client.get_quote("SPY").instrument_type == "etf"


def test_get_quote_classifies_common_stock(client, monkeypatch):
    """AAPL -> assetMainType=EQUITY, assetSubType=COE (Common Equity) — cualquier EQUITY que
    no sea ETF se clasifica "stock", sin depender de conocer todos los códigos de Schwab."""
    payload = {"AAPL": {"assetMainType": "EQUITY", "assetSubType": "COE", "quote": {"lastPrice": 335.0, "bidPrice": 334.9, "askPrice": 335.1}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    assert client.get_quote("AAPL").instrument_type == "stock"


def test_get_quote_classifies_index(client, monkeypatch):
    """$RUT -> assetMainType=INDEX, sin assetSubType."""
    payload = {"$RUT": {"assetMainType": "INDEX", "quote": {"lastPrice": 2930.0, "bidPrice": 2930.0, "askPrice": 2930.0}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    assert client.get_quote("$RUT").instrument_type == "index"


def test_get_quote_instrument_type_none_when_asset_main_type_absent(client, monkeypatch):
    payload = {"AAPL": {"quote": {"lastPrice": 335.0, "bidPrice": 334.9, "askPrice": 335.1}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    assert client.get_quote("AAPL").instrument_type is None


def test_get_quotes_batch_classifies_instrument_type(client, monkeypatch):
    payload = {
        "SPY": {"assetMainType": "EQUITY", "assetSubType": "ETF", "quote": {"lastPrice": 680.0, "bidPrice": 679.9, "askPrice": 680.1}},
        "AAPL": {"assetMainType": "EQUITY", "assetSubType": "COE", "quote": {"lastPrice": 335.0, "bidPrice": 334.9, "askPrice": 335.1}},
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quotes = client.get_quotes(["SPY", "AAPL"])
    assert quotes["SPY"].instrument_type == "etf"
    assert quotes["AAPL"].instrument_type == "stock"


def test_get_quotes_batch_parses_net_change_fields(client, monkeypatch):
    payload = {"NVDA": {"quote": {"lastPrice": 180.5, "bidPrice": 180.4, "askPrice": 180.6, "netChange": -1.2, "netPercentChange": -0.66}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quotes = client.get_quotes(["NVDA"])
    assert quotes["NVDA"].net_change == -1.2
    assert quotes["NVDA"].net_change_pct == -0.66


def test_get_movers_parses_real_schwab_field_names(client, monkeypatch):
    """Shape real capturado en vivo 2026-07-27 (ver schwab_client.py::_parse_mover) — Schwab
    usa lastPrice/netChange/netPercentChange, no last/change/direction (bug real encontrado
    ese día: con esos nombres viejos, todo caía en 0.0/0.0/"up")."""
    payload = {
        "screeners": [
            {
                "symbol": "NVDA", "description": "NVIDIA CORP", "volume": 96974192, "lastPrice": 196.24,
                "netChange": -10.6, "marketShare": 6.05, "totalVolume": 1603360831, "trades": 1511279,
                "netPercentChange": -0.0512,
            },
            {
                "symbol": "ORCL", "description": "ORACLE CORP", "volume": 24055079, "lastPrice": 120.21,
                "netChange": 5.22, "marketShare": 1.5, "totalVolume": 1603360831, "trades": 252715,
                "netPercentChange": 0.0454,
            },
        ]
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    movers = client.get_movers("$SPX", "PERCENT_CHANGE_UP")
    assert len(movers) == 2
    nvda = next(m for m in movers if m.symbol == "NVDA")
    assert nvda.last_price == 196.24
    assert nvda.change_pct == pytest.approx(-5.12)
    assert nvda.direction == "down"
    assert nvda.total_volume == 1603360831
    orcl = next(m for m in movers if m.symbol == "ORCL")
    assert orcl.change_pct == pytest.approx(4.54)
    assert orcl.direction == "up"


def test_get_movers_returns_empty_list_on_failure(client, monkeypatch):
    def _boom(self, path, params=None, headers=None):
        raise httpx.ConnectError("no network", request=httpx.Request("GET", "https://api.schwabapi.com/marketdata/v1/x"))

    monkeypatch.setattr(httpx.Client, "get", _boom)
    assert client.get_movers("$SPX", "PERCENT_CHANGE_UP") == []


def test_get_movers_empty_screeners_returns_empty_list(client, monkeypatch):
    monkeypatch.setattr(httpx.Client, "get", _mock_get({"screeners": []}))
    assert client.get_movers("$SPX", "PERCENT_CHANGE_UP") == []


def test_get_quote_missing_fundamental_section_returns_none(client, monkeypatch):
    payload = {"$SPX": {"quote": {"lastPrice": 7449.47}}}
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quote = client.get_quote("$SPX")
    assert quote.next_ex_dividend_date is None


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


def test_get_quotes_batch_parses_description_and_total_volume(client, monkeypatch):
    payload = {
        "AAPL": {
            "quote": {"lastPrice": 321.1, "bidPrice": 321.0, "askPrice": 321.2, "totalVolume": 56_090_840},
            "reference": {"description": "APPLE INC"},
        },
        "NVDA": {"quote": {"lastPrice": 180.5, "bidPrice": 180.4, "askPrice": 180.6}},
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quotes = client.get_quotes(["AAPL", "NVDA"])
    assert quotes["AAPL"].description == "APPLE INC"
    assert quotes["AAPL"].total_volume == 56_090_840
    assert quotes["NVDA"].description is None


def test_get_quotes_batch_parses_next_ex_dividend_date(client, monkeypatch):
    future = (TODAY + timedelta(days=30)).isoformat()
    payload = {
        "JNJ": {
            "quote": {"lastPrice": 263.25, "bidPrice": 263.0, "askPrice": 263.34},
            "fundamental": {"divExDate": f"{future}T00:00:00Z"},
        },
        "NVDA": {"quote": {"lastPrice": 180.5, "bidPrice": 180.4, "askPrice": 180.6}},
    }
    monkeypatch.setattr(httpx.Client, "get", _mock_get(payload))
    quotes = client.get_quotes(["JNJ", "NVDA"])
    assert quotes["JNJ"].next_ex_dividend_date == TODAY + timedelta(days=30)
    assert quotes["NVDA"].next_ex_dividend_date is None


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


# --- _parse_filled_order / get_recent_filled_orders (rediseño de Operaciones vía /orders,
# 2026-07-28) — fixtures con la forma REAL capturada probando el endpoint en vivo contra la
# cuenta real del usuario. ---


def _hood_raw_order() -> dict:
    """Orden real de apertura de HOOD (2 puts $75 vendidos), capturada en vivo el 2026-07-28."""
    return {
        "orderId": 1007358084142,
        "accountNumber": 74257810,
        "status": "FILLED",
        "enteredTime": "2026-07-28T14:07:26+0000",
        "closeTime": "2026-07-28T14:07:26+0000",
        "orderLegCollection": [
            {
                "orderLegType": "OPTION",
                "legId": 1,
                "instrument": {"assetType": "OPTION", "symbol": "HOOD  260904P00075000"},
                "instruction": "SELL_TO_OPEN",
                "positionEffect": "OPENING",
                "quantity": 2.0,
            }
        ],
        "orderActivityCollection": [
            {
                "activityType": "EXECUTION",
                "executionLegs": [{"legId": 1, "quantity": 2.0, "price": 3.15, "time": "2026-07-28T14:07:26+0000"}],
            }
        ],
    }


def _sofi_roll_raw_order() -> dict:
    """Roll real de SOFI (Aug21 -> Sep18 $21P) capturado en vivo — una sola orden combinada
    (`complexOrderStrategyType: CALENDAR`) con una pata SELL_TO_OPEN y una BUY_TO_CLOSE."""
    return {
        "orderId": 1007347459242,
        "accountNumber": 74257810,
        "status": "FILLED",
        "complexOrderStrategyType": "CALENDAR",
        "enteredTime": "2026-07-27T17:03:59+0000",
        "closeTime": "2026-07-27T17:04:00+0000",
        "orderLegCollection": [
            {
                "orderLegType": "OPTION",
                "legId": 1,
                "instrument": {"assetType": "OPTION", "symbol": "SOFI  260918P00021000"},
                "instruction": "SELL_TO_OPEN",
                "positionEffect": "OPENING",
                "quantity": 2.0,
            },
            {
                "orderLegType": "OPTION",
                "legId": 2,
                "instrument": {"assetType": "OPTION", "symbol": "SOFI  260821P00021000"},
                "instruction": "BUY_TO_CLOSE",
                "positionEffect": "CLOSING",
                "quantity": 2.0,
            },
        ],
        "orderActivityCollection": [
            {
                "activityType": "EXECUTION",
                "executionLegs": [
                    {"legId": 1, "quantity": 2.0, "price": 4.38, "time": "2026-07-27T17:04:00+0000"},
                    {"legId": 2, "quantity": 2.0, "price": 4.15, "time": "2026-07-27T17:04:00+0000"},
                ],
            }
        ],
    }


def test_parse_filled_order_single_opening_leg():
    order = _parse_filled_order(_hood_raw_order())
    assert order is not None
    assert order.order_id == 1007358084142
    assert order.account_number == "74257810"
    assert len(order.legs) == 1
    leg = order.legs[0]
    assert leg.occ_symbol == "HOOD  260904P00075000"
    assert leg.instruction == "SELL_TO_OPEN"
    assert leg.position_effect == "OPENING"
    assert leg.quantity == 2.0
    assert leg.price == 3.15  # fill EXACTO de esta orden, no un promedio


def test_parse_filled_order_roll_has_opening_and_closing_leg_with_own_price():
    order = _parse_filled_order(_sofi_roll_raw_order())
    assert order is not None
    assert len(order.legs) == 2
    by_effect = {leg.position_effect: leg for leg in order.legs}
    assert by_effect["OPENING"].occ_symbol == "SOFI  260918P00021000"
    assert by_effect["OPENING"].price == 4.38
    assert by_effect["CLOSING"].occ_symbol == "SOFI  260821P00021000"
    assert by_effect["CLOSING"].price == 4.15


def test_parse_filled_order_aggregates_multiple_partial_executions():
    """Una orden llenada en 2 tandas (fills parciales) debe promediar el precio ponderado por
    cantidad, no quedarse solo con la primera ejecución."""
    raw = _hood_raw_order()
    raw["orderActivityCollection"] = [
        {"activityType": "EXECUTION", "executionLegs": [{"legId": 1, "quantity": 1.0, "price": 3.10, "time": "2026-07-28T14:07:20+0000"}]},
        {"activityType": "EXECUTION", "executionLegs": [{"legId": 1, "quantity": 1.0, "price": 3.20, "time": "2026-07-28T14:07:26+0000"}]},
    ]
    order = _parse_filled_order(raw)
    assert order is not None
    assert order.legs[0].quantity == 2.0
    assert order.legs[0].price == pytest.approx(3.15, abs=0.001)  # (1x3.10 + 1x3.20) / 2


def test_parse_filled_order_none_without_order_id():
    raw = _hood_raw_order()
    del raw["orderId"]
    assert _parse_filled_order(raw) is None


def test_parse_filled_order_none_when_no_option_legs():
    raw = _hood_raw_order()
    raw["orderLegCollection"] = [{"orderLegType": "EQUITY", "legId": 1, "instrument": {"assetType": "EQUITY", "symbol": "AAPL"}}]
    assert _parse_filled_order(raw) is None


def test_parse_filled_order_none_when_no_matching_execution():
    raw = _hood_raw_order()
    raw["orderActivityCollection"] = []
    assert _parse_filled_order(raw) is None


def _mock_orders_get(accounts: list[dict], orders_by_hash: dict[str, list[dict]]):
    def _get(self, path, params=None, headers=None):
        request = httpx.Request("GET", f"https://api.schwabapi.com/trader/v1{path}")
        if path == "/accounts/accountNumbers":
            return httpx.Response(200, json=accounts, request=request)
        account_hash = path.split("/accounts/", 1)[1].removesuffix("/orders")
        assert params.get("status") == "FILLED"
        return httpx.Response(200, json=orders_by_hash.get(account_hash, []), request=request)

    return _get


def test_get_recent_filled_orders_returns_parsed_orders_across_accounts(client, monkeypatch):
    accounts = [{"accountNumber": "111", "hashValue": "HASH1"}, {"accountNumber": "222", "hashValue": "HASH2"}]
    orders_by_hash = {"HASH1": [_hood_raw_order()], "HASH2": [_sofi_roll_raw_order()]}
    monkeypatch.setattr(httpx.Client, "get", _mock_orders_get(accounts, orders_by_hash))

    orders = client.get_recent_filled_orders(datetime.now(timezone.utc) - timedelta(minutes=15))

    assert len(orders) == 2
    assert {o.order_id for o in orders} == {1007358084142, 1007347459242}


def test_get_recent_filled_orders_empty_when_accounts_call_fails(client, monkeypatch):
    def _boom(self, path, params=None, headers=None):
        raise httpx.ConnectError("no network", request=httpx.Request("GET", "https://api.schwabapi.com/trader/v1/x"))

    monkeypatch.setattr(httpx.Client, "get", _boom)
    assert client.get_recent_filled_orders(datetime.now(timezone.utc)) == []


def test_get_recent_filled_orders_one_account_failing_does_not_block_others(client, monkeypatch):
    accounts = [{"accountNumber": "111", "hashValue": "HASH1"}, {"accountNumber": "222", "hashValue": "HASH2"}]

    def _get(self, path, params=None, headers=None):
        request = httpx.Request("GET", f"https://api.schwabapi.com/trader/v1{path}")
        if path == "/accounts/accountNumbers":
            return httpx.Response(200, json=accounts, request=request)
        if path == "/accounts/HASH1/orders":
            raise httpx.ConnectError("no network", request=request)
        return httpx.Response(200, json=[_hood_raw_order()], request=request)

    monkeypatch.setattr(httpx.Client, "get", _get)
    orders = client.get_recent_filled_orders(datetime.now(timezone.utc) - timedelta(minutes=15))
    assert len(orders) == 1


# -- get_intraday_bars (gráfico de velas + VWAP, 2026-07-31) ------------------------------


def test_get_intraday_bars_builds_session_range_and_parses_candles(client, monkeypatch):
    captured = {}

    def _get(self, path, params=None, headers=None):
        captured["path"] = path
        captured["params"] = params
        candles = [
            {"datetime": 1785410400000, "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1000},
            {"datetime": 1785410460000, "open": 100.5, "high": 100.8, "low": 100.2, "close": 100.6, "volume": 800},
        ]
        request = httpx.Request("GET", f"https://api.schwabapi.com/marketdata/v1{path}")
        return httpx.Response(200, json={"candles": candles}, request=request)

    monkeypatch.setattr(httpx.Client, "get", _get)
    bars = client.get_intraday_bars("AAPL", date(2026, 7, 30), interval_minutes=5)

    assert captured["path"] == "/pricehistory"
    params = captured["params"]
    assert params["frequencyType"] == "minute"
    assert params["frequency"] == 5
    assert params["needExtendedHoursData"] is False
    # 2026-07-30 09:30/16:00 ET == 13:30/20:00 UTC (confirmado en vivo) == estos epoch ms
    assert params["startDate"] == 1785418200000
    assert params["endDate"] == 1785441600000

    assert len(bars) == 2
    assert bars[0].timestamp < bars[1].timestamp
    assert bars[0].open == 100.0 and bars[0].close == 100.5
    assert bars[0].timestamp.tzinfo is not None


def test_get_intraday_bars_rejects_unsupported_interval(client):
    with pytest.raises(ValueError):
        client.get_intraday_bars("AAPL", date(2026, 7, 30), interval_minutes=2)


def test_get_intraday_bars_returns_empty_on_non_trading_day(client, monkeypatch):
    def _boom(self, path, params=None, headers=None):
        raise AssertionError("no debería llamar a Schwab para un día no hábil")

    monkeypatch.setattr(httpx.Client, "get", _boom)
    assert client.get_intraday_bars("AAPL", date(2026, 8, 1)) == []  # sábado
