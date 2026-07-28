from __future__ import annotations

import json
import sqlite3
from datetime import date

from options_advisor.storage.models import (
    Alert,
    CandidateContract,
    IndicatorSnapshot,
    InvestorProfile,
    MacroSnapshot,
    NewsItem,
    Notification,
    RealTradeAlert,
)


def insert_indicator_snapshot(conn: sqlite3.Connection, snap: IndicatorSnapshot) -> int:
    cur = conn.execute(
        """
        INSERT INTO indicator_snapshots
            (symbol, snapshot_date, snapshot_ts, price, iv_atm, iv_rank, iv_rank_source,
             hv_20d, atr_14, rsi_14, sma_8, sma_20, sma_50, sma_200, ma_cross_signal,
             support_levels, resistance_levels, raw_indicators_json, next_earnings_date,
             price_std_20, net_gex, next_ex_dividend_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, snapshot_date) DO UPDATE SET
            snapshot_ts=excluded.snapshot_ts, price=excluded.price, iv_atm=excluded.iv_atm,
            iv_rank=excluded.iv_rank, iv_rank_source=excluded.iv_rank_source, hv_20d=excluded.hv_20d,
            atr_14=excluded.atr_14, rsi_14=excluded.rsi_14, sma_8=excluded.sma_8, sma_20=excluded.sma_20,
            sma_50=excluded.sma_50, sma_200=excluded.sma_200, ma_cross_signal=excluded.ma_cross_signal,
            support_levels=excluded.support_levels, resistance_levels=excluded.resistance_levels,
            raw_indicators_json=excluded.raw_indicators_json, next_earnings_date=excluded.next_earnings_date,
            price_std_20=excluded.price_std_20, net_gex=excluded.net_gex,
            next_ex_dividend_date=excluded.next_ex_dividend_date
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
            snap.next_earnings_date.isoformat() if snap.next_earnings_date else None,
            snap.price_std_20,
            snap.net_gex,
            snap.next_ex_dividend_date.isoformat() if snap.next_ex_dividend_date else None,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_indicator_snapshot(conn: sqlite3.Connection, symbol: str, snapshot_date: date) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM indicator_snapshots WHERE symbol = ? AND snapshot_date = ?",
        (symbol, snapshot_date.isoformat()),
    ).fetchone()


def get_latest_next_earnings_date(conn: sqlite3.Connection, symbol: str) -> date | None:
    """Próxima fecha de earnings conocida del snapshot más reciente de `symbol` — usado por
    Watchlist, Eventos de riesgo y el digest pre-apertura, antes solo duplicado en cada uno."""
    row = conn.execute(
        "SELECT next_earnings_date FROM indicator_snapshots WHERE symbol = ? ORDER BY snapshot_date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row is None or row["next_earnings_date"] is None:
        return None
    return date.fromisoformat(row["next_earnings_date"])


def upsert_macro_snapshot(conn: sqlite3.Connection, snap: MacroSnapshot) -> None:
    conn.execute(
        """
        INSERT INTO macro_snapshot
            (snapshot_date, fed_funds_lower, fed_funds_upper, cpi_yoy_pct, cpi_yoy_date, unemployment_rate_pct,
             gdp_growth_annualized_pct, fed_meeting_date, fed_hike_probability, fed_hold_probability,
             fed_cut_probability, upcoming_events_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date) DO UPDATE SET
            fed_funds_lower=excluded.fed_funds_lower, fed_funds_upper=excluded.fed_funds_upper,
            cpi_yoy_pct=excluded.cpi_yoy_pct, cpi_yoy_date=excluded.cpi_yoy_date,
            unemployment_rate_pct=excluded.unemployment_rate_pct,
            gdp_growth_annualized_pct=excluded.gdp_growth_annualized_pct, fed_meeting_date=excluded.fed_meeting_date,
            fed_hike_probability=excluded.fed_hike_probability, fed_hold_probability=excluded.fed_hold_probability,
            fed_cut_probability=excluded.fed_cut_probability, upcoming_events_json=excluded.upcoming_events_json
        """,
        (
            snap.snapshot_date.isoformat(),
            snap.fed_funds_lower,
            snap.fed_funds_upper,
            snap.cpi_yoy_pct,
            snap.cpi_yoy_date.isoformat() if snap.cpi_yoy_date else None,
            snap.unemployment_rate_pct,
            snap.gdp_growth_annualized_pct,
            snap.fed_meeting_date.isoformat() if snap.fed_meeting_date else None,
            snap.fed_hike_probability,
            snap.fed_hold_probability,
            snap.fed_cut_probability,
            json.dumps(snap.upcoming_events),
        ),
    )
    conn.commit()


def get_latest_macro_snapshot(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM macro_snapshot ORDER BY snapshot_date DESC LIMIT 1").fetchone()


def insert_news_items(conn: sqlite3.Connection, items: list[NewsItem]) -> None:
    """UNIQUE(symbol, url) evita duplicados cuando el mismo artículo sigue apareciendo en
    corridas sucesivas del job dentro de la ventana de lookback de Finnhub."""
    for item in items:
        conn.execute(
            """
            INSERT OR IGNORE INTO news_items (symbol, published_at, headline, source, url, summary, fetched_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.symbol,
                item.published_at.isoformat() if item.published_at else None,
                item.headline,
                item.source,
                item.url,
                item.summary,
                item.fetched_date.isoformat(),
            ),
        )
    conn.commit()


def get_recent_news(conn: sqlite3.Connection, symbol: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
    if symbol:
        return conn.execute(
            "SELECT * FROM news_items WHERE symbol = ? ORDER BY published_at DESC LIMIT ?", (symbol, limit)
        ).fetchall()
    return conn.execute("SELECT * FROM news_items ORDER BY published_at DESC LIMIT ?", (limit,)).fetchall()


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
             dte, underlying_price, payoff_is_estimate, annualized_return_pct, early_close_projection_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            candidate.annualized_return_pct,
            json.dumps(candidate.early_close_projection),
        ),
    )
    conn.commit()
    return cur.lastrowid


def alert_exists(conn: sqlite3.Connection, dedup_key: str) -> bool:
    row = conn.execute("SELECT 1 FROM alerts WHERE dedup_key = ?", (dedup_key,)).fetchone()
    return row is not None


# Estrategias de una sola pata vendida (strategy/candidates.py::_build_single_short_leg) — las
# únicas con un solo strike/breakeven, que mapean limpio a UNA fila de una tabla plana (Sección
# 'Vista tabla en Escaneo', pedido 2026-07-27). Los spreads/Iron Condor tienen 2+ strikes y
# quedan fuera de esta vista a propósito.
SINGLE_LEG_STRATEGIES = ("cash_secured_put", "short_put_naked", "covered_call", "short_call_naked")


def get_recent_single_leg_candidates(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    """Candidatos recientes de estrategias de una sola pata, con el IV Rank del snapshot del
    mismo símbolo/fecha ya unido (LEFT JOIN — None si ese snapshot no tiene IV Rank todavía)."""
    placeholders = ",".join("?" for _ in SINGLE_LEG_STRATEGIES)
    return conn.execute(
        f"""
        SELECT cc.*, isnap.iv_rank AS iv_rank
        FROM candidate_contracts cc
        LEFT JOIN indicator_snapshots isnap
            ON isnap.symbol = cc.symbol AND isnap.snapshot_date = cc.snapshot_date
        WHERE cc.strategy_type IN ({placeholders})
        ORDER BY cc.id DESC
        LIMIT ?
        """,
        (*SINGLE_LEG_STRATEGIES, limit),
    ).fetchall()


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


def get_average_annualized_return_pct(conn: sqlite3.Connection, limit: int = 200) -> float | None:
    """Promedio de annualized_return_pct de los candidatos más recientes — usado para
    prellenar la calculadora de interés compuesto en Configuración (pedido 2026-07-24) con un
    valor de referencia real en vez de un número arbitrario. None si todavía no hay ningún
    candidato con este dato calculado (símbolos sin analizar, o antes de este campo existir)."""
    row = conn.execute(
        """
        SELECT AVG(annualized_return_pct) AS avg_pct FROM (
            SELECT annualized_return_pct FROM candidate_contracts
            WHERE annualized_return_pct IS NOT NULL
            ORDER BY id DESC LIMIT ?
        )
        """,
        (limit,),
    ).fetchone()
    return round(row["avg_pct"], 2) if row and row["avg_pct"] is not None else None


def get_alerts(conn: sqlite3.Connection, symbol: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
    if symbol:
        return conn.execute(
            "SELECT * FROM alerts WHERE symbol = ? ORDER BY alert_ts DESC LIMIT ?", (symbol, limit)
        ).fetchall()
    return conn.execute("SELECT * FROM alerts ORDER BY alert_ts DESC LIMIT ?", (limit,)).fetchall()


def get_alerts_for_date(conn: sqlite3.Connection, alert_date: date) -> list[sqlite3.Row]:
    """Alertas de un día con los campos de riesgo/dirección del candidato ya unidos (delta,
    max_loss, strategy_type) — usado por el panel de resumen de portafolio."""
    return conn.execute(
        """
        SELECT a.*, c.strategy_type AS strategy_type, c.delta AS delta, c.max_loss AS max_loss
        FROM alerts a
        LEFT JOIN candidate_contracts c ON c.id = a.candidate_contract_id
        WHERE a.alert_date = ?
        """,
        (alert_date.isoformat(),),
    ).fetchall()


def notification_exists(conn: sqlite3.Connection, kind: str, title: str) -> bool:
    """Dedup para notificaciones que no deben repetirse (ej. aviso proactivo de un evento de
    riesgo — Sección Fed/FRED, ver `alerts/digest.py`): el título ya incluye la fecha/distancia
    exacta del evento, así que kind+title exactos alcanzan como clave, sin agregar una columna
    nueva a la tabla."""
    row = conn.execute("SELECT 1 FROM notifications WHERE kind = ? AND title = ? LIMIT 1", (kind, title)).fetchone()
    return row is not None


def insert_notification(conn: sqlite3.Connection, notification: Notification) -> int:
    cur = conn.execute(
        "INSERT INTO notifications (created_at, kind, title, body, is_read) VALUES (?, ?, ?, ?, 0)",
        (notification.created_at.isoformat(), notification.kind, notification.title, notification.body),
    )
    conn.commit()
    return cur.lastrowid


def get_unread_notification_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE is_read = 0").fetchone()
    return row["n"]


def get_recent_notifications(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()


def mark_all_notifications_read(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0")
    conn.commit()


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


def get_alerted_order_leg_keys(conn: sqlite3.Connection) -> set[tuple[int, str]]:
    """(order_id, occ_symbol) de toda alerta real ya generada — clave de dedup contra
    reprocesar la misma pata de la misma orden en corridas sucesivas del cron (las ventanas de
    detección se solapan a propósito, ver alerts/real_trades.py). Filas de antes del rediseño
    vía /orders (order_id NULL) no aportan nada acá, se excluyen."""
    rows = conn.execute("SELECT order_id, occ_symbol FROM real_trade_alerts WHERE order_id IS NOT NULL").fetchall()
    return {(r["order_id"], r["occ_symbol"]) for r in rows}


def insert_real_trade_alert(conn: sqlite3.Connection, trade: RealTradeAlert) -> int:
    cur = conn.execute(
        """
        INSERT INTO real_trade_alerts
            (account_number, occ_symbol, symbol, trade_date, trade_ts, strategy_type, option_type,
             strike, expiration_date, quantity, entry_price, order_id, legs_json, net_premium,
             max_profit, max_loss, breakevens_json, probability_of_profit, dte, underlying_price,
             payoff_is_estimate, annualized_return_pct, early_close_projection_json,
             narrative_text, narrative_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade.account_number,
            trade.occ_symbol,
            trade.symbol,
            trade.trade_date.isoformat(),
            trade.trade_ts.isoformat(),
            trade.strategy_type,
            trade.option_type,
            trade.strike,
            trade.expiration_date.isoformat(),
            trade.quantity,
            trade.entry_price,
            trade.order_id,
            json.dumps(trade.legs),
            trade.net_premium,
            trade.max_profit,
            trade.max_loss,
            json.dumps(trade.breakevens),
            trade.probability_of_profit,
            trade.dte,
            trade.underlying_price,
            int(trade.payoff_is_estimate),
            trade.annualized_return_pct,
            json.dumps(trade.early_close_projection),
            trade.narrative_text,
            trade.narrative_source,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_real_trade_alerts(conn: sqlite3.Connection, symbol: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
    if symbol:
        return conn.execute(
            "SELECT * FROM real_trade_alerts WHERE symbol = ? ORDER BY trade_ts DESC LIMIT ?", (symbol, limit)
        ).fetchall()
    return conn.execute("SELECT * FROM real_trade_alerts ORDER BY trade_ts DESC LIMIT ?", (limit,)).fetchall()


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
