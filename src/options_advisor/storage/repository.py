from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta

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


def get_latest_indicator_snapshot(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row | None:
    """Último snapshot técnico conocido de `symbol` (RSI, SMAs, ATR, IV rank, soportes/resistencias),
    sin importar la fecha — para alimentar al Asesor AI con análisis técnico en vivo (usuario 2026-08-11)."""
    return conn.execute(
        "SELECT * FROM indicator_snapshots WHERE symbol = ? ORDER BY snapshot_date DESC LIMIT 1",
        (symbol,),
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
             dte, underlying_price, payoff_is_estimate, annualized_return_pct, early_close_projection_json,
             historical_move_occurrences, historical_move_total_windows,
             similar_move_occurrences, similar_move_bigger_occurrences)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            candidate.historical_move_occurrences,
            candidate.historical_move_total_windows,
            candidate.similar_move_occurrences,
            candidate.similar_move_bigger_occurrences,
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


def get_active_candidate_alerts_with_legs(conn: sqlite3.Connection, symbol: str, as_of: date) -> list[sqlite3.Row]:
    """Alertas de candidatos de `symbol` que todavía no vencieron (`expiration_date >= as_of`),
    con `legs_json`/`strategy_type` ya unidos desde `candidate_contracts` — usado por el gráfico
    de velas (pedido 2026-07-31, "conectar el gráfico con alertas") para dibujar los strikes en
    contexto de precio."""
    return conn.execute(
        """
        SELECT a.symbol, c.strategy_type, c.expiration_date, c.legs_json
        FROM alerts a
        JOIN candidate_contracts c ON c.id = a.candidate_contract_id
        WHERE a.symbol = ? AND c.expiration_date >= ?
        """,
        (symbol, as_of.isoformat()),
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


def insert_real_trade_alert(conn: sqlite3.Connection, trade: RealTradeAlert) -> int | None:
    """None si ya existe una alerta para el mismo (order_id, occ_symbol) — golpea el índice
    UNIQUE de `schema.sql::idx_real_trade_alerts_order_leg`. Incidente real 2026-07-29: dos
    procesos de detección corriendo a la vez (scheduler recién reiniciado + una corrida manual)
    leyeron el mismo set de "ya alertadas" antes de que ninguno insertara, y ambos intentaron
    grabar la misma orden — el chequeo en Python (`get_alerted_order_leg_keys`) no alcanza
    contra una carrera real entre procesos, hace falta la garantía a nivel de base. El caller
    (`alerts/real_trades.py`) debe tratar `None` como "ya la detectó otra corrida" y NO enviar
    la notificación de nuevo."""
    try:
        cur = conn.execute(
            """
            INSERT INTO real_trade_alerts
                (account_number, occ_symbol, symbol, trade_date, trade_ts, strategy_type, option_type,
                 strike, expiration_date, quantity, entry_price, order_id, legs_json, net_premium,
                 max_profit, max_loss, breakevens_json, probability_of_profit, dte, underlying_price,
                 payoff_is_estimate, annualized_return_pct, early_close_projection_json,
                 historical_move_occurrences, historical_move_total_windows,
                 similar_move_occurrences, similar_move_bigger_occurrences,
                 narrative_text, narrative_source, leg_role,
                 net_delta, net_gamma, net_theta, net_vega, net_rho, greeks_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?)
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
                trade.historical_move_occurrences,
                trade.historical_move_total_windows,
                trade.similar_move_occurrences,
                trade.similar_move_bigger_occurrences,
                trade.narrative_text,
                trade.narrative_source,
                trade.leg_role,
                trade.net_delta,
                trade.net_gamma,
                trade.net_theta,
                trade.net_vega,
                trade.net_rho,
                trade.greeks_source,
            ),
        )
    except sqlite3.IntegrityError:
        return None
    conn.commit()
    return cur.lastrowid


def get_real_trade_alerts(conn: sqlite3.Connection, symbol: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
    if symbol:
        return conn.execute(
            "SELECT * FROM real_trade_alerts WHERE symbol = ? ORDER BY trade_ts DESC LIMIT ?", (symbol, limit)
        ).fetchall()
    return conn.execute("SELECT * FROM real_trade_alerts ORDER BY trade_ts DESC LIMIT ?", (limit,)).fetchall()


def get_simulated_account(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM simulated_account WHERE id = 1").fetchone()


def init_simulated_account(conn: sqlite3.Connection, initial_capital: float, created_at: datetime) -> None:
    """`INSERT OR IGNORE`: si ya existe (corridas sucesivas del scheduler), no reinicia el
    capital ni pisa el cash acumulado — solo crea la cuenta la primera vez."""
    conn.execute(
        "INSERT OR IGNORE INTO simulated_account (id, cash, created_at) VALUES (1, ?, ?)",
        (initial_capital, created_at.isoformat()),
    )
    conn.commit()


def update_simulated_account_cash(conn: sqlite3.Connection, cash: float) -> None:
    conn.execute("UPDATE simulated_account SET cash = ? WHERE id = 1", (cash,))
    conn.commit()


def has_open_simulated_position(conn: sqlite3.Connection, symbol: str) -> bool:
    """Evita abrir una segunda posición simulada sobre el mismo subyacente mientras ya tenga
    una abierta (concentración/duplicados entre corridas sucesivas del scheduler)."""
    row = conn.execute(
        "SELECT 1 FROM simulated_positions WHERE symbol = ? AND status = 'open' LIMIT 1", (symbol,)
    ).fetchone()
    return row is not None


def insert_simulated_position(
    conn: sqlite3.Connection,
    symbol: str,
    strategy_type: str,
    strike: float,
    expiration_date: date,
    quantity: int,
    entry_date: date,
    entry_premium: float,
    collateral: float,
    entry_ts: datetime | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO simulated_positions
            (symbol, strategy_type, strike, expiration_date, quantity, entry_date, entry_premium,
             collateral, status, entry_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (symbol, strategy_type, strike, expiration_date.isoformat(), quantity, entry_date.isoformat(),
         entry_premium, collateral, entry_ts.isoformat() if entry_ts else None),
    )
    conn.commit()
    return cur.lastrowid


def get_open_simulated_positions(conn: sqlite3.Connection, symbol: str | None = None) -> list[sqlite3.Row]:
    if symbol:
        return conn.execute(
            "SELECT * FROM simulated_positions WHERE status = 'open' AND symbol = ?", (symbol,)
        ).fetchall()
    return conn.execute("SELECT * FROM simulated_positions WHERE status = 'open' ORDER BY entry_date DESC").fetchall()


def get_closed_simulated_positions(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM simulated_positions WHERE status = 'closed' ORDER BY close_date DESC LIMIT ?", (limit,)
    ).fetchall()


def mark_simulated_position(conn: sqlite3.Connection, position_id: int, marked_date: date, unrealized_pnl: float) -> None:
    conn.execute(
        "UPDATE simulated_positions SET last_marked_date = ?, last_unrealized_pnl = ? WHERE id = ?",
        (marked_date.isoformat(), unrealized_pnl, position_id),
    )
    conn.commit()


def close_simulated_position(
    conn: sqlite3.Connection, position_id: int, close_date: date, close_premium: float, close_reason: str,
    realized_pnl: float, close_ts: datetime | None = None,
) -> None:
    conn.execute(
        """
        UPDATE simulated_positions
        SET status = 'closed', close_date = ?, close_premium = ?, close_reason = ?, realized_pnl = ?,
            last_marked_date = ?, last_unrealized_pnl = ?, close_ts = ?
        WHERE id = ?
        """,
        (close_date.isoformat(), close_premium, close_reason, realized_pnl, close_date.isoformat(),
         realized_pnl, close_ts.isoformat() if close_ts else None, position_id),
    )
    conn.commit()


def upsert_simulated_equity_snapshot(
    conn: sqlite3.Connection, snapshot_date: date, cash: float, collateral_committed: float, unrealized_pnl: float, equity: float
) -> None:
    conn.execute(
        """
        INSERT INTO simulated_equity_history (snapshot_date, cash, collateral_committed, unrealized_pnl, equity)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date) DO UPDATE SET
            cash=excluded.cash, collateral_committed=excluded.collateral_committed,
            unrealized_pnl=excluded.unrealized_pnl, equity=excluded.equity
        """,
        (snapshot_date.isoformat(), cash, collateral_committed, unrealized_pnl, equity),
    )
    conn.commit()


def get_simulated_equity_history(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM simulated_equity_history ORDER BY snapshot_date ASC").fetchall()


def get_simulated_performance_stats(conn: sqlite3.Connection) -> dict:
    """Win rate y ganancia/pérdida promedio sobre las posiciones simuladas ya CERRADAS —
    Dashboard: Simulador. Todo en 0/None cuando todavía no cerró ninguna (nada que promediar)."""
    rows = conn.execute("SELECT realized_pnl FROM simulated_positions WHERE status = 'closed'").fetchall()
    pnls = [r["realized_pnl"] for r in rows if r["realized_pnl"] is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    return {
        "closed_count": len(pnls),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "total_realized_pnl": round(sum(pnls), 2) if pnls else 0.0,
    }


# --- Estado del robot (banderas compartidas dashboard/scheduler) ---

def set_robot_flag(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO robot_flags (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, datetime.now().isoformat()),
    )
    conn.commit()


def get_robot_flag(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM robot_flags WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def is_puts_paused(conn: sqlite3.Connection) -> bool:
    """¿Está pausada la venta de puts? (botón del dashboard). Por defecto NO pausada."""
    return get_robot_flag(conn, "puts_paused", "0") == "1"


def set_puts_paused(conn: sqlite3.Connection, paused: bool) -> None:
    set_robot_flag(conn, "puts_paused", "1" if paused else "0")


def is_condor_paused(conn: sqlite3.Connection) -> bool:
    """¿Está pausada la apertura de Iron Condors? (botón del dashboard). Por defecto NO pausada."""
    return get_robot_flag(conn, "condor_paused", "0") == "1"


def set_condor_paused(conn: sqlite3.Connection, paused: bool) -> None:
    set_robot_flag(conn, "condor_paused", "1" if paused else "0")


def is_condor_real_paused(conn: sqlite3.Connection) -> bool:
    """¿Está pausada la apertura de Iron Condors REALES? Bandera PROPIA del real (usuario 2026-08-14:
    "solo al real") — pausar acá NO frena el Simulador, que sigue operando en papel y aprendiendo.
    Como todas las pausas, frena SOLO las aperturas nuevas: lo que ya está abierto se sigue marcando y
    cerrando, porque de una posición viva siempre hay que poder salir. Por defecto NO pausada."""
    return get_robot_flag(conn, "condor_real_paused", "0") == "1"


def set_condor_real_paused(conn: sqlite3.Connection, paused: bool) -> None:
    set_robot_flag(conn, "condor_real_paused", "1" if paused else "0")


def is_butterfly_paused(conn: sqlite3.Connection) -> bool:
    """¿Está pausada la apertura de Iron Butterflies? (botón del dashboard). Por defecto NO pausada."""
    return get_robot_flag(conn, "butterfly_paused", "0") == "1"


def set_butterfly_paused(conn: sqlite3.Connection, paused: bool) -> None:
    set_robot_flag(conn, "butterfly_paused", "1" if paused else "0")


def is_all_paused(conn: sqlite3.Connection) -> bool:
    """Interruptor MAESTRO (usuario 2026-08): pausa TODAS las aperturas nuevas (puts + los dos irons)
    de una sola vez. El marcado/cierre de lo YA abierto sigue corriendo para no dejar posiciones sin
    manejar. Por defecto NO pausado."""
    return get_robot_flag(conn, "all_paused", "0") == "1"


def set_all_paused(conn: sqlite3.Connection, paused: bool) -> None:
    set_robot_flag(conn, "all_paused", "1" if paused else "0")


def resume_all(conn: sqlite3.Connection) -> None:
    """Reanuda TODO: limpia el interruptor maestro y las pausas por estrategia de una vez (incluida la
    del condor REAL), para que el usuario pueda re-arrancar todas las operaciones con un solo botón."""
    for key in ("all_paused", "puts_paused", "condor_paused", "condor_real_paused", "butterfly_paused"):
        set_robot_flag(conn, key, "0")


def insert_live_order_log(
    conn: sqlite3.Connection, *, log_date: date, log_ts: datetime, symbol: str, action: str,
    strike: float | None, expiration: str | None, approved: bool, final_contracts: int,
    start_limit_price: float | None, collateral: float, dry_run: bool, sent: bool,
    reasons: str | None, payload_json: str | None, ladder_json: str | None,
    bid: float | None = None, ask: float | None = None, open_context_json: str | None = None,
    price_floor: float | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO live_order_log
            (log_date, log_ts, symbol, action, strike, expiration, approved, final_contracts,
             start_limit_price, collateral, dry_run, sent, reasons, payload_json, ladder_json, bid, ask,
             open_context_json, price_floor, open_email_sent)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (log_date.isoformat(), log_ts.isoformat(), symbol, action, strike, expiration,
         1 if approved else 0, final_contracts, start_limit_price, collateral,
         1 if dry_run else 0, 1 if sent else 0, reasons, payload_json, ladder_json, bid, ask,
         open_context_json, price_floor),
    )
    conn.commit()
    return cur.lastrowid


def mark_live_order_sent(
    conn: sqlite3.Connection, order_id: int, *, schwab_order_id: str | None, order_status: str,
    fill_price: float | None, filled_contracts: int, final_limit_price: float | None,
    replacements: int, sent_ts: datetime, send_error: str | None = None,
) -> None:
    """Registra el resultado del ENVÍO REAL en la fila del log (usuario 2026-08-10, "poner en real"): id de
    Schwab, estado final, precio/contratos del fill y detalle del caminado. Marca sent=1. No toca approved
    ni el conteo del día (la fila ya contaba desde que se insertó aprobada, así el tope de 1/día se respeta
    aunque el fill falle)."""
    conn.execute(
        "UPDATE live_order_log SET sent = 1, schwab_order_id = ?, order_status = ?, fill_price = ?, "
        "filled_contracts = ?, final_limit_price = ?, replacements = ?, sent_ts = ?, send_error = ? "
        "WHERE id = ?",
        (schwab_order_id, order_status, fill_price, filled_contracts, final_limit_price,
         replacements, sent_ts.isoformat(), send_error, order_id),
    )
    conn.commit()


def set_live_order_feedback(conn: sqlite3.Connection, order_id: int, feedback: str | None, note: str | None = None) -> None:
    """Puntuación 👍/👎 (+ nota) de una orden REAL desde Real Market (usuario 2026-08-10). La guarda en
    live_order_log Y la propaga a la decisión del robot que la originó (mismo símbolo y día), para que el
    aprendizaje —que lee robot_decisions.user_feedback— la tome igual que las del simulador."""
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE live_order_log SET user_feedback = ?, user_note = ?, feedback_at = ? WHERE id = ?",
        (feedback, note, now, order_id),
    )
    row = conn.execute("SELECT symbol, log_date FROM live_order_log WHERE id = ?", (order_id,)).fetchone()
    if row:
        dec = conn.execute(
            "SELECT id FROM robot_decisions WHERE symbol = ? AND decision_date = ? AND action = 'open' "
            "ORDER BY created_at DESC LIMIT 1",
            (row["symbol"], row["log_date"]),
        ).fetchone()
        if dec:
            conn.execute(
                "UPDATE robot_decisions SET user_feedback = ?, user_note = COALESCE(?, user_note), feedback_at = ? WHERE id = ?",
                (feedback, note, now, dec["id"]),
            )
    conn.commit()


def has_live_committed_order_for_symbol_today(conn: sqlite3.Connection, symbol: str, day: date) -> bool:
    """¿Ya hay una orden REAL de hoy sobre este símbolo que OCUPA lugar (llenó o quedó viva/esperando)?
    Evita abrir una segunda posición real sobre el MISMO símbolo el mismo día (usuario 2026-08-10: "una
    operación más" = otra oportunidad distinta, no repetir la misma). Una orden que murió (rechazada/
    cancelada) no cuenta, así ese símbolo se puede reintentar."""
    row = conn.execute(
        "SELECT 1 FROM live_order_log WHERE log_date = ? AND symbol = ? AND action = 'SELL_TO_OPEN' "
        "AND dry_run = 0 AND sent = 1 AND (order_status IS NULL OR order_status NOT IN "
        "('REJECTED','CANCELED','EXPIRED','error')) LIMIT 1",
        (day.isoformat(), symbol),
    ).fetchone()
    return row is not None


def get_live_orders_today(conn: sqlite3.Connection, day: date) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM live_order_log WHERE log_date = ? ORDER BY log_ts DESC", (day.isoformat(),)
    ).fetchall()


def get_live_orders_between(conn: sqlite3.Connection, start: date, end: date) -> list[sqlite3.Row]:
    """Órdenes reales entre dos fechas (inclusive), para el filtro por período de Real Market (usuario
    2026-08-10: 'mostrar las que alcanzó a entrar y las que no, por día/semana/mes')."""
    return conn.execute(
        "SELECT * FROM live_order_log WHERE log_date >= ? AND log_date <= ? ORDER BY log_ts DESC",
        (start.isoformat(), end.isoformat()),
    ).fetchall()


def get_closed_real_positions_between(conn: sqlite3.Connection, start: date, end: date) -> list[sqlite3.Row]:
    """Posiciones REALES CERRADAS cuya fecha de CIERRE cae en el período — para la ganancia realizada por
    día/mes/año del registro real (usuario 2026-08-10)."""
    return conn.execute(
        "SELECT * FROM live_order_log WHERE action = 'SELL_TO_OPEN' AND dry_run = 0 AND closed = 1 "
        "AND substr(close_ts, 1, 10) >= ? AND substr(close_ts, 1, 10) <= ? ORDER BY close_ts DESC",
        (start.isoformat(), end.isoformat()),
    ).fetchall()


# Estados en los que la orden REAL murió sin abrir posición: NO ocupan el cupo del día, el robot puede
# reintentar (usuario 2026-08-10: "que solo cuente si LLENA; un rechazo o cancelación no gasta el día").
# Cualquier OTRO estado (FILLED, o algo ambiguo/desconocido) SÍ ocupa el cupo — conservador: ante la duda
# de si abrió, no mandamos otra (evita abrir dos posiciones por un fill mal reconciliado).
_LIVE_DEAD_STATUSES = ("REJECTED", "CANCELED", "EXPIRED", "error")


def _live_slot_filter() -> str:
    placeholders = ", ".join("?" for _ in _LIVE_DEAD_STATUSES)
    return (
        "action = 'SELL_TO_OPEN' AND dry_run = 0 AND sent = 1 "
        f"AND (order_status IS NULL OR order_status NOT IN ({placeholders}))"
    )


def count_live_approved_opens_today(conn: sqlite3.Connection, day: date) -> int:
    """Aperturas REALES que OCUPAN el cupo del día. Cuenta órdenes reales ENVIADAS (dry_run=0, sent=1) que
    NO terminaron muertas (rechazada/cancelada/expirada/error) — o sea, las que llenaron o cuyo resultado
    quedó ambiguo. Un rechazo/cancelación NO cuenta, así el robot reintenta hasta abrir 1 (usuario 2026-08-10)."""
    row = conn.execute(
        f"SELECT COUNT(*) FROM live_order_log WHERE log_date = ? AND {_live_slot_filter()}",
        (day.isoformat(), *_LIVE_DEAD_STATUSES),
    ).fetchone()
    return row[0] if row else 0


def count_live_approved_opens_this_week(conn: sqlite3.Connection, day: date) -> int:
    """Aperturas REALES que ocupan cupo en la semana (lunes→`day`), para el tope semanal. Misma regla que
    el diario: solo cuentan las enviadas que no murieron."""
    monday = day - timedelta(days=day.weekday())
    row = conn.execute(
        f"SELECT COUNT(*) FROM live_order_log WHERE log_date >= ? AND log_date <= ? AND {_live_slot_filter()}",
        (monday.isoformat(), day.isoformat(), *_LIVE_DEAD_STATUSES),
    ).fetchone()
    return row[0] if row else 0


def sum_live_collateral_today(conn: sqlite3.Connection, day: date) -> float:
    """Colateral/margen comprometido hoy por aperturas REALES que ocupan cupo (para el tope de capital).
    Una orden rechazada/cancelada no comprometió nada, así que no suma."""
    row = conn.execute(
        f"SELECT COALESCE(SUM(collateral), 0) FROM live_order_log WHERE log_date = ? AND {_live_slot_filter()}",
        (day.isoformat(), *_LIVE_DEAD_STATUSES),
    ).fetchone()
    return float(row[0]) if row else 0.0


def arm_live_today(conn: sqlite3.Connection, day: date) -> None:
    """El usuario apretó START para operar REAL hoy (usuario 2026-08-09: "cada mañana yo debo darle
    start"). Guarda la fecha; el armado vale SOLO para ese día — al día siguiente hay que re-armar."""
    set_robot_flag(conn, "live.armed_date", day.isoformat())


def disarm_live(conn: sqlite3.Connection) -> None:
    """Desarma el trading real (frena nuevas órdenes; lo abierto se sigue gestionando)."""
    set_robot_flag(conn, "live.armed_date", "")


def is_live_armed(conn: sqlite3.Connection, day: date) -> bool:
    """¿Está ARMADO el trading real para `day`? Solo True si el usuario dio START hoy — se resetea solo
    cada día (no queda armado de un día para el otro)."""
    return get_robot_flag(conn, "live.armed_date", "") == day.isoformat()


def set_live_kill_switch(conn: sqlite3.Connection, on: bool) -> None:
    """Freno de emergencia del trading real: corta TODO al instante, aun estando armado."""
    set_robot_flag(conn, "live.kill_switch", "1" if on else "0")


def is_live_kill_switch(conn: sqlite3.Connection) -> bool:
    return get_robot_flag(conn, "live.kill_switch", "0") == "1"


def get_max_live_orders_per_day(conn: sqlite3.Connection, default: int, day: date) -> int:
    """Tope AJUSTABLE (desde Real Market) de órdenes reales por día. Override en vivo del valor de config,
    sin reiniciar (usuario 2026-08-10: "un botón de cuántas órdenes por día quiero"). El override vale
    SOLO para el día en que se seteó (usuario 2026-08-10, punto 2/5: "lo que arma el robot debe resetear
    todos los días y comenzar desde 0") — a la medianoche vuelve solo al `default` de config, así el robot
    arranca cada jornada con el tope base y no arrastra el 'abrir más' de ayer. Formato guardado:
    'YYYY-MM-DD:N'. Si no está seteado o es de otro día, usa `default`."""
    v = get_robot_flag(conn, "live.max_orders_per_day", None)
    if v is None or v == "":
        return default
    try:
        stored_day, _, n = v.partition(":")
        if not n:  # formato viejo sin fecha (compat): se ignora para no arrastrar de días previos
            return default
        if stored_day != day.isoformat():
            return default
        return int(n)
    except (TypeError, ValueError):
        return default


def set_max_live_orders_per_day(conn: sqlite3.Connection, n: int, day: date) -> None:
    """Guarda el tope override junto con la fecha para la que vale (ver get_max_live_orders_per_day)."""
    set_robot_flag(conn, "live.max_orders_per_day", f"{day.isoformat()}:{int(n)}")


# --- Autorización SEPARADA del Iron Condor real (usuario 2026-08-13: "un botón para autorizar a operar
#     el condor separado de los naked, y cuántas operaciones por día autorizo") ---
def arm_condor_live_today(conn: sqlite3.Connection, day: date) -> None:
    """START propio del Iron Condor real de HOY, INDEPENDIENTE del de los naked. Vale solo para `day`."""
    set_robot_flag(conn, "condor.live_armed_date", day.isoformat())


def disarm_condor_live(conn: sqlite3.Connection) -> None:
    set_robot_flag(conn, "condor.live_armed_date", "")


def is_condor_live_armed(conn: sqlite3.Connection, day: date) -> bool:
    """¿El usuario autorizó HOY el condor real? Botón aparte del START de los naked; se resetea cada día."""
    return get_robot_flag(conn, "condor.live_armed_date", "") == day.isoformat()


def get_condor_live_max_per_day(conn: sqlite3.Connection, default: int, day: date) -> int:
    """Cuántos condors reales por día autoriza el usuario (override en vivo del valor de config, aparte del
    tope de los naked). Vale SOLO para el día seteado; a la medianoche vuelve al `default`. Formato
    'YYYY-MM-DD:N'."""
    v = get_robot_flag(conn, "condor.live_max_per_day", None)
    if v is None or v == "":
        return default
    try:
        stored_day, _, n = v.partition(":")
        if not n or stored_day != day.isoformat():
            return default
        return int(n)
    except (TypeError, ValueError):
        return default


def set_condor_live_max_per_day(conn: sqlite3.Connection, n: int, day: date) -> None:
    set_robot_flag(conn, "condor.live_max_per_day", f"{day.isoformat()}:{int(n)}")


def get_resting_real_open_orders(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Órdenes de APERTURA reales que quedaron PUESTAS al mid y todavía NO llenaron (ni murieron): para
    seguir negociándolas re-preciándolas al mid actual cada escaneo (usuario 2026-08-10: 'está a un centavo
    y no negocia'). Son las enviadas (sent=1, real), no cerradas, con id de Schwab y estado no-terminal."""
    return conn.execute(
        "SELECT * FROM live_order_log WHERE action = 'SELL_TO_OPEN' AND dry_run = 0 AND sent = 1 "
        "AND COALESCE(closed, 0) = 0 AND schwab_order_id IS NOT NULL AND order_status IS NOT NULL "
        "AND order_status NOT IN ('FILLED', 'REJECTED', 'CANCELED', 'EXPIRED', 'error') ORDER BY id"
    ).fetchall()


def update_resting_order_reprice(conn: sqlite3.Connection, order_id: int, *, new_schwab_order_id: str,
                                 new_limit_price: float, bid: float | None, ask: float | None) -> None:
    """Actualiza una orden en espera tras re-preciarla al mid actual: nuevo id de Schwab, nuevo límite,
    bid/ask frescos, y suma 1 a los reemplazos."""
    conn.execute(
        "UPDATE live_order_log SET schwab_order_id = ?, final_limit_price = ?, bid = ?, ask = ?, "
        "replacements = COALESCE(replacements, 0) + 1 WHERE id = ?",
        (new_schwab_order_id, new_limit_price, bid, ask, order_id),
    )
    conn.commit()


def get_open_fills_needing_email(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Aperturas REALES que LLENARON y todavía NO tienen su email de apertura mandado (idempotente,
    usuario 2026-08-11: 'no me llegó el email de NU'). El scheduler las recorre cada escaneo y manda 1
    email por cada una, sin importar si llenó al toque o negociando — así no se pierde ninguno ni se duplica."""
    return conn.execute(
        "SELECT * FROM live_order_log WHERE action = 'SELL_TO_OPEN' AND dry_run = 0 AND sent = 1 "
        "AND order_status = 'FILLED' AND COALESCE(open_email_sent, 0) = 0 AND COALESCE(closed, 0) = 0 "
        "ORDER BY id"
    ).fetchall()


def mark_open_email_sent(conn: sqlite3.Connection, order_id: int) -> None:
    conn.execute("UPDATE live_order_log SET open_email_sent = 1 WHERE id = ?", (int(order_id),))
    conn.commit()


def get_open_real_put_positions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Posiciones REALES de put abiertas por el robot y todavía SIN cerrar: aperturas (SELL_TO_OPEN) reales
    que LLENARON y no tienen `closed=1`. Base del cierre real con las reglas del simulador (usuario
    2026-08-10). No incluye las que quedaron 'esperando' sin llenar (esas no son posición todavía)."""
    return conn.execute(
        "SELECT * FROM live_order_log WHERE action = 'SELL_TO_OPEN' AND dry_run = 0 AND sent = 1 "
        "AND order_status = 'FILLED' AND COALESCE(closed, 0) = 0 ORDER BY id"
    ).fetchall()


def mark_real_position_closed(
    conn: sqlite3.Connection, open_id: int, *, close_ts: datetime, close_fill_price: float | None,
    close_reason: str, realized_pnl: float | None, close_schwab_order_id: str | None,
    pnl_is_estimate: bool = False,
) -> None:
    """Marca la apertura real como CERRADA con su P&L realizado (usuario 2026-08-10).
    `pnl_is_estimate`: el P&L no salió del fill exacto de Schwab sino del valor de mercado al detectar
    el cierre (posición cerrada por fuera del robot). Se guarda igual — un número aproximado y marcado
    como tal sirve para el control; un NULL desaparecía de todos los totales (usuario 2026-08-14)."""
    conn.execute(
        "UPDATE live_order_log SET closed = 1, close_ts = ?, close_fill_price = ?, close_reason = ?, "
        "realized_pnl = ?, close_schwab_order_id = ?, pnl_is_estimate = ? WHERE id = ?",
        (close_ts.isoformat(), close_fill_price, close_reason, realized_pnl, close_schwab_order_id,
         1 if pnl_is_estimate else 0, open_id),
    )
    conn.commit()


def bump_real_close_attempt(conn: sqlite3.Connection, open_id: int, error: str | None) -> int:
    """Registra UN intento de cierre que no llegó a llenar, con su motivo. Devuelve cuántos van.
    Existe porque el robot podía intentar cerrar, fallar y volver a fallar sin dejar rastro visible:
    NU y NVDA quedaron abiertas al 75% y 45% de ganancia hasta que el usuario las cerró a mano."""
    conn.execute(
        "UPDATE live_order_log SET close_attempts = COALESCE(close_attempts, 0) + 1, last_close_error = ? "
        "WHERE id = ?",
        ((error or "")[:500], open_id),
    )
    conn.commit()
    row = conn.execute("SELECT close_attempts FROM live_order_log WHERE id = ?", (open_id,)).fetchone()
    return (row["close_attempts"] or 0) if row else 0


def set_real_close_price_manual(conn: sqlite3.Connection, open_id: int, close_price: float) -> float | None:
    """El usuario carga a mano el precio al que SALIÓ una posición que se cerró fuera del robot, y el
    P&L se calcula solo (usuario 2026-08-14: "que todas las ganancias se ingresen, para llevar un
    control"). Devuelve el P&L calculado. Queda marcada como NO estimada: el número lo puso él.
    Solo aplica a filas ya cerradas — no reabre ni toca nada vivo."""
    row = conn.execute(
        "SELECT fill_price, filled_contracts, final_contracts, closed FROM live_order_log WHERE id = ?",
        (open_id,),
    ).fetchone()
    if row is None or not row["closed"] or row["fill_price"] is None:
        return None
    contratos = row["filled_contracts"] or row["final_contracts"] or 1
    realizado = round((float(row["fill_price"]) - float(close_price)) * 100.0 * contratos, 2)
    conn.execute(
        "UPDATE live_order_log SET close_fill_price = ?, realized_pnl = ?, pnl_is_estimate = 0 WHERE id = ?",
        (float(close_price), realizado, open_id),
    )
    conn.commit()
    return realizado


def reset_real_close_attempts(conn: sqlite3.Connection, open_id: int) -> None:
    """La posición se cerró bien: limpia el contador de intentos fallidos y el aviso pendiente."""
    conn.execute(
        "UPDATE live_order_log SET close_attempts = 0, last_close_error = NULL, close_fail_email_sent = 0 "
        "WHERE id = ?", (open_id,)
    )
    conn.commit()


def mark_close_fail_email_sent(conn: sqlite3.Connection, open_id: int) -> None:
    conn.execute("UPDATE live_order_log SET close_fail_email_sent = 1 WHERE id = ?", (open_id,))
    conn.commit()


def close_fail_email_pending(conn: sqlite3.Connection, open_id: int) -> bool:
    row = conn.execute("SELECT close_fail_email_sent FROM live_order_log WHERE id = ?", (open_id,)).fetchone()
    return not (row and row["close_fail_email_sent"])


def get_open_decision_for(conn: sqlite3.Connection, symbol: str, day: str, strike: float | None = None) -> sqlite3.Row | None:
    """La decisión de APERTURA del robot detrás de una orden real — para mostrar el detalle y puntuarla igual
    que en el Simulador (usuario 2026-08-10). Si se pasa `strike`, matchea la decisión de ESE strike (el robot
    puede haber evaluado el mismo símbolo en varios strikes/momentos; sin esto la placa mostraba otro strike).
    Si no hay una decisión de ese strike exacto, devuelve None (mejor sin datos que datos del strike equivocado)."""
    rows = conn.execute(
        "SELECT * FROM robot_decisions WHERE symbol = ? AND decision_date = ? AND action = 'open' "
        "ORDER BY created_at DESC",
        (symbol, day),
    ).fetchall()
    if not rows:
        return None
    if strike is None:
        return rows[0]
    import json as _json
    for r in rows:
        try:
            ctx = _json.loads(r["context_json"]) if r["context_json"] else {}
        except Exception:
            ctx = {}
        cs = ctx.get("chosen_strike")
        if isinstance(cs, (int, float)) and abs(float(cs) - float(strike)) < 0.01:
            return r
    return None


def _trailing_stop_losses_today(conn: sqlite3.Connection, table: str, day: date) -> int:
    """Cuántos cierres SEGUIDOS por stop-loss hubo hoy en `table`, contando desde el más reciente hacia
    atrás (freno del día de los irons, usuario 2026-08-08). Una ganancia (cualquier reason != stop_loss)
    corta la racha. `table` es un nombre FIJO interno (iron_condor_positions / butterfly_positions), no
    entra input del usuario."""
    rows = conn.execute(
        f"SELECT close_reason FROM {table} WHERE status = 'closed' AND close_date = ? ORDER BY close_ts DESC",
        (day.isoformat(),),
    ).fetchall()
    n = 0
    for r in rows:
        if r["close_reason"] == "stop_loss":
            n += 1
        else:
            break   # la racha se corta con el primer cierre que no fue stop-loss
    return n


def condor_consecutive_stop_losses_today(conn: sqlite3.Connection, day: date) -> int:
    """Stop-losses SEGUIDOS de Iron Condor hoy (para el freno del día)."""
    return _trailing_stop_losses_today(conn, "iron_condor_positions", day)


def butterfly_consecutive_stop_losses_today(conn: sqlite3.Connection, day: date) -> int:
    """Stop-losses SEGUIDOS de Iron Butterfly hoy (para el freno del día)."""
    return _trailing_stop_losses_today(conn, "butterfly_positions", day)


def get_max_puts_per_day(conn: sqlite3.Connection, default: int) -> int:
    """Tope diario de naked puts elegido por el usuario desde el dashboard (usuario 2026-08-07, trading
    real: "quiero ajustar cuántos trades hace por día, de 1 a 20+, antes de que abra el mercado"). Si no
    lo tocó nunca, cae al default de config (`max_opens_per_day`). Nunca rompe: valor inválido → default."""
    raw = get_robot_flag(conn, "sim.max_puts_per_day", None)
    if raw is None:
        return default
    try:
        n = int(raw)
        return n if n >= 1 else default
    except (ValueError, TypeError):
        return default


def set_max_puts_per_day(conn: sqlite3.Connection, n: int) -> None:
    """Guarda el tope diario de naked puts elegido en el dashboard (mínimo 1)."""
    set_robot_flag(conn, "sim.max_puts_per_day", str(max(1, int(n))))


def count_puts_opens_today(conn: sqlite3.Connection, today: date) -> int:
    """Cuántos puts abrió el robot HOY — para el tope diario de tickets.

    Cuenta SOLO las aperturas de naked puts: las de las estrategias intradía (Iron Condor, Iron
    Butterfly) guardan `strategy` en su contexto y las de puts no, así que el filtro es "sin strategy".

    Bug real (2026-08-14): la versión anterior excluía solo al butterfly con un LIKE, así que las 10
    aperturas del Iron Condor de ese día contaron como puts. El contador marcó 10/4, dio el tope por
    alcanzado y el simulador de puts NO abrió ninguna posición en todo el día — bloqueado por la
    actividad de otra estrategia. El usuario lo vio al revés ("puse 4 y va 10") porque el número que
    veía en pantalla era el del condor contado en el casillero de los puts."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM robot_decisions
        WHERE action = 'open' AND decision_date = ?
          AND json_extract(context_json, '$.strategy') IS NULL
        """,
        (today.isoformat(),),
    ).fetchone()
    return row["n"] if row else 0


def get_condor_max_per_day(conn: sqlite3.Connection, default: int) -> int:
    """Tope diario de Iron Condors de PAPEL, ajustable desde el dashboard (usuario 2026-08-14: quería
    limitarlos a 4 y no encontraba dónde — el único control que había era el de los puts). 0 = sin
    tope. Sin valor guardado cae al `max_per_day` del config."""
    raw = get_robot_flag(conn, "sim.max_condors_per_day", "")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


def set_condor_max_per_day(conn: sqlite3.Connection, n: int) -> None:
    set_robot_flag(conn, "sim.max_condors_per_day", str(max(0, int(n))))


# --- Iron Butterfly 0DTE (Estrategia 2) ---

def insert_butterfly_position(
    conn: sqlite3.Connection,
    *,
    underlying: str,
    direction: str,
    entry_date: date,
    expiration_date: date,
    body_strike: float,
    long_put_strike: float,
    long_call_strike: float,
    entry_net_credit: float,
    max_loss: float,
    max_profit: float,
    lower_breakeven: float,
    upper_breakeven: float,
    entry_spot: float,
    entry_ts: datetime | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO butterfly_positions
            (underlying, direction, entry_date, entry_ts, expiration_date, body_strike,
             long_put_strike, long_call_strike, entry_net_credit, max_loss, max_profit,
             lower_breakeven, upper_breakeven, entry_spot, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
        """,
        (
            underlying, direction, entry_date.isoformat(),
            entry_ts.isoformat() if entry_ts else None, expiration_date.isoformat(), body_strike,
            long_put_strike, long_call_strike, entry_net_credit, max_loss, max_profit,
            lower_breakeven, upper_breakeven, entry_spot,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_open_butterfly_positions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM butterfly_positions WHERE status = 'open' ORDER BY id DESC").fetchall()


def get_closed_butterfly_positions(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM butterfly_positions WHERE status = 'closed' ORDER BY close_ts DESC LIMIT ?", (limit,)
    ).fetchall()


def mark_butterfly_position(conn: sqlite3.Connection, position_id: int, marked_ts: datetime, unrealized_pnl: float) -> None:
    conn.execute(
        "UPDATE butterfly_positions SET last_marked_ts = ?, last_unrealized_pnl = ? WHERE id = ?",
        (marked_ts.isoformat(), unrealized_pnl, position_id),
    )
    conn.commit()


def close_butterfly_position(
    conn: sqlite3.Connection, position_id: int, close_date: date, close_value: float, close_reason: str,
    realized_pnl: float, close_ts: datetime | None = None,
) -> None:
    conn.execute(
        """
        UPDATE butterfly_positions
        SET status = 'closed', close_date = ?, close_value = ?, close_reason = ?, realized_pnl = ?,
            last_unrealized_pnl = ?, close_ts = ?
        WHERE id = ?
        """,
        (close_date.isoformat(), close_value, close_reason, realized_pnl, realized_pnl,
         close_ts.isoformat() if close_ts else None, position_id),
    )
    conn.commit()


def get_butterfly_performance_stats(conn: sqlite3.Connection) -> dict:
    """Resumen del Iron Butterfly para el dashboard: abiertas, cerradas, win rate y P&L total."""
    closed = conn.execute("SELECT realized_pnl FROM butterfly_positions WHERE status = 'closed'").fetchall()
    pnls = [r["realized_pnl"] for r in closed if r["realized_pnl"] is not None]
    open_rows = conn.execute("SELECT last_unrealized_pnl FROM butterfly_positions WHERE status = 'open'").fetchall()
    unrealized = sum((r["last_unrealized_pnl"] or 0.0) for r in open_rows)
    wins = [p for p in pnls if p > 0]
    return {
        "open_count": len(open_rows),
        "closed_count": len(pnls),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "total_realized_pnl": round(sum(pnls), 2) if pnls else 0.0,
        "open_unrealized_pnl": round(unrealized, 2),
    }


# --- Iron Condor 0DTE (Estrategia 3, 2026-08-05) ---

def insert_condor_position(
    conn: sqlite3.Connection,
    *,
    underlying: str,
    entry_date: date,
    expiration_date: date,
    short_put_strike: float,
    short_call_strike: float,
    long_put_strike: float,
    long_call_strike: float,
    entry_net_credit: float,
    max_loss: float,
    max_profit: float,
    lower_breakeven: float,
    upper_breakeven: float,
    entry_spot: float,
    entry_ts: datetime | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO iron_condor_positions
            (underlying, entry_date, entry_ts, expiration_date, short_put_strike, short_call_strike,
             long_put_strike, long_call_strike, entry_net_credit, max_loss, max_profit,
             lower_breakeven, upper_breakeven, entry_spot, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
        """,
        (
            underlying, entry_date.isoformat(), entry_ts.isoformat() if entry_ts else None,
            expiration_date.isoformat(), short_put_strike, short_call_strike, long_put_strike,
            long_call_strike, entry_net_credit, max_loss, max_profit, lower_breakeven,
            upper_breakeven, entry_spot,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_open_condor_positions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM iron_condor_positions WHERE status = 'open' ORDER BY id DESC").fetchall()


def get_closed_condor_positions(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM iron_condor_positions WHERE status = 'closed' ORDER BY close_ts DESC LIMIT ?", (limit,)
    ).fetchall()


def count_condor_opens_today(conn: sqlite3.Connection, day: date) -> int:
    """Cuántos Iron Condors se abrieron HOY (para el tope de `max_per_day`)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM iron_condor_positions WHERE entry_date = ?", (day.isoformat(),)
    ).fetchone()
    return row[0] if row else 0


def mark_condor_position(conn: sqlite3.Connection, position_id: int, marked_ts: datetime, unrealized_pnl: float) -> None:
    conn.execute(
        "UPDATE iron_condor_positions SET last_marked_ts = ?, last_unrealized_pnl = ? WHERE id = ?",
        (marked_ts.isoformat(), unrealized_pnl, position_id),
    )
    conn.commit()


def close_condor_position(
    conn: sqlite3.Connection, position_id: int, close_date: date, close_value: float, close_reason: str,
    realized_pnl: float, close_ts: datetime | None = None,
) -> None:
    conn.execute(
        """
        UPDATE iron_condor_positions
        SET status = 'closed', close_date = ?, close_value = ?, close_reason = ?, realized_pnl = ?,
            last_unrealized_pnl = ?, close_ts = ?
        WHERE id = ?
        """,
        (close_date.isoformat(), close_value, close_reason, realized_pnl, realized_pnl,
         close_ts.isoformat() if close_ts else None, position_id),
    )
    conn.commit()


def get_condor_performance_stats(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """Resumen del Iron Condor para el dashboard: abiertas, cerradas, win rate y P&L total, MÁS la
    ganancia REALIZADA de HOY (usuario 2026-08-12: "quiero ver la ganancia que se hizo en el día")."""
    closed = conn.execute("SELECT realized_pnl FROM iron_condor_positions WHERE status = 'closed'").fetchall()
    pnls = [r["realized_pnl"] for r in closed if r["realized_pnl"] is not None]
    open_rows = conn.execute("SELECT last_unrealized_pnl FROM iron_condor_positions WHERE status = 'open'").fetchall()
    unrealized = sum((r["last_unrealized_pnl"] or 0.0) for r in open_rows)
    wins = [p for p in pnls if p > 0]
    # P&L realizado SOLO de los condors cerrados HOY (por fecha de close_ts).
    _day = (today or date.today()).isoformat()
    today_rows = conn.execute(
        "SELECT realized_pnl FROM iron_condor_positions WHERE status = 'closed' "
        "AND realized_pnl IS NOT NULL AND substr(close_ts, 1, 10) = ?", (_day,)
    ).fetchall()
    today_pnls = [r["realized_pnl"] for r in today_rows]
    return {
        "open_count": len(open_rows),
        "closed_count": len(pnls),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "total_realized_pnl": round(sum(pnls), 2) if pnls else 0.0,
        "open_unrealized_pnl": round(unrealized, 2),
        "realized_pnl_today": round(sum(today_pnls), 2) if today_pnls else 0.0,
        "closed_count_today": len(today_pnls),
    }


# ============================ Iron Condor con DINERO REAL (Schwab) ============================
# Espejo de las funciones del condor de PAPEL (arriba) pero sobre real_condor_positions, con los
# símbolos OCC exactos y los ids de orden de Schwab (usuario 2026-08-13: pasar el condor a real con
# el mismo cerebro del papel). El real lleva su PROPIO conteo/tope diario y su propio P&L.

def insert_real_condor_position(
    conn: sqlite3.Connection,
    *,
    underlying: str,
    entry_date: date,
    expiration_date: date,
    short_put_strike: float,
    short_call_strike: float,
    long_put_strike: float,
    long_call_strike: float,
    short_put_symbol: str,
    long_put_symbol: str,
    short_call_symbol: str,
    long_call_symbol: str,
    quantity: int,
    entry_net_credit: float,
    max_loss: float,
    max_profit: float,
    lower_breakeven: float,
    upper_breakeven: float,
    entry_spot: float,
    open_schwab_order_id: str | None,
    status: str = "working",
    entry_ts: datetime | None = None,
) -> int:
    """Registra un condor REAL apenas se MANDA la orden combinada (status 'working' hasta que llene).
    `entry_net_credit` es el crédito TOTAL en dólares esperado (se corrige al fill real). Guarda los
    símbolos OCC EXACTOS para poder cerrar exactamente las mismas 4 patas."""
    cur = conn.execute(
        """
        INSERT INTO real_condor_positions
            (underlying, entry_date, entry_ts, expiration_date, short_put_strike, short_call_strike,
             long_put_strike, long_call_strike, short_put_symbol, long_put_symbol, short_call_symbol,
             long_call_symbol, quantity, entry_net_credit, max_loss, max_profit, lower_breakeven,
             upper_breakeven, entry_spot, open_schwab_order_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            underlying, entry_date.isoformat(), entry_ts.isoformat() if entry_ts else None,
            expiration_date.isoformat(), short_put_strike, short_call_strike, long_put_strike,
            long_call_strike, short_put_symbol, long_put_symbol, short_call_symbol, long_call_symbol,
            quantity, entry_net_credit, max_loss, max_profit, lower_breakeven, upper_breakeven,
            entry_spot, open_schwab_order_id, status,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_open_real_condor_positions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Condors reales vivos: 'working' (orden puesta, sin llenar) y 'open' (llenó). Ambos hay que
    seguirlos cada tick (reconciliar el fill o marcar/cerrar)."""
    return conn.execute(
        "SELECT * FROM real_condor_positions WHERE status IN ('working', 'open') ORDER BY id DESC"
    ).fetchall()


def get_closed_real_condor_positions(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM real_condor_positions WHERE status = 'closed' ORDER BY close_ts DESC LIMIT ?", (limit,)
    ).fetchall()


def count_real_condor_opens_today(conn: sqlite3.Connection, day: date) -> int:
    """Cuántos condors REALES se mandaron HOY (cuenta 'working'/'open'/'closed' con entry_date=hoy),
    para el tope de live_max_per_day. Una fila 'working' YA ocupa el cupo del día — así un fill que
    tarda no habilita mandar otro (seguridad > oportunidad)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM real_condor_positions WHERE entry_date = ?", (day.isoformat(),)
    ).fetchone()
    return row[0] if row else 0


def real_condor_consecutive_stop_losses_today(conn: sqlite3.Connection, day: date) -> int:
    """Racha de stop-loss al cierre de HOY en condors reales (freno del día, igual que el paper)."""
    return _trailing_stop_losses_today(conn, "real_condor_positions", day)


def mark_real_condor_fill(
    conn: sqlite3.Connection, position_id: int, *, entry_credit_ps: float, entry_net_credit: float,
    open_schwab_order_id: str | None, entry_ts: datetime | None = None,
) -> None:
    """La orden combinada LLENÓ: pasa de 'working' a 'open' y fija el crédito real cobrado."""
    conn.execute(
        """
        UPDATE real_condor_positions
        SET status = 'open', entry_credit_ps = ?, entry_net_credit = ?, open_schwab_order_id = ?,
            entry_ts = COALESCE(?, entry_ts)
        WHERE id = ?
        """,
        (entry_credit_ps, entry_net_credit, open_schwab_order_id,
         entry_ts.isoformat() if entry_ts else None, position_id),
    )
    conn.commit()


def update_real_condor_open_order(
    conn: sqlite3.Connection, position_id: int, *, open_schwab_order_id: str | None, status: str
) -> None:
    """Actualiza el id de orden / estado de una apertura que sigue negociando (o murió sin llenar)."""
    conn.execute(
        "UPDATE real_condor_positions SET open_schwab_order_id = ?, status = ? WHERE id = ?",
        (open_schwab_order_id, status, position_id),
    )
    conn.commit()


def mark_real_condor_position(conn: sqlite3.Connection, position_id: int, marked_ts: datetime, unrealized_pnl: float) -> None:
    conn.execute(
        "UPDATE real_condor_positions SET last_marked_ts = ?, last_unrealized_pnl = ? WHERE id = ?",
        (marked_ts.isoformat(), unrealized_pnl, position_id),
    )
    conn.commit()


def request_real_condor_manual_close(conn: sqlite3.Connection, position_id: int, requested_ts: datetime | None = None) -> None:
    """El usuario pidió cerrar ESTE condor real YA, aunque no haya llegado al objetivo de ganancia
    (usuario 2026-08-14). Solo deja la bandera: la orden la manda el scheduler en el próximo tick, con la
    misma escalera de precio que el cierre automático (nunca cruza el mid). Deliberadamente NO manda la
    orden desde el dashboard — mirar una página nunca debe operar en tu cuenta.
    Solo aplica a posiciones vivas; una ya cerrada no se toca."""
    conn.execute(
        "UPDATE real_condor_positions SET manual_close_requested = ? WHERE id = ? AND status IN ('working', 'open')",
        ((requested_ts or datetime.now()).isoformat(), position_id),
    )
    conn.commit()


def cancel_real_condor_manual_close(conn: sqlite3.Connection, position_id: int) -> None:
    """Se arrepintió antes de que el scheduler alcance a cerrarla: vuelve a la gestión automática."""
    conn.execute(
        "UPDATE real_condor_positions SET manual_close_requested = NULL WHERE id = ?", (position_id,)
    )
    conn.commit()


def is_real_condor_manual_close_requested(conn: sqlite3.Connection, position_id: int) -> bool:
    row = conn.execute(
        "SELECT manual_close_requested FROM real_condor_positions WHERE id = ?", (position_id,)
    ).fetchone()
    return bool(row and row["manual_close_requested"])


def close_real_condor_position(
    conn: sqlite3.Connection, position_id: int, close_date: date, close_value: float | None, close_reason: str,
    realized_pnl: float | None, close_ts: datetime | None = None, close_schwab_order_id: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE real_condor_positions
        SET status = 'closed', close_date = ?, close_value = ?, close_reason = ?, realized_pnl = ?,
            last_unrealized_pnl = ?, close_ts = ?, close_schwab_order_id = ?
        WHERE id = ?
        """,
        (close_date.isoformat(), close_value, close_reason, realized_pnl, realized_pnl,
         close_ts.isoformat() if close_ts else None, close_schwab_order_id, position_id),
    )
    conn.commit()


def get_real_condor_performance_stats(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """Resumen del Iron Condor REAL para el dashboard: abiertos, cerrados, win rate, P&L total y la
    ganancia realizada de HOY (mismo formato que el paper get_condor_performance_stats)."""
    closed = conn.execute("SELECT realized_pnl FROM real_condor_positions WHERE status = 'closed'").fetchall()
    pnls = [r["realized_pnl"] for r in closed if r["realized_pnl"] is not None]
    open_rows = conn.execute(
        "SELECT last_unrealized_pnl FROM real_condor_positions WHERE status IN ('working', 'open')"
    ).fetchall()
    unrealized = sum((r["last_unrealized_pnl"] or 0.0) for r in open_rows)
    wins = [p for p in pnls if p > 0]
    _day = (today or date.today()).isoformat()
    today_rows = conn.execute(
        "SELECT realized_pnl FROM real_condor_positions WHERE status = 'closed' "
        "AND realized_pnl IS NOT NULL AND substr(close_ts, 1, 10) = ?", (_day,)
    ).fetchall()
    today_pnls = [r["realized_pnl"] for r in today_rows]
    return {
        "open_count": len(open_rows),
        "closed_count": len(pnls),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "total_realized_pnl": round(sum(pnls), 2) if pnls else 0.0,
        "open_unrealized_pnl": round(unrealized, 2),
        "realized_pnl_today": round(sum(today_pnls), 2) if today_pnls else 0.0,
        "closed_count_today": len(today_pnls),
    }


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


def insert_robot_decision(
    conn: sqlite3.Connection,
    decision_date: date,
    symbol: str,
    action: str,
    reason: str,
    context_json: str,
    created_at: datetime,
    position_id: int | None = None,
) -> int:
    """Registra una decisión del robot (abrir/saltear/cerrar) con su contexto — historial para
    la capa de aprendizaje y para la pestaña Robot del dashboard. `position_id` enlaza una
    apertura con la posición que abrió, para después cruzar la decisión con su resultado real."""
    cur = conn.execute(
        """
        INSERT INTO robot_decisions (decision_date, symbol, action, reason, context_json, created_at, position_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (decision_date.isoformat(), symbol, action, reason, context_json, created_at.isoformat(), position_id),
    )
    conn.commit()
    return cur.lastrowid


def set_decision_feedback(conn: sqlite3.Connection, decision_id: int, feedback: str | None) -> None:
    """Guarda tu 👍/👎 sobre una decisión ('good' | 'bad' | None para limpiar). Señal clave para
    que el aprendizaje aprenda TU criterio (Etapa 1, usuario 2026-08). Sella `feedback_at` con el
    momento en que puntuaste (para la pestaña Puntuadas: filtrar por día/semana); al limpiar el
    voto (None) también se borra la fecha."""
    stamp = datetime.now().isoformat() if feedback else None
    conn.execute(
        "UPDATE robot_decisions SET user_feedback = ?, feedback_at = ? WHERE id = ?",
        (feedback, stamp, decision_id),
    )
    conn.commit()


def get_rated_decisions(conn: sqlite3.Connection, since_iso: str | None = None) -> list[sqlite3.Row]:
    """Operaciones que YA puntuaste (👍/👎), con el resultado de su posición si la tiene, ordenadas
    por cuándo las puntuaste (más nuevas primero). `since_iso` filtra por feedback_at >= ese
    timestamp ISO (para 'hoy' / 'esta semana'). Sirve para la pestaña Puntuadas del dashboard —
    donde van a parar las que sacaste de la lista de pendientes (usuario 2026-08-05)."""
    base = (
        """
        SELECT d.id, d.symbol, d.action, d.decision_date, d.context_json, d.user_feedback,
               d.user_note, d.feedback_at,
               p.status AS position_status, p.realized_pnl, p.close_reason, p.strike,
               p.entry_premium, p.quantity, p.close_date
        FROM robot_decisions d
        LEFT JOIN simulated_positions p ON p.id = d.position_id
        WHERE d.user_feedback IS NOT NULL
          AND (d.context_json IS NULL OR d.context_json NOT LIKE '%iron_butterfly%')
        """
    )
    params: tuple = ()
    if since_iso:
        base += " AND d.feedback_at IS NOT NULL AND d.feedback_at >= ?"
        params = (since_iso,)
    base += " ORDER BY d.feedback_at DESC, d.id DESC"
    return conn.execute(base, params).fetchall()


def set_decision_note(conn: sqlite3.Connection, decision_id: int, note: str | None) -> None:
    """Guarda tu nota libre sobre una decisión — para enseñarle con tus palabras por qué te gustó
    o no (usuario 2026-08). Se guarda como parte del historial para la capa de IA."""
    conn.execute("UPDATE robot_decisions SET user_note = ? WHERE id = ?", (note or None, decision_id))
    conn.commit()


def set_decision_param_feedback(conn: sqlite3.Connection, decision_id: int, votes: dict) -> None:
    """Guarda tu voto POR PARÁMETRO de una decisión (usuario 2026-08-06: "votar cada casillero
    bien/normal/mal"). `votes` = {param_key: 'good'|'normal'|'bad'}; las claves sin voto se omiten.
    El aprendizaje cruza estos votos con los pesos del cerebro (learning.review_param_feedback)."""
    clean = {k: v for k, v in (votes or {}).items() if v in ("good", "normal", "bad")}
    payload = json.dumps(clean) if clean else None
    conn.execute("UPDATE robot_decisions SET param_feedback_json = ? WHERE id = ?", (payload, decision_id))
    conn.commit()


def get_decision_param_feedback(conn: sqlite3.Connection, decision_id: int) -> dict:
    """Lee el voto por parámetro guardado de una decisión, como dict (vacío si no hay)."""
    row = conn.execute(
        "SELECT param_feedback_json FROM robot_decisions WHERE id = ?", (decision_id,)
    ).fetchone()
    if not row or not row["param_feedback_json"]:
        return {}
    try:
        return json.loads(row["param_feedback_json"])
    except (ValueError, TypeError):
        return {}


def get_decisions_with_param_feedback(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Todas las decisiones que tienen algún voto por parámetro, con su contexto — para que el
    aprendizaje agregue los votos y proponga ajustes de peso (usuario 2026-08-06)."""
    return conn.execute(
        """
        SELECT id, symbol, context_json, param_feedback_json
        FROM robot_decisions
        WHERE param_feedback_json IS NOT NULL AND param_feedback_json != ''
        """
    ).fetchall()


def update_decision_context(conn: sqlite3.Connection, decision_id: int, context_json: str) -> None:
    """Reescribe el contexto (JSON) de una decisión — usado por el enriquecimiento para completar
    los datos de mercado (precio, bid/ask, OI, volumen, RSI, HV…) de posiciones abiertas por el
    código viejo, que no los capturó al abrir (usuario 2026-08-05: 'todos los campos deben tener
    información')."""
    conn.execute("UPDATE robot_decisions SET context_json = ? WHERE id = ?", (context_json, decision_id))
    conn.commit()


def set_decision_position_id(conn: sqlite3.Connection, decision_id: int, position_id: int) -> None:
    """Enlaza (a posteriori) una decisión de apertura con su posición, cuando el código viejo la
    guardó sin position_id. Permite cruzar decisión↔resultado real con precisión."""
    conn.execute("UPDATE robot_decisions SET position_id = ? WHERE id = ?", (position_id, decision_id))
    conn.commit()


def get_intraday_open_decision(conn: sqlite3.Connection, strategy: str, position_id: int,
                               book: str = "paper") -> sqlite3.Row | None:
    """La decisión de apertura de una posición intradía (iron_butterfly / iron_condor), enlazada por
    el position_id guardado en su contexto — para poder puntuarla (👍/👎 + nota) desde el dashboard
    igual que los puts (usuario 2026-08-05).

    `book` distingue papel de real: las dos tablas de condor llevan ids independientes, así que sin
    este filtro el condor de papel #7 podía traer la decisión del condor REAL #7 (y mostrarte los
    datos de otra operación al puntuar). Las decisiones viejas, de antes de que se guardara `book`,
    se tratan como de papel — era lo único que existía."""
    return conn.execute(
        """
        SELECT * FROM robot_decisions
        WHERE action = 'open'
          AND json_extract(context_json, '$.strategy') = ?
          AND CAST(json_extract(context_json, '$.position_id') AS INTEGER) = ?
          AND COALESCE(json_extract(context_json, '$.book'), 'paper') = ?
        ORDER BY id DESC LIMIT 1
        """,
        (strategy, position_id, book),
    ).fetchone()


def get_latest_open_decision_for_symbol(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row | None:
    """La apertura de put más reciente registrada para `symbol` (excluye el Iron Butterfly). Se usa
    para enlazar/enriquecer la decisión de una posición abierta cuando falta el position_id."""
    return conn.execute(
        """
        SELECT * FROM robot_decisions
        WHERE symbol = ? AND action = 'open'
          AND (context_json IS NULL OR context_json NOT LIKE '%iron_butterfly%')
        ORDER BY id DESC LIMIT 1
        """,
        (symbol,),
    ).fetchone()


# --- Capa de aprendizaje (Etapa 2/3) ---

def set_learning_value(conn: sqlite3.Connection, key: str, value: float) -> None:
    conn.execute(
        """
        INSERT INTO learning_state (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, datetime.now().isoformat()),
    )
    conn.commit()


def get_learning_state(conn: sqlite3.Connection) -> dict[str, float]:
    """Todos los ajustes aprendidos vigentes, como dict key->value."""
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM learning_state")}


def insert_learning_proposal(
    conn: sqlite3.Connection, param: str, current_value: float | None, proposed_value: float | None, rationale: str
) -> int:
    cur = conn.execute(
        """
        INSERT INTO learning_proposals (created_at, param, current_value, proposed_value, rationale, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
        """,
        (datetime.now().isoformat(), param, current_value, proposed_value, rationale),
    )
    conn.commit()
    return cur.lastrowid


def get_pending_learning_proposals(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM learning_proposals WHERE status = 'pending' ORDER BY id DESC").fetchall()


def get_proposal(conn: sqlite3.Connection, proposal_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM learning_proposals WHERE id = ?", (proposal_id,)).fetchone()


def resolve_learning_proposal(conn: sqlite3.Connection, proposal_id: int, status: str) -> None:
    """Marca una propuesta como 'approved' o 'rejected' (la aplicación del cambio la hace el
    llamador si es approved, escribiendo en learning_state)."""
    conn.execute(
        "UPDATE learning_proposals SET status = ?, decided_at = ? WHERE id = ?",
        (status, datetime.now().isoformat(), proposal_id),
    )
    conn.commit()


def has_pending_proposal_for(conn: sqlite3.Connection, param: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM learning_proposals WHERE param = ? AND status = 'pending' LIMIT 1", (param,)
    ).fetchone()
    return row is not None


def insert_learning_report(conn: sqlite3.Connection, examples_used: int, summary: str, detail_json: str) -> int:
    cur = conn.execute(
        "INSERT INTO learning_reports (created_at, examples_used, summary, detail_json) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), examples_used, summary, detail_json),
    )
    conn.commit()
    return cur.lastrowid


def get_latest_learning_report(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM learning_reports ORDER BY id DESC LIMIT 1").fetchone()


def get_learning_examples(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Dataset para el motor de aprendizaje: cada APERTURA de put con su contexto (features), tu
    feedback, y el RESULTADO real de la posición que abrió (realized_pnl y estado) enlazado por
    position_id. Excluye el Iron Butterfly (se aprende aparte)."""
    return conn.execute(
        """
        SELECT d.id, d.symbol, d.decision_date, d.context_json, d.user_feedback,
               p.status AS position_status, p.realized_pnl, p.close_reason,
               p.entry_premium, p.quantity, p.collateral
        FROM robot_decisions d
        LEFT JOIN simulated_positions p ON p.id = d.position_id
        WHERE d.action = 'open'
          AND (d.context_json IS NULL OR d.context_json NOT LIKE '%iron_butterfly%')
        ORDER BY d.id DESC
        """
    ).fetchall()


def get_butterfly_learning_examples(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Dataset del aprendizaje del Iron Butterfly: cada APERTURA (con su distancia a la SMA y demás
    features en el contexto), tu feedback, y el resultado de la posición del butterfly que abrió
    (enlazada por el position_id guardado en el contexto). Usa json_extract (JSON1 de SQLite)."""
    return conn.execute(
        """
        SELECT d.id, d.context_json, d.user_feedback,
               b.status AS position_status, b.realized_pnl, b.close_reason
        FROM robot_decisions d
        LEFT JOIN butterfly_positions b
               ON b.id = CAST(json_extract(d.context_json, '$.position_id') AS INTEGER)
        WHERE d.action = 'open' AND d.context_json LIKE '%iron_butterfly%'
        ORDER BY d.id DESC
        """
    ).fetchall()


def get_condor_learning_examples(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Dataset del aprendizaje del Iron Condor (usuario 2026-08-14): cada APERTURA con sus features
    en el contexto (delta real de los cortos, rango del día, crédito, VIX), tu feedback, y el
    resultado de la posición que abrió.

    Toma PAPEL Y REAL juntos, como pidió el usuario. El contexto guarda `position_id` y `book`
    ('paper' o 'real') para saber contra qué tabla enlazar — las dos tienen ids independientes, así
    que sin el `book` un id 7 de papel se confundiría con un id 7 real. Las aperturas viejas, de
    antes de que se guardara `book`, se tratan como de papel (era lo único que existía)."""
    return conn.execute(
        """
        SELECT d.id, d.context_json, d.user_feedback,
               COALESCE(p.status, r.status)             AS position_status,
               COALESCE(p.realized_pnl, r.realized_pnl) AS realized_pnl,
               COALESCE(p.close_reason, r.close_reason) AS close_reason
        FROM robot_decisions d
        LEFT JOIN iron_condor_positions p
               ON COALESCE(json_extract(d.context_json, '$.book'), 'paper') = 'paper'
              AND p.id = CAST(json_extract(d.context_json, '$.position_id') AS INTEGER)
        LEFT JOIN real_condor_positions r
               ON json_extract(d.context_json, '$.book') = 'real'
              AND r.id = CAST(json_extract(d.context_json, '$.position_id') AS INTEGER)
        WHERE d.action = 'open'
          AND json_extract(d.context_json, '$.strategy') = 'iron_condor'
        ORDER BY d.id DESC
        """
    ).fetchall()


def get_robot_decisions(conn: sqlite3.Connection, limit: int = 200, action: str | None = None) -> list[sqlite3.Row]:
    if action:
        return conn.execute(
            "SELECT * FROM robot_decisions WHERE action = ? ORDER BY id DESC LIMIT ?", (action, limit)
        ).fetchall()
    return conn.execute("SELECT * FROM robot_decisions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def get_robot_decision_stats(conn: sqlite3.Connection) -> dict:
    """Resumen operativo del robot para la pestaña Robot: cuántas rondas (días distintos)
    evaluó, cuántas oportunidades abrió/cerró/rechazó, y la última decisión. Todo en 0/None
    cuando el robot todavía no registró nada."""
    rows = conn.execute("SELECT action, decision_date, reason FROM robot_decisions ORDER BY id DESC").fetchall()
    opened = sum(1 for r in rows if r["action"] == "open")
    closed = sum(1 for r in rows if r["action"] == "close")
    skipped = sum(1 for r in rows if r["action"] in ("skip", "skip_risk"))
    rounds = len({r["decision_date"] for r in rows})
    last = rows[0] if rows else None
    return {
        "total": len(rows),
        "opened": opened,
        "closed": closed,
        "skipped": skipped,
        "rounds": rounds,
        "last_date": last["decision_date"] if last else None,
        "last_action": last["action"] if last else None,
        "last_reason": last["reason"] if last else None,
    }


# ============================ Asesor AI (usuario 2026-08-10) ============================
# Preferencias aprendidas (auditable) + sugerencias del asesor que el usuario aprueba a mano.

def add_ai_preference(conn: sqlite3.Connection, text: str, *, source: str = "chat") -> int:
    """Guarda una preferencia que la IA aprendió del usuario (ej: 'no me gusta COIN con VIX alto').
    Devuelve el id. No duplica: si ya existe una preferencia activa idéntica (case-insensitive), la reusa."""
    text = (text or "").strip()
    if not text:
        return 0
    existing = conn.execute(
        "SELECT id FROM ai_preferences WHERE active = 1 AND lower(text) = lower(?)", (text,)
    ).fetchone()
    if existing:
        return int(existing[0])
    cur = conn.execute(
        "INSERT INTO ai_preferences (created_at, text, source, active) VALUES (?, ?, ?, 1)",
        (datetime.now().isoformat(), text, source),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_ai_preferences(conn: sqlite3.Connection, *, active_only: bool = True) -> list[sqlite3.Row]:
    q = "SELECT * FROM ai_preferences"
    if active_only:
        q += " WHERE active = 1"
    q += " ORDER BY id DESC"
    return conn.execute(q).fetchall()


def delete_ai_preference(conn: sqlite3.Connection, pref_id: int) -> None:
    """Baja lógica (active=0): la preferencia deja de aplicarse pero queda el registro."""
    conn.execute("UPDATE ai_preferences SET active = 0 WHERE id = ?", (int(pref_id),))
    conn.commit()


def add_ai_suggestion(
    conn: sqlite3.Connection, *, symbol: str, strike: float, expiration: str, contracts: int,
    target_credit: float | None, rationale: str | None, option_type: str = "put", action: str = "open",
    target: str = "real", sim_kind: str | None = None, position_id: int | None = None,
    min_price: float | None = None,
) -> int:
    """Registra una sugerencia concreta del asesor en estado 'pending' (todavía sin aprobar). `action`:
    'open' = vender un put nuevo; 'close' = recomprar/cerrar una posición abierta. `target`: 'real' (cuenta
    Schwab) o 'simulador' (paper). `sim_kind` ('put'/'condor') si es del simulador (usuario 2026-08-11).
    `min_price`: piso DURO de precio pedido por el usuario al vender (ej. 'no bajes de 3.00') — la orden
    nunca se coloca/re-precia por debajo de esto (usuario 2026-08-11)."""
    cur = conn.execute(
        "INSERT INTO ai_suggested_orders (created_at, action, target, sim_kind, position_id, symbol, "
        "option_type, strike, expiration, contracts, target_credit, min_price, rationale, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
        (datetime.now().isoformat(), action, target, sim_kind, position_id, symbol.strip().upper(),
         option_type, float(strike), expiration, int(contracts), target_credit, min_price, rationale),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_ai_suggestion(conn: sqlite3.Connection, sug_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM ai_suggested_orders WHERE id = ?", (int(sug_id),)).fetchone()


def approve_ai_suggestion(conn: sqlite3.Connection, sug_id: int) -> None:
    """El usuario aprobó la sugerencia: pasa a 'approved'. El robot la levanta en el próximo escaneo."""
    conn.execute(
        "UPDATE ai_suggested_orders SET status = 'approved', approved_at = ? WHERE id = ? AND status = 'pending'",
        (datetime.now().isoformat(), int(sug_id)),
    )
    conn.commit()


def reject_ai_suggestion(conn: sqlite3.Connection, sug_id: int) -> None:
    conn.execute(
        "UPDATE ai_suggested_orders SET status = 'rejected', resolved_at = ? WHERE id = ?",
        (datetime.now().isoformat(), int(sug_id)),
    )
    conn.commit()


def get_approved_ai_suggestions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Sugerencias aprobadas por el usuario, listas para que el robot las mande por el guardián real."""
    return conn.execute(
        "SELECT * FROM ai_suggested_orders WHERE status = 'approved' ORDER BY approved_at"
    ).fetchall()


def resolve_ai_suggestion(
    conn: sqlite3.Connection, sug_id: int, *, status: str, live_order_log_id: int | None = None,
    result_note: str | None = None,
) -> None:
    """Marca el resultado final de una sugerencia aprobada tras pasar por el robot (sent / rejected / error)."""
    conn.execute(
        "UPDATE ai_suggested_orders SET status = ?, resolved_at = ?, live_order_log_id = ?, result_note = ? "
        "WHERE id = ?",
        (status, datetime.now().isoformat(), live_order_log_id, result_note, int(sug_id)),
    )
    conn.commit()


def list_ai_suggestions(conn: sqlite3.Connection, *, limit: int = 30) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ai_suggested_orders ORDER BY id DESC LIMIT ?", (int(limit),)
    ).fetchall()
