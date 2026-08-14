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
    leg_role TEXT,
    -- Griegas NETAS de la posición combinada (usuario 2026-08-12: detalle completo tipo OptionStrat).
    net_delta REAL,
    net_gamma REAL,
    net_theta REAL,
    net_vega REAL,
    net_rho REAL,
    greeks_source TEXT
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
    last_unrealized_pnl REAL,
    entry_ts TEXT,   -- momento EXACTO de apertura (hora:min:seg) para la vista estilo broker
    close_ts TEXT    -- momento EXACTO de cierre
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

-- Estrategia 2 (Iron Butterfly 0DTE intradía, 2026-08): posición multi-pata guardada como UNA
-- fila. El cuerpo (put+call vendidos) va en `body_strike`; las alas compradas en
-- `long_put_strike`/`long_call_strike`. `entry_net_credit` y `max_loss` en dólares. Se marca a
-- mercado minuto a minuto y se cierra a +profit_target / -stop_loss / vencimiento (mismo día).
CREATE TABLE IF NOT EXISTS iron_condor_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_ts TEXT,
    expiration_date TEXT NOT NULL,
    short_put_strike REAL NOT NULL,
    short_call_strike REAL NOT NULL,
    long_put_strike REAL NOT NULL,
    long_call_strike REAL NOT NULL,
    entry_net_credit REAL NOT NULL,     -- crédito recibido al abrir, en dólares
    max_loss REAL NOT NULL,
    max_profit REAL NOT NULL,
    lower_breakeven REAL,
    upper_breakeven REAL,
    entry_spot REAL,
    status TEXT NOT NULL DEFAULT 'open',
    last_marked_ts TEXT,
    last_unrealized_pnl REAL,
    close_date TEXT,
    close_ts TEXT,
    close_value REAL,
    close_reason TEXT,
    realized_pnl REAL
);


CREATE TABLE IF NOT EXISTS butterfly_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying TEXT NOT NULL,
    direction TEXT NOT NULL,            -- 'revert_down' | 'revert_up'
    entry_date TEXT NOT NULL,
    entry_ts TEXT,
    expiration_date TEXT NOT NULL,
    body_strike REAL NOT NULL,
    long_put_strike REAL NOT NULL,
    long_call_strike REAL NOT NULL,
    entry_net_credit REAL NOT NULL,     -- crédito recibido al abrir, en dólares
    max_loss REAL NOT NULL,             -- pérdida máxima (colateral), en dólares
    max_profit REAL NOT NULL,
    lower_breakeven REAL,
    upper_breakeven REAL,
    entry_spot REAL,
    status TEXT NOT NULL DEFAULT 'open',
    last_marked_ts TEXT,
    last_unrealized_pnl REAL,
    close_date TEXT,
    close_ts TEXT,
    close_value REAL,                   -- costo de recompra al cerrar, en dólares
    close_reason TEXT,
    realized_pnl REAL
);
CREATE INDEX IF NOT EXISTS idx_butterfly_positions_status ON butterfly_positions(status);


-- Iron Condor 0DTE con DINERO REAL en Schwab (usuario 2026-08-13: "el mismo cerebro del papel pero
-- real, mismo stop loss y mismo profit %"). Tabla SEPARADA del paper (iron_condor_positions) para que
-- el real lleve su propio conteo, su propio tope diario y su propio P&L. Guarda los símbolos OCC EXACTOS
-- de cada pata (críticos para recomprar/vender exactamente lo mismo al cerrar) + los ids de orden de
-- Schwab. status: 'working' (orden combinada puesta, sin llenar) → 'open' (llenó) → 'closed'.
CREATE TABLE IF NOT EXISTS real_condor_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_ts TEXT,
    expiration_date TEXT NOT NULL,
    short_put_strike REAL NOT NULL,
    short_call_strike REAL NOT NULL,
    long_put_strike REAL NOT NULL,
    long_call_strike REAL NOT NULL,
    short_put_symbol TEXT NOT NULL,     -- símbolo OCC EXACTO de la cadena (no se reconstruye)
    long_put_symbol TEXT NOT NULL,
    short_call_symbol TEXT NOT NULL,
    long_call_symbol TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    entry_credit_ps REAL,               -- crédito por-acción al que llenó (ej. 1.85); NULL mientras 'working'
    entry_net_credit REAL NOT NULL,     -- crédito TOTAL en dólares (fill_ps × 100 × cantidad); base del profit/stop
    max_loss REAL NOT NULL,
    max_profit REAL NOT NULL,
    lower_breakeven REAL,
    upper_breakeven REAL,
    entry_spot REAL,
    open_schwab_order_id TEXT,
    status TEXT NOT NULL DEFAULT 'working',   -- 'working' | 'open' | 'closed'
    last_marked_ts TEXT,
    last_unrealized_pnl REAL,
    close_date TEXT,
    close_ts TEXT,
    close_value REAL,                   -- costo de cerrar (débito), en dólares
    close_reason TEXT,
    realized_pnl REAL,
    close_schwab_order_id TEXT,
    -- Cierre manual pedido desde el dashboard (usuario 2026-08-14: "un botón de dar la orden de vender la
    -- operación aunque no esté en la ganancia que marque"). Timestamp ISO de cuándo se pidió; NULL = no
    -- pedido. El dashboard SOLO escribe esta bandera: quien manda la orden sigue siendo el scheduler, en
    -- el próximo tick, con la MISMA escalera de precio (nunca cruza el mid) que el cierre automático.
    manual_close_requested TEXT
);
CREATE INDEX IF NOT EXISTS idx_real_condor_positions_status ON real_condor_positions(status);

-- Estado del robot compartido entre procesos (dashboard y scheduler corren separados): banderas
-- key/value como la pausa de la venta de puts (usuario 2026-08). El dashboard escribe, el
-- scheduler lee en cada corrida — por eso va en la base, no en memoria.
CREATE TABLE IF NOT EXISTS robot_flags (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT
);

-- Capa de aprendizaje (Etapa 2/3, usuario 2026-08). `learning_state` guarda los AJUSTES aprendidos
-- que el cerebro ya aplica (ej. override de un peso del scoring). El scheduler los lee al escanear.
CREATE TABLE IF NOT EXISTS learning_state (
    key TEXT PRIMARY KEY,        -- ej. 'weight.score_weight_coverage'
    value REAL NOT NULL,
    updated_at TEXT
);

-- Propuestas de cambio GRANDE que esperan tu aprobación (modo mixto: los chicos se aplican solos).
CREATE TABLE IF NOT EXISTS learning_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    param TEXT NOT NULL,         -- ej. 'score_weight_coverage'
    current_value REAL,
    proposed_value REAL,
    rationale TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'approved' | 'rejected'
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_learning_proposals_status ON learning_proposals(status);

-- Bitácora de cada revisión de aprendizaje: qué aprendió, en lenguaje simple + números.
CREATE TABLE IF NOT EXISTS learning_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    examples_used INTEGER,
    summary TEXT,
    detail_json TEXT
);

-- Log de decisiones del robot (2026-08-03): cada apertura/salteo/cierre con los datos que la
-- motivaron (volatilidad, cobertura pedida/obtenida, griegos incluido theta, IV, etc.) — el
-- historial que la capa de IA de la Fase 2 va a cruzar para aprender la mejor entrada/salida.
CREATE TABLE IF NOT EXISTS robot_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,        -- 'open' | 'skip' | 'skip_risk' | 'close' | 'watch'
    reason TEXT,
    context_json TEXT,
    created_at TEXT NOT NULL,
    user_feedback TEXT,          -- 'good' | 'bad' | NULL — tu 👍/👎 (Etapa 1 del aprendizaje, 2026-08)
    position_id INTEGER,         -- id de la posición abierta (para enlazar la decisión con su resultado)
    user_note TEXT,              -- tu nota libre para enseñarle ("por qué me gustó / no") (2026-08)
    feedback_at TEXT,            -- cuándo pusiste el 👍/👎 (para "Puntuadas": filtrar por día/semana, 2026-08-05)
    param_feedback_json TEXT     -- voto por parámetro {param_key:'good'|'normal'|'bad'} (2026-08-06)
);
CREATE INDEX IF NOT EXISTS idx_robot_decisions_date ON robot_decisions(decision_date);
CREATE INDEX IF NOT EXISTS idx_robot_decisions_symbol ON robot_decisions(symbol);

CREATE INDEX IF NOT EXISTS idx_real_trade_alerts_symbol_date ON real_trade_alerts(symbol, trade_date);
CREATE INDEX IF NOT EXISTS idx_iv_snapshots_symbol_date ON iv_snapshots(symbol, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_indicator_snapshots_symbol_date ON indicator_snapshots(symbol, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_alerts_symbol_date ON alerts(symbol, alert_date);
CREATE INDEX IF NOT EXISTS idx_news_items_symbol_published ON news_items(symbol, published_at);
CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON notifications(is_read);

-- Registro de órdenes REALES / DRY-RUN del trading real (usuario 2026-08-09). Cada vez que el robot,
-- con el día ARMADO, decide una orden sobre un símbolo de la whitelist real, deja acá lo que armó:
-- aprobada o rechazada por el guardián, con qué precio arrancaría y el payload exacto. En dry-run
-- `sent=0` (nunca se envió); en real `sent=1`. Es lo que el usuario revisa antes de pasar a real.
CREATE TABLE IF NOT EXISTS live_order_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date TEXT NOT NULL,          -- YYYY-MM-DD del día de la decisión
    log_ts TEXT NOT NULL,            -- timestamp ISO
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,            -- 'SELL_TO_OPEN' | 'BUY_TO_CLOSE'
    strike REAL,
    expiration TEXT,
    approved INTEGER NOT NULL,       -- 1 aprobada por el guardián, 0 rechazada
    final_contracts INTEGER,
    start_limit_price REAL,
    collateral REAL,                 -- margen comprometido (final_contracts x margen/contrato)
    dry_run INTEGER NOT NULL,        -- 1 = simulacro (NO enviada)
    sent INTEGER NOT NULL DEFAULT 0, -- 1 = enviada de verdad al broker
    reasons TEXT,                    -- motivos de rechazo / recortes del guardián
    payload_json TEXT,               -- el JSON exacto de la orden que se armó
    ladder_json TEXT,                -- la escalera de precios a caminar
    bid REAL,                        -- bid de la opción al momento (para ver la negociación vs mid)
    ask REAL,                        -- ask de la opción al momento
    user_feedback TEXT,              -- 👍 'good' / 👎 'bad' — tu puntuación de la orden real (2026-08-10)
    user_note TEXT,                  -- tu nota libre para enseñarle
    feedback_at TEXT,                -- cuándo la puntuaste
    -- Resultado del ENVÍO REAL (usuario 2026-08-10, "poner en real"): se completan al mandar la orden.
    schwab_order_id TEXT,            -- id de la orden en Schwab (el último, tras los reemplazos)
    order_status TEXT,               -- estado final: FILLED / CANCELED / REJECTED / EXPIRED / error
    fill_price REAL,                 -- prima promedio a la que LLENÓ (None si no llenó)
    filled_contracts INTEGER,        -- contratos efectivamente llenados
    final_limit_price REAL,          -- último precio límite caminado antes del fill/corte
    replacements INTEGER,            -- cuántas veces se reemplazó la orden caminando el precio
    sent_ts TEXT,                    -- cuándo se mandó de verdad
    send_error TEXT,                 -- detalle si algo falló en el envío/reconciliación
    -- Cierre REAL de la posición (usuario 2026-08-10: "que cierre con las mismas reglas del simulador").
    -- Se completan en la fila de la APERTURA (SELL_TO_OPEN llena) cuando el robot la recompra para cerrar.
    closed INTEGER DEFAULT 0,        -- 1 = ya cerrada (recomprada/vencida)
    close_ts TEXT,                   -- cuándo cerró
    close_fill_price REAL,           -- prima a la que recompró (o intrínseco al vencer)
    close_reason TEXT,               -- profit_target / stop_loss / dte_close / news_close / expired
    realized_pnl REAL,               -- P&L realizado de la operación (crédito − recompra) × 100 × contratos
    close_schwab_order_id TEXT,      -- id de la orden de cierre en Schwab
    open_context_json TEXT,          -- contexto EXACTO de la decisión al abrir (delta, POP, IV, % del día,
                                     -- cobertura, bid/ask…): para puntuar con el dato real de ESTA orden
    price_floor REAL,                -- piso DURO de precio al vender (usuario 2026-08-11: 'no bajes de 3.00'):
                                     -- ni la colocación ni el re-precio bajan de acá, aunque el mid caiga
    open_email_sent INTEGER DEFAULT 0 -- 1 = ya se mandó el email de APERTURA de esta orden (idempotente,
                                      -- usuario 2026-08-11: garantiza 1 email por fill sin importar el timing)
);
CREATE INDEX IF NOT EXISTS idx_live_order_log_date ON live_order_log(log_date);

-- ============================ Asesor AI (usuario 2026-08-10) ============================
-- Chat de recomendaciones + "aprendizaje" auditable. La IA charla con contexto en vivo (posiciones,
-- VIX, % del día de la watchlist), RECOMIENDA operaciones y APRENDE preferencias del usuario. Nada
-- se abre sin que el usuario apruebe con un botón (la orden aprobada pasa por el MISMO guardián real
-- START/kill/cupo/colateral). Dos tablas: preferencias aprendidas y sugerencias del asesor.

-- Preferencias que la IA fue aprendiendo de las charlas (ej: "no me gusta COIN con VIX alto",
-- "priorizá las caídas fuertes"). Auditable: el usuario las ve y puede borrarlas. `active=0` = borrada.
CREATE TABLE IF NOT EXISTS ai_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,         -- ISO timestamp de cuándo se aprendió
    text TEXT NOT NULL,               -- la preferencia en lenguaje natural
    source TEXT,                      -- 'chat' (la dijo el usuario) / 'manual' (la agregó a mano)
    active INTEGER NOT NULL DEFAULT 1 -- 1 = vigente (se aplica), 0 = borrada/archivada
);

-- Sugerencias concretas del asesor. La IA propone UNA operación; el usuario la aprueba o rechaza.
-- Solo las 'approved' las levanta el robot en el próximo escaneo y las manda por el guardián real.
CREATE TABLE IF NOT EXISTS ai_suggested_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'open',     -- 'open' (vender put nuevo) / 'close' (recomprar/cerrar una abierta)
    target TEXT NOT NULL DEFAULT 'real',     -- 'real' (cuenta Schwab) / 'simulador' (paper) — usuario 2026-08-11
    sim_kind TEXT,                           -- si target='simulador': 'put' | 'condor' (qué tipo cerrar)
    position_id INTEGER,                     -- ID exacto de la posición a cerrar (usuario 2026-08-11: sin confusión)
    symbol TEXT NOT NULL,
    option_type TEXT NOT NULL DEFAULT 'put',
    strike REAL NOT NULL,
    expiration TEXT NOT NULL,         -- YYYY-MM-DD
    contracts INTEGER NOT NULL DEFAULT 1,
    target_credit REAL,               -- prima objetivo estimada por la IA (informativa)
    min_price REAL,                   -- piso DURO de precio al vender pedido por el usuario ('no bajes de 3.00')
    rationale TEXT,                   -- por qué la recomienda (volatilidad, caída, riesgo…)
    status TEXT NOT NULL DEFAULT 'pending',  -- pending / approved / sent / rejected / expired / error
    approved_at TEXT,
    resolved_at TEXT,                 -- cuándo el robot la ejecutó/descartó
    live_order_log_id INTEGER,        -- fila de live_order_log si se llegó a mandar
    result_note TEXT                  -- detalle del resultado (fill, rechazo del guardián, error…)
);
CREATE INDEX IF NOT EXISTS idx_ai_suggested_status ON ai_suggested_orders(status);
