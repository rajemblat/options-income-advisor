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
    payoff_is_estimate INTEGER
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

CREATE INDEX IF NOT EXISTS idx_iv_snapshots_symbol_date ON iv_snapshots(symbol, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_indicator_snapshots_symbol_date ON indicator_snapshots(symbol, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_alerts_symbol_date ON alerts(symbol, alert_date);
CREATE INDEX IF NOT EXISTS idx_news_items_symbol_published ON news_items(symbol, published_at);
CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON notifications(is_read);
