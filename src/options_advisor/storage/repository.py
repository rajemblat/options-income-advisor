from __future__ import annotations

import json
import sqlite3
from datetime import date

from options_advisor.storage.models import (
    Alert,
    CandidateContract,
    IndicatorSnapshot,
    InvestorProfile,
)


def insert_indicator_snapshot(conn: sqlite3.Connection, snap: IndicatorSnapshot) -> int:
    cur = conn.execute(
        """
        INSERT INTO indicator_snapshots
            (symbol, snapshot_date, snapshot_ts, price, iv_atm, iv_rank, iv_rank_source,
             hv_20d, atr_14, rsi_14, sma_8, sma_20, sma_50, sma_200, ma_cross_signal,
             support_levels, resistance_levels, raw_indicators_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, snapshot_date) DO UPDATE SET
            snapshot_ts=excluded.snapshot_ts, price=excluded.price, iv_atm=excluded.iv_atm,
            iv_rank=excluded.iv_rank, iv_rank_source=excluded.iv_rank_source, hv_20d=excluded.hv_20d,
            atr_14=excluded.atr_14, rsi_14=excluded.rsi_14, sma_8=excluded.sma_8, sma_20=excluded.sma_20,
            sma_50=excluded.sma_50, sma_200=excluded.sma_200, ma_cross_signal=excluded.ma_cross_signal,
            support_levels=excluded.support_levels, resistance_levels=excluded.resistance_levels,
            raw_indicators_json=excluded.raw_indicators_json
        """,
        (
            snap.symbol,
            snap.snapshot_date.isoformat(),
            snap.snapshot_ts.isoformat(),
            snap.price,
            snap.iv_atm,
            snap.iv_rank,
            snap.iv_rank_source,
            snap.hv_20d,
            snap.atr_14,
            snap.rsi_14,
            snap.sma_8,
            snap.sma_20,
            snap.sma_50,
            snap.sma_200,
            snap.ma_cross_signal,
            json.dumps(snap.support_levels),
            json.dumps(snap.resistance_levels),
            snap.raw_indicators_json,
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_iv_snapshot(conn: sqlite3.Connection, symbol: str, snapshot_date: date, iv_atm: float, source: str) -> None:
    conn.execute(
        """
        INSERT INTO iv_snapshots (symbol, snapshot_date, iv_atm, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(symbol, snapshot_date) DO UPDATE SET iv_atm=excluded.iv_atm, source=excluded.source
        """,
        (symbol, snapshot_date.isoformat(), iv_atm, source),
    )
    conn.commit()


def get_iv_snapshots(conn: sqlite3.Connection, symbol: str) -> list[tuple[date, float]]:
    rows = conn.execute(
        "SELECT snapshot_date, iv_atm FROM iv_snapshots WHERE symbol = ? ORDER BY snapshot_date ASC",
        (symbol,),
    ).fetchall()
    return [(date.fromisoformat(r["snapshot_date"]), r["iv_atm"]) for r in rows]


def insert_candidate_contract(conn: sqlite3.Connection, candidate: CandidateContract) -> int:
    cur = conn.execute(
        """
        INSERT INTO candidate_contracts
            (symbol, snapshot_date, strategy_type, expiration_date, strikes_json,
             delta, gamma, theta, vega, rho, greeks_source, conviction_score, scoring_breakdown_json,
             legs_json, net_premium, max_profit, max_loss, breakevens_json, probability_of_profit,
             dte, underlying_price, payoff_is_estimate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.symbol,
            candidate.snapshot_date.isoformat(),
            candidate.strategy_type,
            candidate.expiration_date.isoformat(),
            json.dumps(candidate.strikes),
            candidate.delta,
            candidate.gamma,
            candidate.theta,
            candidate.vega,
            candidate.rho,
            candidate.greeks_source,
            candidate.conviction_score,
            json.dumps(candidate.scoring_breakdown),
            json.dumps(candidate.legs),
            candidate.net_premium,
            candidate.max_profit,
            candidate.max_loss,
            json.dumps(candidate.breakevens),
            candidate.probability_of_profit,
            candidate.dte,
            candidate.underlying_price,
            int(candidate.payoff_is_estimate),
        ),
    )
    conn.commit()
    return cur.lastrowid


def alert_exists(conn: sqlite3.Connection, dedup_key: str) -> bool:
    row = conn.execute("SELECT 1 FROM alerts WHERE dedup_key = ?", (dedup_key,)).fetchone()
    return row is not None


def insert_alert(conn: sqlite3.Connection, alert: Alert) -> int | None:
    """Devuelve el id insertado, o None si ya existía una alerta con el mismo dedup_key (Sección 6 dedup)."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO alerts
            (symbol, alert_date, alert_ts, candidate_contract_id, conviction_score, risk_profile,
             threshold_applied, was_notified, narrative_text, narrative_source, dedup_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert.symbol,
            alert.alert_date.isoformat(),
            alert.alert_ts.isoformat(),
            alert.candidate_contract_id,
            alert.conviction_score,
            alert.risk_profile,
            alert.threshold_applied,
            int(alert.was_notified),
            alert.narrative_text,
            alert.narrative_source,
            alert.dedup_key,
        ),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount > 0 else None


def get_alerts(conn: sqlite3.Connection, symbol: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
    if symbol:
        return conn.execute(
            "SELECT * FROM alerts WHERE symbol = ? ORDER BY alert_ts DESC LIMIT ?", (symbol, limit)
        ).fetchall()
    return conn.execute("SELECT * FROM alerts ORDER BY alert_ts DESC LIMIT ?", (limit,)).fetchall()


def get_investor_profile(conn: sqlite3.Connection) -> InvestorProfile | None:
    row = conn.execute("SELECT * FROM investor_profile WHERE id = 1").fetchone()
    if row is None:
        return None
    return InvestorProfile(
        capital_available=row["capital_available"],
        loss_tolerance_pct=row["loss_tolerance_pct"],
        experience_level=row["experience_level"],
        risk_preference=row["risk_preference"],
        risk_level=row["risk_level"],
        conviction_threshold_override=row["conviction_threshold_override"],
        updated_at=row["updated_at"],
    )


def upsert_investor_profile(conn: sqlite3.Connection, profile: InvestorProfile) -> None:
    conn.execute(
        """
        INSERT INTO investor_profile
            (id, capital_available, loss_tolerance_pct, experience_level, risk_preference,
             risk_level, conviction_threshold_override, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            capital_available=excluded.capital_available, loss_tolerance_pct=excluded.loss_tolerance_pct,
            experience_level=excluded.experience_level, risk_preference=excluded.risk_preference,
            risk_level=excluded.risk_level, conviction_threshold_override=excluded.conviction_threshold_override,
            updated_at=excluded.updated_at
        """,
        (
            profile.capital_available,
            profile.loss_tolerance_pct,
            profile.experience_level,
            profile.risk_preference,
            profile.risk_level,
            profile.conviction_threshold_override,
            profile.updated_at.isoformat(),
        ),
    )
    conn.commit()


def get_open_assigned_positions(conn: sqlite3.Connection, symbol: str | None = None) -> list[sqlite3.Row]:
    if symbol:
        return conn.execute(
            "SELECT * FROM assigned_positions WHERE status = 'open' AND symbol = ?", (symbol,)
        ).fetchall()
    return conn.execute("SELECT * FROM assigned_positions WHERE status = 'open'").fetchall()


def insert_assigned_position(
    conn: sqlite3.Connection, symbol: str, shares: int, cost_basis: float, assigned_date: date, origin_alert_id: int | None
) -> int:
    cur = conn.execute(
        """
        INSERT INTO assigned_positions (symbol, shares, cost_basis, assigned_date, origin_alert_id, status)
        VALUES (?, ?, ?, ?, ?, 'open')
        """,
        (symbol, shares, cost_basis, assigned_date.isoformat(), origin_alert_id),
    )
    conn.commit()
    return cur.lastrowid
