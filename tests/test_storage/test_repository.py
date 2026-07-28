from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from options_advisor.storage import db
from options_advisor.storage import repository as repo
from options_advisor.storage.models import (
    Alert,
    CandidateContract,
    IndicatorSnapshot,
    MacroSnapshot,
    NewsItem,
    PositionSnapshot,
    RealTradeAlert,
)


@pytest.fixture
def conn():
    return db.connect(":memory:")


def _news_item(symbol: str, url: str, headline: str, published_at: datetime | None) -> NewsItem:
    return NewsItem(
        symbol=symbol,
        published_at=published_at,
        headline=headline,
        source="Yahoo",
        url=url,
        summary="resumen",
        fetched_date=date(2026, 7, 23),
    )


def test_insert_and_get_recent_news_orders_by_published_at_desc(conn):
    items = [
        _news_item("AAPL", "https://x/1", "old", datetime(2026, 7, 20, tzinfo=timezone.utc)),
        _news_item("AAPL", "https://x/2", "new", datetime(2026, 7, 22, tzinfo=timezone.utc)),
    ]
    repo.insert_news_items(conn, items)

    result = repo.get_recent_news(conn, symbol="AAPL")
    assert [r["headline"] for r in result] == ["new", "old"]


def test_insert_news_items_dedupes_by_symbol_and_url(conn):
    item = _news_item("AAPL", "https://x/1", "headline", datetime(2026, 7, 20, tzinfo=timezone.utc))
    repo.insert_news_items(conn, [item])
    repo.insert_news_items(conn, [item])  # misma corrida repetida del job, no debe duplicar

    result = repo.get_recent_news(conn, symbol="AAPL")
    assert len(result) == 1


def test_get_recent_news_filters_by_symbol(conn):
    repo.insert_news_items(
        conn,
        [
            _news_item("AAPL", "https://x/1", "aapl news", datetime(2026, 7, 20, tzinfo=timezone.utc)),
            _news_item("MSFT", "https://x/2", "msft news", datetime(2026, 7, 21, tzinfo=timezone.utc)),
        ],
    )
    result = repo.get_recent_news(conn, symbol="MSFT")
    assert len(result) == 1
    assert result[0]["symbol"] == "MSFT"


def test_get_recent_news_without_symbol_returns_all(conn):
    repo.insert_news_items(
        conn,
        [
            _news_item("AAPL", "https://x/1", "aapl news", datetime(2026, 7, 20, tzinfo=timezone.utc)),
            _news_item("MSFT", "https://x/2", "msft news", datetime(2026, 7, 21, tzinfo=timezone.utc)),
        ],
    )
    result = repo.get_recent_news(conn)
    assert len(result) == 2


def _insert_alert_with_candidate(conn, symbol: str, alert_date: date, strategy_type: str, delta: float, max_loss: float) -> None:
    candidate_id = repo.insert_candidate_contract(
        conn,
        CandidateContract(
            symbol=symbol,
            snapshot_date=alert_date,
            strategy_type=strategy_type,
            expiration_date=date(2026, 8, 15),
            strikes={"short": 100},
            delta=delta,
            greeks_source="calculated",
            conviction_score=80,
            scoring_breakdown={},
            max_loss=max_loss,
        ),
    )
    repo.insert_alert(
        conn,
        Alert(
            symbol=symbol,
            alert_date=alert_date,
            alert_ts=datetime(2026, 7, 23, 10, 0),
            candidate_contract_id=candidate_id,
            conviction_score=80,
            risk_profile="moderado",
            threshold_applied=65,
            was_notified=True,
            narrative_text="texto",
            narrative_source="fallback_template",
            dedup_key=f"{symbol}-{strategy_type}-{alert_date}",
        ),
    )


def test_get_alerts_for_date_joins_candidate_fields(conn):
    today = date(2026, 7, 23)
    _insert_alert_with_candidate(conn, "AAPL", today, "cash_secured_put", delta=0.3, max_loss=500.0)
    _insert_alert_with_candidate(conn, "MSFT", today, "iron_condor", delta=0.0, max_loss=300.0)
    _insert_alert_with_candidate(conn, "TSLA", today - timedelta(days=1), "covered_call", delta=0.2, max_loss=200.0)

    rows = repo.get_alerts_for_date(conn, today)
    assert {r["symbol"] for r in rows} == {"AAPL", "MSFT"}
    aapl = next(r for r in rows if r["symbol"] == "AAPL")
    assert aapl["strategy_type"] == "cash_secured_put"
    assert aapl["delta"] == 0.3
    assert aapl["max_loss"] == 500.0


def _insert_candidate_with_annualized_return(conn, symbol: str, annualized_return_pct: float | None) -> None:
    repo.insert_candidate_contract(
        conn,
        CandidateContract(
            symbol=symbol,
            snapshot_date=date(2026, 7, 24),
            strategy_type="cash_secured_put",
            expiration_date=date(2026, 8, 15),
            strikes={"short": 100},
            greeks_source="calculated",
            conviction_score=80,
            scoring_breakdown={},
            annualized_return_pct=annualized_return_pct,
        ),
    )


def test_get_average_annualized_return_pct_averages_recent_candidates(conn):
    _insert_candidate_with_annualized_return(conn, "AAPL", 10.0)
    _insert_candidate_with_annualized_return(conn, "MSFT", 20.0)
    _insert_candidate_with_annualized_return(conn, "TSLA", None)  # ignorado, sin dato
    assert repo.get_average_annualized_return_pct(conn) == pytest.approx(15.0)


def test_get_average_annualized_return_pct_none_without_data(conn):
    assert repo.get_average_annualized_return_pct(conn) is None


def test_upsert_macro_snapshot_persists_cpi_yoy_date(conn):
    repo.upsert_macro_snapshot(
        conn,
        MacroSnapshot(snapshot_date=date(2026, 7, 26), cpi_yoy_pct=3.1, cpi_yoy_date=date(2026, 6, 1)),
    )
    snapshot = repo.get_latest_macro_snapshot(conn)
    assert snapshot["cpi_yoy_pct"] == 3.1
    assert snapshot["cpi_yoy_date"] == "2026-06-01"  # fecha del dato de FRED, distinta de snapshot_date (fecha del job)


def test_upsert_macro_snapshot_without_cpi_yoy_date_leaves_it_null(conn):
    repo.upsert_macro_snapshot(conn, MacroSnapshot(snapshot_date=date(2026, 7, 26)))
    snapshot = repo.get_latest_macro_snapshot(conn)
    assert snapshot["cpi_yoy_date"] is None


def test_get_position_snapshots_empty_by_default(conn):
    assert repo.get_position_snapshots(conn) == {}


def test_replace_position_snapshots_round_trip(conn):
    repo.replace_position_snapshots(
        conn,
        [
            PositionSnapshot(account_number="123", symbol="TSLA  260821P00320000", quantity=-1.0, snapshot_ts=datetime(2026, 7, 27, 10, 0)),
            PositionSnapshot(account_number="123", symbol="AAPL  260821C00200000", quantity=-2.0, snapshot_ts=datetime(2026, 7, 27, 10, 0)),
        ],
    )
    snapshots = repo.get_position_snapshots(conn)
    assert snapshots == {("123", "TSLA  260821P00320000"): -1.0, ("123", "AAPL  260821C00200000"): -2.0}


def test_replace_position_snapshots_forgets_closed_positions(conn):
    """Un reemplazo completo (no upsert incremental) — una posición que ya no aparece en la
    corrida actual debe desaparecer de la tabla, así si se reabre el mismo contrato más
    adelante se detecta como operación nueva en vez de compararse contra un número viejo."""
    repo.replace_position_snapshots(
        conn, [PositionSnapshot(account_number="123", symbol="TSLA  260821P00320000", quantity=-1.0, snapshot_ts=datetime(2026, 7, 27, 10, 0))]
    )
    repo.replace_position_snapshots(
        conn, [PositionSnapshot(account_number="123", symbol="AAPL  260821C00200000", quantity=-2.0, snapshot_ts=datetime(2026, 7, 27, 10, 30))]
    )
    assert repo.get_position_snapshots(conn) == {("123", "AAPL  260821C00200000"): -2.0}


def _real_trade_alert(**overrides) -> RealTradeAlert:
    defaults = dict(
        account_number="123",
        occ_symbol="TSLA  260821P00320000",
        symbol="TSLA",
        trade_date=date(2026, 7, 27),
        trade_ts=datetime(2026, 7, 27, 10, 0),
        strategy_type="cash_secured_put",
        option_type="put",
        strike=320.0,
        expiration_date=date(2026, 8, 21),
        quantity=1,
        entry_price=5.5,
        legs=[{"side": "sell", "option_type": "put", "strike": 320.0}],
        net_premium=550.0,
        max_profit=550.0,
        max_loss=31450.0,
        breakevens=[314.5],
        probability_of_profit=0.72,
        dte=25,
        underlying_price=330.0,
        payoff_is_estimate=False,
        annualized_return_pct=25.5,
        early_close_projection=[{"pct": 50, "days": 10}],
        narrative_text="texto de la operación",
        narrative_source="claude",
    )
    defaults.update(overrides)
    return RealTradeAlert(**defaults)


def test_insert_and_get_real_trade_alerts(conn):
    repo.insert_real_trade_alert(conn, _real_trade_alert())
    rows = repo.get_real_trade_alerts(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "TSLA"
    assert row["strategy_type"] == "cash_secured_put"
    assert row["strike"] == 320.0
    assert row["quantity"] == 1
    assert json.loads(row["legs_json"]) == [{"side": "sell", "option_type": "put", "strike": 320.0}]
    assert row["narrative_text"] == "texto de la operación"


def test_get_real_trade_alerts_filters_by_symbol(conn):
    repo.insert_real_trade_alert(conn, _real_trade_alert(symbol="TSLA", occ_symbol="TSLA  260821P00320000"))
    repo.insert_real_trade_alert(conn, _real_trade_alert(symbol="AAPL", occ_symbol="AAPL  260821C00200000"))
    rows = repo.get_real_trade_alerts(conn, symbol="AAPL")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"


# --- get_recent_single_leg_candidates (vista tabla en Escaneo) ---


def _candidate(symbol: str, strategy_type: str = "cash_secured_put", snapshot_date=date(2026, 7, 27)) -> CandidateContract:
    return CandidateContract(
        symbol=symbol,
        snapshot_date=snapshot_date,
        strategy_type=strategy_type,
        expiration_date=date(2026, 8, 21),
        strikes={"short_strike": 320.0},
        delta=-0.25,
        greeks_source="broker",
        conviction_score=80,
        scoring_breakdown={},
        legs=[{"side": "sell", "option_type": "put", "strike": 320.0, "bid": 5.4, "open_interest": 500, "volume": 50}],
        net_premium=550.0,
        max_profit=550.0,
        max_loss=31450.0,
        breakevens=[314.5],
        probability_of_profit=0.72,
        dte=25,
        underlying_price=330.0,
        annualized_return_pct=25.5,
    )


def test_get_recent_single_leg_candidates_includes_single_leg_strategies(conn):
    repo.insert_candidate_contract(conn, _candidate("TSLA", strategy_type="cash_secured_put"))
    repo.insert_candidate_contract(conn, _candidate("AAPL", strategy_type="covered_call"))
    rows = repo.get_recent_single_leg_candidates(conn)
    assert {r["symbol"] for r in rows} == {"TSLA", "AAPL"}


def test_get_recent_single_leg_candidates_excludes_multi_leg_strategies(conn):
    repo.insert_candidate_contract(conn, _candidate("TSLA", strategy_type="cash_secured_put"))
    repo.insert_candidate_contract(conn, _candidate("SPY", strategy_type="iron_condor"))
    rows = repo.get_recent_single_leg_candidates(conn)
    assert {r["symbol"] for r in rows} == {"TSLA"}


def test_get_recent_single_leg_candidates_joins_iv_rank_from_indicator_snapshot(conn):
    repo.insert_indicator_snapshot(
        conn,
        IndicatorSnapshot(
            symbol="TSLA", snapshot_date=date(2026, 7, 27), snapshot_ts=datetime(2026, 7, 27, 10, 0),
            price=330.0, iv_rank=68.0, iv_rank_source="implied_volatility",
        ),
    )
    repo.insert_candidate_contract(conn, _candidate("TSLA", snapshot_date=date(2026, 7, 27)))
    rows = repo.get_recent_single_leg_candidates(conn)
    assert rows[0]["iv_rank"] == 68.0


def test_get_recent_single_leg_candidates_iv_rank_none_without_matching_snapshot(conn):
    repo.insert_candidate_contract(conn, _candidate("TSLA"))
    rows = repo.get_recent_single_leg_candidates(conn)
    assert rows[0]["iv_rank"] is None
