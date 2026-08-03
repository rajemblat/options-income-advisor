-- Snapshot diario de indicadores calculados por símbolo (Sección 5 del plan de Fase 1)
CREATE TABLE IF NOT EXISTS indicator_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    snapshot_ts TEXT NOT NULL,
    price REAL NOT NULL,
    iv_atm REAL,
    iv_rank REAL,
    iv_rank_source TEXT NOT NULL,
    hv_20d REAL,
    atr_14 REAL,
    rsi_14 REAL,
    sma_8 REAL,
    sma_20 REAL,
    sma_50 REAL,
    sma_200 REAL,
    ma_cross_signal TEXT,
    support_levels TEXT,
    resistance_levels TEXT,
    raw_indicators_json TEXT,
    next_earnings_date TEXT,
    price_std_20 REAL,
    net_gex REAL,
    next_ex_dividend_date TEXT,
    UNIQUE(symbol, snapshot_date)
);

-- Contexto macro, una fila por día (no es por símbolo): tasa de la Fed vigente, indicadores
-- FRED más recientes, y probabilidad de la próxima decisión de tasas a partir de precios
-- reales de mercado (Kalshi) — nunca una especulación del narrador de IA.
CREATE TABLE IF NOT EXISTS macro_snapshot (
    snapshot_date TEXT PRIMARY KEY,
    fed_funds_lower REAL,
    fed_funds_upper REAL,
    cpi_yoy_pct REAL,
    cpi_yoy_date TEXT,
    unemployment_rate_pct REAL,
    gdp_growth_annualized_pct REAL,
    fed_meeting_date TEXT,
    fed_hike_probability REAL,
    fed_hold_probability REAL,
    fed_cut_probability REAL,
    upcoming_events_json TEXT
);

-- Historial dedicado de IV, usado para el bootstrap de IV Rank (Sección 4 del plan)
CREATE TABLE IF NOT EXISTS iv_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    iv_atm REAL NOT NULL,
    source TEXT NOT NULL,
    UNIQUE(symbol, snapshot_date)
);

-- Contratos/candidatos de estrategia evaluados
CREATE TABLE IF NOT EXISTS candidate_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    strategy_type TEXT NOT NULL,
    expiration_date TEXT NOT NULL,
    strikes_json TEXT NOT NULL,
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    rho REAL,
    greeks_source TEXT NOT NULL,
    conviction_score INTEGER NOT NULL,
    scoring_breakdown_json TEXT NOT NULL,
    legs_json TEXT,
    net_premium REAL,
    max_profit REAL,
    max_loss REAL,
    breakevens_json TEXT,
    probability_of_profit REAL,
    dte INTEGER,
    underlying_price REAL,
    payoff_is_estimate INTEGER,
    annualized_return_pct REAL,
    early_close_projection_json TEXT,
    historical_move_occurrences INTEGER,
    historical_move_total_windows INTEGER,
    similar_move_occurrences INTEGER,
    similar_move_bigger_occurrences INTEGER
);

-- Noticias recientes por símbolo (Finnhub /company-news). UNIQUE(symbol, url) para poder
-- refrescar en cada corrida del job sin acumular duplicados cuando el mismo artículo sigue
-- apareciendo en el rango de lookback.
CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    published_at TEXT,
    headline TEXT NOT NULL,
    source TEXT,
    url TEXT NOT NULL,
    summary TEXT,
    fetched_date TEXT NOT NULL,
    UNIQUE(symbol, url)
);

-- Historial de alertas generadas (notificadas o descartadas por umbral)
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    alert_date TEXT NOT NULL,
    alert_ts TEXT NOT NULL,
    candidate_contract_id INTEGER,
    conviction_score INTEGER NOT NULL,
    risk_profile TEXT NOT NULL,
    threshold_applied INTEGER NOT NULL,
    was_notified INTEGER NOT NULL,
    narrative_text TEXT,
    narrative_source TEXT,
    dedup_key TEXT NOT NULL,
    UNIQUE(dedup_key),
    FOREIGN KEY (candidate_contract_id) REFERENCES candidate_contracts(id)
);

-- Notificaciones internas del dashboard (campanita 🔔) — hoy solo las llena el digest
-- pre-apertura (scheduler/jobs.py::job_premarket_digest), pensado genérico para sumar otros
-- `kind` más adelante sin cambiar el esquema.
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0
);

-- Perfil de inversor (fila única, herramienta de un solo usuario)
CREATE TABLE IF NOT EXISTS investor_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    capital_available REAL NOT NULL,
    loss_tolerance_pct REAL NOT NULL,
    experience_level TEXT NOT NULL,
    risk_preference TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    conviction_threshold_override INTEGER,
    updated_at TEXT NOT NULL
);

-- Posiciones asignadas (para detectar candidatos a Covered Call tras un Cash-Secured Put asignado)
CREATE TABLE IF NOT EXISTS assigned_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    shares INTEGER NOT NULL,
    cost_basis REAL NOT NULL,
    assigned_date TEXT NOT NULL,
    origin_alert_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open',
    FOREIGN KEY (origin_alert_id) REFERENCES alerts(id)
);

-- Operaciones reales de venta de opciones detectadas en la cuenta Schwab (Sección 'Operaciones'
-- — réplica automática de operaciones reales, pedido 2026-07-25; rediseñado 2026-07-28 para
-- detectar vía /orders en vez de diffear posiciones, ver alerts/real_trades.py) — tabla separada
-- de candidate_contracts/alerts a propósito: esas representan sugerencias no ejecutadas (con
-- score/threshold/perfil), esto es lo que el usuario YA hizo, sin nada que puntuar.
CREATE TABLE IF NOT EXISTS real_trade_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_number TEXT NOT NULL,
    occ_symbol TEXT NOT NULL,
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    trade_ts TEXT NOT NULL,
    strategy_type TEXT NOT NULL,
    option_type TEXT NOT NULL,
    strike REAL NOT NULL,
    expiration_date TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price REAL,
    order_id INTEGER,
    legs_json TEXT,
    net_premium REAL,
    max_profit REAL,
    max_loss REAL,
    breakevens_json TEXT,
    probability_of_profit REAL,
    dte INTEGER,
    underlying_price REAL,
    payoff_is_estimate INTEGER,
    annualized_return_pct REAL,
    early_close_projection_json TEXT,
    historical_move_occurrences INTEGER,
    historical_move_total_windows INTEGER,
    similar_move_occurrences INTEGER,
    similar_move_bigger_occurrences INTEGER,
    narrative_text TEXT,
    narrative_source TEXT,
    -- NULL = apertura normal (todo el comportamiento de siempre) | 'roll_closed' = pata que se
    -- CERRÓ como parte de un roll (registro liviano, sin P&L propio) | 'roll_opened' = pata
    -- NUEVA que reemplazó a la cerrada (cálculo completo, igual que una apertura normal) —
    -- pedido 2026-07-30. Ambas filas de un mismo roll comparten `order_id`, cada una con su
    -- propio `occ_symbol` (strike/vencimiento distintos), así que el índice UNIQUE existente
    -- (order_id, occ_symbol) no choca entre ellas.
    leg_role TEXT
);

-- Simulador de Trading Automático (paper trading, pedido 2026-08-02): cuenta simulada de
-- $100,000 en datos REALES de mercado, fila única (mismo patrón que investor_profile).
CREATE TABLE IF NOT EXISTS simulated_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,
    created_at TEXT NOT NULL
);

-- Posiciones Naked Put simuladas (único alcance inicial) — abiertas por
-- simulator/engine.py::process_symbol_entry cuando los 8 criterios de entrada
-- (simulator/entry_rules.py) pasan, cerradas por simulator/positions.py::mark_position al
-- llegar al 30% de ganancia sobre la prima cobrada o al vencimiento.
CREATE TABLE IF NOT EXISTS simulated_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    strategy_type TEXT NOT NULL DEFAULT 'cash_secured_put',
    strike REAL NOT NULL,
    expiration_date TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_date TEXT NOT NULL,
    entry_premium REAL NOT NULL,
    collateral REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    close_date TEXT,
    close_premium REAL,
    close_reason TEXT,
    realized_pnl REAL,
    last_marked_date TEXT,
    last_unrealized_pnl REAL
);
CREATE INDEX IF NOT EXISTS idx_simulated_positions_status ON simulated_positions(status);
CREATE INDEX IF NOT EXISTS idx_simulated_positions_symbol ON simulated_positions(symbol);

-- Curva de equity diaria de la cuenta simulada (Dashboard: Simulador — P&L acumulado, %
-- rendimiento sobre el capital inicial).
CREATE TABLE IF NOT EXISTS simulated_equity_history (
    snapshot_date TEXT PRIMARY KEY,
    cash REAL NOT NULL,
    collateral_committed REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    equity REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_real_trade_alerts_symbol_date ON real_trade_alerts(symbol, trade_date);
CREATE INDEX IF NOT EXISTS idx_iv_snapshots_symbol_date ON iv_snapshots(symbol, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_indicator_snapshots_symbol_date ON indicator_snapshots(symbol, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_alerts_symbol_date ON alerts(symbol, alert_date);
CREATE INDEX IF NOT EXISTS idx_news_items_symbol_published ON news_items(symbol, published_at);
CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON notifications(is_read);
