from __future__ import annotations

import logging.config
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RiskLevel = Literal["conservador", "moderado", "agresivo"]
ExperienceLevel = Literal["principiante", "intermedio", "avanzado"]
RiskPreference = Literal["defined", "undefined"]
BrokerMode = Literal["mock", "schwab"]


class BrokerSettings(BaseModel):
    mode: BrokerMode
    fixtures_dir: Path

    def resolved_fixtures_dir(self) -> Path:
        return PROJECT_ROOT / self.fixtures_dir


class DatabaseSettings(BaseModel):
    path: Path

    def resolved_path(self) -> Path:
        return PROJECT_ROOT / self.path


class MarketSettings(BaseModel):
    risk_free_rate: float


class LlmSettings(BaseModel):
    model: str
    max_tokens: int


class SchedulerSettings(BaseModel):
    timezone: str
    poll_interval_minutes: int
    real_trade_poll_interval_minutes: int
    market_open_snapshot_time: str
    market_close_snapshot_time: str
    market_hours_start: str
    market_hours_end: str
    premarket_digest_time: str
    # Cadencia del escaneo RÁPIDO solo-robot (indicadores + entrada + marcado, sin alertas/IA).
    # Es lo que hace que el robot opere solo, sin abrir el dashboard ni clickear (usuario 2026-08).
    robot_scan_interval_minutes: int = 5


class InvestorProfileSettings(BaseModel):
    capital_available: float
    loss_tolerance_pct: float
    experience_level: ExperienceLevel
    risk_preference: RiskPreference
    risk_level: RiskLevel


class ConvictionThresholds(BaseModel):
    conservador: int
    moderado: int
    agresivo: int

    def for_risk_level(self, risk_level: RiskLevel) -> int:
        return getattr(self, risk_level)


class RiskLevelFloatParams(BaseModel):
    """Mismo patrón que ConvictionThresholds pero en float — reusado para delta objetivo e
    IV Rank mínimo, los dos parámetros que el perfil de riesgo ajusta en la selección de
    strikes (Sección 'perfil de riesgo' 2026-07-24)."""

    conservador: float
    moderado: float
    agresivo: float

    def for_risk_level(self, risk_level: RiskLevel) -> float:
        return getattr(self, risk_level)


class IvRankSettings(BaseModel):
    min_sessions_for_real_iv: int
    full_window_sessions: int
    hv_window_days: int


class RiskLevelSupportSmaParams(BaseModel):
    """Qué SMA(s) aceptar como "buen soporte/resistencia" por perfil de riesgo (Sección
    'perfil de riesgo' refinado, 2026-07-24) — conservador solo SMA8 (más estricto), normal y
    agresivo aceptan SMA8 O SMA20 (cualquiera de las dos alcanza, más permisivo)."""

    conservador: list[int]
    moderado: list[int]
    agresivo: list[int]

    def for_risk_level(self, risk_level: RiskLevel) -> list[int]:
        return getattr(self, risk_level)


class StrategySettings(BaseModel):
    # Sección Fed/FRED (pedido 2026-07-26): no generar candidatos nuevos en días de CPI/NFP/FOMC.
    block_new_candidates_on_high_risk_days: bool = True
    # MVP: enfocado en 4 categorías (cash_secured_put/short_put_naked cuentan como una sola
    # categoría "Naked Put"). Las otras 15 estrategias quedan en el código sin borrar, solo
    # pausadas acá — para reactivarlas alcanza con sumarlas a esta lista en settings.yaml.
    enabled: list[str]
    # Perfil de riesgo (conservador/moderado/agresivo) ajusta qué tan OTM se eligen los strikes
    # y qué tan alto tiene que estar el IV Rank para considerar vender prima — no es solo un
    # filtro visual, cambia qué candidatos arma strategy/candidates.py.
    target_short_delta: RiskLevelFloatParams
    iv_rank_high_threshold: RiskLevelFloatParams
    # Refinamiento 2026-07-24 (pedido explícito del usuario, 3 preguntas de diseño confirmadas
    # — ver NOTES.md): cobertura mínima (% que el subyacente debe poder moverse antes de llegar
    # al strike vendido) y qué SMA(s) debe respetar el strike como soporte/resistencia técnico.
    min_coverage_pct: RiskLevelFloatParams
    support_sma_periods: RiskLevelSupportSmaParams


class SimulatorSettings(BaseModel):
    """Robot de Trading Automático (paper trading). Criterios de entrada COMBINADOS (confirmado
    con el usuario 2026-08-03, ver OptionsUp_Parametros_Venta_de_Puts.pdf): soporte fuerte + RSI
    (setup original) MÁS los filtros del PDF (precio >= VWAP, tendencia confirmada por medias,
    liquidez, POP, delta 0.15-0.25), y gestión de posición del PDF (cierre a 50% de ganancia,
    stop-loss, cierre a <=7 DTE). Los campos agregados 2026-08-03 tienen defaults neutros ("off")
    para no alterar el comportamiento de tests que construyen esta config con los campos viejos:
    el robot real los activa vía config/settings.yaml."""

    enabled: bool = True
    initial_capital: float
    max_position_pct: float
    profit_target_pct: float
    dte_range: tuple[int, int]
    max_delta: float
    rsi_range: tuple[float, float]
    iv_rank_min: float
    iv_percentile_min: float
    support_max_distance_pct: float
    weekly_support_max_distance_pct: float
    sma_periods: list[int]
    sma_min_distance_pct: float

    # --- Parámetros del PDF (2026-08-03), todos con default neutro ---
    # Selección de contrato: delta objetivo 0.15-0.25 (criterio 3 del PDF). `max_delta` sigue
    # siendo el techo; `delta_min` agrega el piso. Con delta_min=0 el comportamiento es el viejo.
    delta_min: float = 0.0
    # Liquidez (criterios 6 y 7): bid>0 siempre; OI/volumen mínimos y ancho de spread máximo
    # (como fracción del mid). Defaults desactivados (0 y 1.0 = 100%).
    min_open_interest: int = 0
    min_contract_volume: int = 0
    max_bid_ask_spread_pct: float = 1.0
    require_positive_bid: bool = False
    # Probabilidad OTM mínima (criterio 5), estimada como 1 - |delta|. 0 = desactivado.
    min_probability_otm: float = 0.0
    # Crédito mínimo por contrato (en $ por acción) para que valga comprometer la garantía.
    min_credit: float = 0.0
    # PRIMA MÍNIMA PROPORCIONAL A LA EXPOSICIÓN (usuario 2026-09-04). `min_credit` es un piso en
    # dólares por acción y no sirve para esto: $0.10 es lo mismo para un put de AAL de $13 que para
    # uno de AAPL de $250, cuando la exposición es 19 veces más grande.
    #
    # El 2026-09-04, con dinero real: el robot vendió un AAPL 250 (21% abajo del precio) por $26 de
    # prima, comprometiendo $25.000 de exposición de asignación por 42 días. Usuario: "en una
    # exposición de AAPL no puede solo tener una prima de 26, mínimo debe ser 250".
    #
    # Se expresa como fracción del STRIKE porque así escala solo con el tamaño de la exposición: la
    # prima por acción debe ser ≥ `min_premium_pct_of_strike` × strike. Un mismo porcentaje pide
    # cientos de dólares en un AAPL de $250 y unos pocos en un AAL de $13, que es exactamente la
    # proporción entre las dos exposiciones.
    #
    # El nivel vive en settings.yaml, no acá: el usuario lo eligió mirando sus propias operaciones.
    #
    # Los contratos se cancelan en la cuenta (prima y exposición escalan igual), así que el piso vale
    # por contrato y no depende de cuántos se manden. 0 = desactivado.
    min_premium_pct_of_strike: float = 0.0
    # Retorno anualizado mínimo sobre la garantía (prima/strike * 365/DTE). El robot descarta lo
    # que no llega y, entre los que pasan, elige el de MAYOR retorno anualizado (usuario: apunta a
    # 40-50% anualizado, hoy saca >50%). 0 = desactivado.
    min_annualized_return: float = 0.0
    # Confirmación de tendencia (criterio 9): precio por ENCIMA de la SMA de tendencia y esa SMA
    # con pendiente alcista respecto de la larga. Reemplaza el gate viejo de "precio bajo TODAS
    # las SMA" cuando está activo.
    confirm_uptrend: bool = False
    trend_sma_period: int = 50
    # VWAP (criterio 8): exigir precio >= VWAP intradía. El engine calcula el VWAP y lo pasa a la
    # evaluación; si el dato no está y esto es True, el símbolo se saltea por datos incompletos.
    require_price_above_vwap: bool = False
    # IV Rank "real": rechazar entradas cuando el IV Rank viene del proxy de HV, no de IV
    # implícita (criterio 4 + "no operar con datos incompletos").
    require_real_iv_rank: bool = False

    # --- Gestión de riesgo de cartera (PDF) ---
    max_open_positions: int = 1_000_000  # "máximo 5 posiciones abiertas"
    min_free_buying_power_pct: float = 0.0  # "mantener al menos 30% del buying power libre"
    # Tope de aperturas de puts por DÍA (usuario 2026-08: "que no abra más de 10 tickets por día,
    # así puedo revisarlos"). 0 = sin tope.
    max_opens_per_day: int = 5
    # Naked puts SOLO sobre acciones por debajo de este precio (usuario 2026-08-07, trading real):
    # las más caras que esto no se operan. 0 = sin tope de precio.
    max_underlying_price_puts: float = 700.0
    # No abrir un put si la acción SUBE más de esto hoy (%), y preferir las que caen (usuario
    # 2026-08-04: no entrar caro en algo que viene volando — más propenso a corregir/rebotar).
    max_entry_day_change_pct: float = 2.0
    # Cuánto MÁS abajo (%) del tope de suba se considera la entrada "ideal" para el puntaje de
    # cómo viene la acción hoy (cayendo esto o más → puntaje 1).
    day_change_pref_span_pct: float = 4.0

    # --- Clasificación de volatilidad (2026-08-03, "por IV") ---
    # Una acción es "volátil" si su IV ATM (o HV como respaldo) supera este umbral anualizado.
    # Rige la cobertura pedida (10% vs 8%) y la delta objetivo (0.15 vs 0.20).
    volatile_iv_threshold: float = 0.40

    # --- Cobertura dinámica (distancia OTM del strike, "un poco de todo") ---
    # Cobertura base según volatilidad de la acción.
    coverage_normal: float = 0.0        # no muy volátil (default off; 0.08 en yaml)
    coverage_volatile: float = 0.0      # volátil (0.10 en yaml)
    # Ajustes por contexto: lejos de soporte pide más; apoyado en soporte/SMA200 permite la base.
    coverage_far_from_support: float = 0.0   # 0.12 en yaml
    coverage_sma200_far_above: float = 0.0   # 0.09 en yaml (precio muy por debajo de la SMA200)
    # Distancia (fracción) para considerar "cerca" de un soporte fuerte o de la SMA200 diaria.
    near_support_pct: float = 0.03
    near_sma200_pct: float = 0.03
    # Si hay Fed/earnings antes del vencimiento: entrar igual (aprovechar la vola) pero sumando
    # esta cobertura extra en vez de bloquear. 0 = sin ajuste.
    event_coverage_bump: float = 0.0    # 0.02 en yaml
    # Gate duro de cobertura mínima del contrato elegido (se calcula dinámicamente arriba).
    require_coverage: bool = False

    # --- Delta objetivo por volatilidad (criterio 9) ---
    use_dynamic_delta: bool = False     # si False, usa el rango [delta_min, max_delta] (viejo)
    delta_target_volatile: float = 0.15
    delta_target_normal: float = 0.20
    delta_band: float = 0.05            # |delta| elegible dentro de target ± band

    # --- Cerebro flexible: puntaje en vez de gates rígidos (usuario 2026-08-04) ---
    # En vez de EXIGIR delta Y cobertura Y retorno por separado (casi nunca se dan juntos), puntúa
    # cada put por qué tan cerca está del ideal en cada dimensión y elige el mejor "promedio".
    use_soft_scoring: bool = False
    min_entry_score: float = 0.0        # puntaje total mínimo (0-1) para abrir
    delta_score_tol: float = 0.15       # a esta distancia del delta objetivo el puntaje de delta llega a 0
    target_annualized: float = 0.20     # retorno anualizado "ideal" (puntaje 1 al llegar acá)
    # Pesos de cada dimensión en el puntaje (se normalizan solos).
    score_weight_delta: float = 0.35
    score_weight_coverage: float = 0.25
    score_weight_return: float = 0.25
    score_weight_pop: float = 0.15
    score_weight_day_change: float = 0.20  # peso de "que la acción venga cayendo hoy" (usuario 2026-08-04)
    # Nuevas dimensiones del cerebro (usuario 2026-08-05: "ajustar lo mejor"): prefiere IV Rank alta,
    # spread ajustado (liquidez) y buen decaimiento de prima (theta).
    score_weight_iv_rank: float = 0.25
    score_weight_liquidity: float = 0.12
    score_weight_theta: float = 0.12

    # --- Soporte mensual (además del diario) ---
    require_monthly_support: bool = False  # el robot exige soporte fuerte en diario Y mensual
    monthly_support_max_distance_pct: float = 0.12
    # Techo de strike por SOPORTE (usuario 2026-08-10): el strike del put debe quedar en/por debajo de un
    # soporte fuerte del 2º para abajo (más profundo → más cobertura), no pegado al 1º (deja muy poco
    # colchón). Entre los soportes del rank pedido para abajo, se elige el MÁS FUERTE (más toques).
    # 1 = usar el más cercano (viejo); 2 = del 2º soporte para abajo. 0 = desactivado (sin techo por soporte).
    put_min_support_rank: int = 2
    # RSI como gate duro: el robot combinado lo deja opcional (la lógica nueva no lo lista).
    require_rsi: bool = True

    # --- Gestión de posición (PDF + escalonado 2026-08-03) ---
    # Stop-loss como múltiplo de la prima cobrada: cerrar si el valor de la opción llega a
    # entry_premium * (1 + stop_loss_multiple). 0 = desactivado.
    stop_loss_multiple: float = 0.0
    # Cerrar cuando falten <= close_at_dte días al vencimiento (0 = desactivado).
    close_at_dte: int = 0
    # Salida de ganancia PLANA a 30% (profit_target_pct): estos escalones opcionales (esperar más
    # lejos del strike / cerca de vencimiento) están en 0.0 = desactivados (usuario 2026-08-05:
    # "30 o más"). Se dejan como parámetros por si en el futuro se quieren reactivar.
    profit_target_far_pct: float = 0.0          # 0 = sin escalón (ganancia a 30% plano)
    profit_target_near_exp_pct: float = 0.0     # 0 = sin escalón (ganancia a 30% plano)
    far_from_strike_pct: float = 0.10           # coverage actual >= esto = "lejos del strike"
    near_exp_dte: int = 7                        # <= esto = "cerca de vencimiento" (salvaguarda por noticia)

    # --- Salida de ganancia por ANTIGÜEDAD de la operación (regla del usuario 2026-08-12) ---
    # Objetivo de ganancia ESCALONADO por días desde que se ABRIÓ la operación (aplica IGUAL al
    # simulador y al Real Market, venta de puts):
    #   · días 0 a (profit_age_step2_days-1): profit_target_pct (30%)
    #   · días profit_age_step2_days a (profit_age_step3_days-1): profit_target_step2_pct (35%)
    #   · profit_age_step3_days o más: profit_target_step3_pct (40%)
    # (Reemplaza el esquema semana1/semana2 + 45% cerca de vencimiento anterior — usuario 2026-08-12:
    # "30% a 2 días, 35% a 3 días, 40% a 7 días o más", con el 30% desde el día 0.) Las salvaguardas
    # (stop-loss, DTE mínimo, noticia importante) siguen aparte, no son toma de ganancia.
    profit_age_step2_days: int = 3               # a partir de este día: 35%
    profit_age_step3_days: int = 7               # a partir de este día: 40%
    profit_target_step2_pct: float = 0.35        # objetivo días 3-6
    profit_target_step3_pct: float = 0.40        # objetivo días 7+
    # Compat: campos del esquema anterior (semana1/semana2/near-exp). Ya NO los usa
    # tiered_profit_target, se conservan para no romper configs viejas que los traen.
    profit_week1_days: int = 7
    profit_week2_days: int = 14
    profit_target_week1_pct: float = 0.18
    profit_target_week2_pct: float = 0.30
    profit_dte_threshold: int = 20
    profit_target_near_exp_far_pct: float = 0.45
    near_strike_close_pct: float = 0.06          # precio a <=6% del strike = "cerca/riesgoso"

    # --- Fricción y ejecución con órdenes límite (usuario 2026-08-03) ---
    commission_per_contract: float = 0.0
    use_bid_ask_fills: bool = False  # (obsoleto, reemplazado por fill_edge_pct; se deja por compat)
    # Negociación del spread: en vez de asumir el peor fill, modela que se trabaja con órdenes
    # límite hacia el lado favorable. fill_edge_pct = fracción del medio-spread capturada a favor:
    # 0 = mid; 1 = ask al ABRIR (vender caro) / bid al CERRAR (comprar barato). 0.5 = a mitad de
    # camino entre el mid y el lado favorable (negociación buena pero no perfecta).
    fill_edge_pct: float = 0.0

    # Modelo de capital: "naked" usa el margen Reg-T (chico) → rendimiento real de naked put;
    # "cash_secured" reserva strike*100. (usuario 2026-08-04: opera naked).
    margin_mode: str = "cash_secured"
    # Calibración al margen REAL del broker (usuario 2026-08-06): multiplicador sobre el margen
    # Reg-T naked, para que el colateral y el anualizado del robot coincidan con tu cuenta (que usa
    # portfolio margin, más bajo que Reg-T). 1.0 = Reg-T puro. El aprendizaje lo recalcula solo desde
    # el maintenanceRequirement real de tus posiciones (learning_state 'sim.broker_margin_factor').
    broker_margin_factor: float = 1.0

    # --- Tamaño por tramo de precio del subyacente (contratos por posición) ---
    # Si está en True, el tamaño se decide por el precio de la acción (más contratos en acciones
    # baratas). Reemplaza al tope por % de capital (max_position_pct) — solo lo limita el cash.
    use_price_tier_sizing: bool = False
    price_tier_low: float = 50.0
    price_tier_high: float = 400.0
    contracts_cheap: int = 4      # precio < price_tier_low (hasta $50 → 4)
    contracts_mid: int = 3        # price_tier_low <= precio < price_tier_high ($50-$400 → 3)
    contracts_expensive: int = 1  # precio >= price_tier_high ($400+ → 1)

    # Tasa libre de riesgo para cálculos de probabilidad, si hicieran falta.
    risk_free_rate: float = 0.043

    # Si es True, el robot evalúa TODO el universo del S&P 500 (config/universe_sp500.yaml, ~386
    # acciones) en vez de solo la watchlist de 15. "Ver todo el mercado" (usuario 2026-08-04).
    # Cada corrida tarda más (cientos de cadenas de opciones), pero encuentra las volátiles de
    # prima alta que la watchlist conservadora no tiene.
    scan_full_universe: bool = False


class IntradayButterflySettings(BaseModel):
    """Estrategia 2 (2026-08-03): Iron Butterfly 0DTE intradía de reversión a la media móvil 8
    (SPX). Day-trading rápido: cuando el precio se ALEJA de la SMA8 en el gráfico de 1 min, se
    entra buscando la reversión a la SMA8, con las alas armando un riesgo acotado, y se sale a
    +profit_target o -stop_loss en dólares. SEPARADA del robot de CSP. Arranca apagada hasta que
    el motor en vivo esté probado con datos intradía reales."""

    enabled: bool = False
    underlying: str = "$SPX"   # Schwab exige el índice con prefijo $ (SPX pelado no trae cadena)
    timeframe_minutes: int = 1          # gráfico de 1 (o 2) minutos
    sma_period: int = 8                 # media móvil 8
    # Cuánto se tiene que alejar el precio de la SMA8 (fracción) para disparar la entrada.
    distance_threshold_pct: float = 0.0010   # ~0.10% (bajado 2026-08-05: SPX estaba muy quieto y no entraba)
    # El breakeven del butterfly se ubica este % en la dirección de la reversión.
    breakeven_offset_pct: float = 0.0015
    # Ancho de las alas (en puntos del subyacente). SPX cotiza en múltiplos de 5.
    wing_width: float = 5.0
    # Riesgo/objetivo por operación, en dólares.
    max_collateral: float = 500.0       # tope para ARMAR el fly (~máx teórico de un fly de 5pts en SPX)
    profit_target_pct: float = 0.30     # cerrar al 30% del crédito, en TODO momento (usuario 2026-08-07:
                                        # "más entradas"). Si es > 0, manda sobre el objetivo en dólares.
    profit_target: float = 50.0         # (respaldo en $) cerrar al llegar a +$50 si profit_target_pct = 0
    stop_loss: float = 70.0             # cerrar al llegar a -$70 (control real de riesgo por operación)
    dte: int = 0                        # 0DTE (vencimiento del mismo día)
    max_open_positions: int = 1
    # Freno del día (usuario 2026-08-08): si el butterfly cierra por STOP-LOSS esta cantidad de veces
    # SEGUIDAS en el mismo día, no abre más butterflies hoy (arranca de nuevo al día siguiente). Una
    # ganancia en el medio corta la racha. 0 = sin freno.
    stop_loss_streak_halt: int = 2


class IntradayCondorSettings(BaseModel):
    """Estrategia 3 (2026-08-05): Iron Condor 0DTE sobre SPX para DÍAS CALMOS (poco movimiento).
    Vende un put y un call OTM a delta <= short_delta_max (donde MEJOR pague), compra alas
    `wing_width` puntos más afuera (riesgo acotado), y gana con que SPX se quede en el rango
    (theta). Entra TEMPRANO si el día viene calmo (rango intradía chico) dentro de la ventana
    horaria, hasta max_per_day por día. Sale a +profit_target_pct del crédito o -stop_loss_dollars."""

    enabled: bool = True
    underlying: str = "$SPX"
    timeframe_minutes: int = 1
    short_delta_max: float = 0.15        # vender put/call a delta <= 0.15
    wing_width: float = 10.0             # alas de 10 puntos
    profit_target_pct: float = 0.50      # cerrar al 50% del crédito una vez pasada la ventana temprana
    profit_target_early_pct: float = 0.40  # si YA está a +40% dentro de los primeros `early_window_minutes`,
                                          # cerrar ya para reentrar (usuario 2026-08-07)
    early_window_minutes: float = 20.0   # "temprano" = primeros 20 min de vida de la posición
    stop_loss_dollars: float = 100.0     # cerrar con -$100 de pérdida
    dte: int = 0                         # 0DTE
    max_per_day: int = 3                 # hasta 3 por día
    max_open_positions: int = 3          # y hasta 3 abiertos a la vez
    # Freno del día (usuario 2026-08-08): si el condor cierra por STOP-LOSS esta cantidad de veces
    # SEGUIDAS en el mismo día, no abre más condors hoy (arranca de nuevo al día siguiente). Una
    # ganancia en el medio corta la racha. 0 = sin freno.
    stop_loss_streak_halt: int = 2
    # "Día calmo": rango intradía (máx-mín) como fracción del primer precio, por debajo de esto.
    calm_range_pct: float = 0.004        # <= 0.4% de rango = calmo
    # Ventana horaria de entrada (ET) — temprano, con tiempo para que corra el 0DTE.
    # Ventana de entrada en HORA DE NUEVA YORK (cambiado 2026-08-27). Antes se comparaba en UTC.
    entry_window_start: str = "09:30"
    entry_window_end: str = "14:00"
    max_collateral: float = 1000.0       # 10 pts de ala en SPX = ~$1000 teórico (riesgo real = stop $100)
    min_credit: float = 0.0              # crédito mínimo en DÓLARES para armar (0 = sin mínimo)
    # Filtro de VIX QUIETO (usuario 2026-08-14). Primero se pensó como "que el VIX no suba", pero el
    # usuario lo corrigió: "no tiene que estar bajando ni subiendo; lo mejor es que no suba ni baje
    # mucho ese día, que sea un día lateral estable". Un VIX que se DERRUMBA 8% tampoco es un día
    # tranquilo — suele ser un rally fuerte — y al condor lo que lo mata es que el SPX se mueva,
    # para el lado que sea. Por eso el tope es sobre el movimiento ABSOLUTO: si |ΔVIX del día| supera
    # este %, no se abre. None = filtro apagado (comportamiento histórico intacto). Es una de las
    # perillas que el aprendizaje puede mover.
    max_vix_change_pct: float | None = None
    # --- Paso a DINERO REAL del Iron Condor (usuario 2026-08-13: "conectar el condor en real, el
    # mismo cerebro que en papel, mismo stop loss y mismo profit %") ---
    # APAGADO por default: aunque el condor de PAPEL corra (enabled=true), NUNCA se manda una orden
    # real de condor hasta que esto sea True Y el trading real esté encendido/armado (live_trading).
    # Es un segundo cinturón, redundante con live_trading.enabled — los DOS tienen que estar prendidos.
    live_enabled: bool = False
    # Tope DURO de condors REALES abiertos por día (usuario 2026-08-13: "1 condor por día"). Es un
    # conteo SEPARADO del condor de papel — el real lleva el suyo. 0 = sin tope (no recomendado).
    live_max_per_day: int = 1
    # Crédito MÍNIMO en dólares para abrir un condor REAL. Es un piso DURO y aparte de `min_credit`
    # a propósito: `min_credit` es una perilla que el aprendizaje puede mover (y puede bajarla hasta
    # 0), y el 2026-09-02 el robot abrió un condor cobrando $95 contra $905 de riesgo — 1 a 9.5,
    # cuando lo normal en este libro venía siendo 1 a 5. El usuario fue explícito: "tampoco puede
    # abrir con esa prima de .95". Este piso NO lo toca el aprendizaje. 0 = sin piso.
    live_min_credit: float = 0.0
    # Cuántos minutos como máximo puede quedar PUESTA una apertura que todavía no llenó. El
    # 2026-09-02 una orden quedó colgada 38 minutos (09:30 → 10:08) y llenó en un mercado que ya no
    # era el que la había justificado: el precio se había movido, la volatilidad también, y nadie
    # volvió a preguntarse si esa entrada seguía teniendo sentido. Pasado este plazo se cancela y,
    # si la oportunidad sigue viva, el próximo tick la vuelve a armar con precios de AHORA.
    # 0 = sin caducidad (comportamiento viejo).
    open_working_max_minutes: float = 5.0


class LiveTradingSettings(BaseModel):
    """Topes DUROS de trading REAL en Schwab (usuario 2026-08-07, poco capital: "buena seguridad, que
    no me ponga más de lo que pedí, que sea perfecto"). TODO apagado por default: hasta que `enabled`
    sea True (y el día esté armado desde el dashboard), NUNCA se manda una orden real. Estos números
    son un segundo cinturón de seguridad, redundante con la estrategia, evaluado justo antes del envío
    por `execution.live_guard`. El guard nunca los relaja ni agranda una orden."""
    enabled: bool = False                    # MAESTRO: False = jamás se envía una orden real
    dry_run: bool = True                     # True = construye y loguea la orden pero NO la manda
    kill_switch: bool = False                # freno de emergencia (corta todo, incluso armado)
    require_manual_arm: bool = True          # exige "armar" el trading real de HOY desde el dashboard
    max_contracts_per_order: int = 4         # TECHO duro del guardián: jamás manda más que esto por orden
    # Cuántos contratos PIDE el robot automático. Separado del techo de arriba a propósito: el techo es
    # el freno de seguridad (solo recorta), esto es la estrategia (cuánto se quiere operar).
    base_contracts_per_order: int = 1        # lo normal
    # "Cuando el strike es menos de $30 debe abrir más cantidad, mínimo 4" (usuario 2026-08-17). Un put
    # de strike $13 traba ~$88 de colateral por contrato contra los ~$2.500 de uno de $285: a 1 contrato
    # la posición barata era 28 veces más chica que las caras y casi no movía la aguja. Con 4 contratos
    # queda un tamaño comparable. 0 en cualquiera de los dos = regla apagada.
    cheap_strike_max: float = 0.0            # strike POR DEBAJO de esto = "barato"
    cheap_strike_contracts: int = 0          # ...y ahí se piden estos contratos
    # Colateral OBJETIVO por posición, en dólares. Cuando está en >0, la cantidad de contratos sale
    # de dividir este objetivo por lo que traba UN contrato, en vez de mirar el número del strike.
    # 0 = apagado (se usa la regla vieja por strike). Ver `contracts_for_collateral`.
    target_collateral_per_position: float = 0.0
    max_notional_per_order: float = 40_000.0 # tope duro de notional (strike×100×contratos) por orden
    max_orders_per_day: int = 5              # tope duro de órdenes reales por día
    max_orders_per_week: int = 0             # tope duro por semana (0 = sin tope; Fase 1 = 5, usuario 2026-08-09)
    max_total_deployed: float = 50_000.0     # tope duro de capital comprometido total (colateral) por día
    max_underlying_price: float = 700.0      # no operar acciones por encima de este precio
    # Máximo de posiciones REALES vivas sobre el MISMO subyacente, contando TODOS los días
    # (usuario 2026-09-07). El tope que ya existía solo miraba el día en curso, así que el robot
    # podía volver al mismo símbolo mañana y pasado mañana: quedaron dos AAL 13P del mismo
    # vencimiento, abiertos el 17 y el 18 de agosto, doblando la apuesta a la misma acción sin que
    # nadie lo decidiera. Cuenta posiciones ABIERTAS, no órdenes: cuando una cierra se libera el
    # lugar. No aplica a las órdenes pedidas por chat, que saltean los topes a propósito.
    # 0 = desactivado.
    max_open_real_per_symbol: int = 0
    # Piso DURO de prima para una apertura REAL, como fracción del strike (usuario 2026-09-04). Es
    # el mismo criterio que `simulator.min_premium_pct_of_strike`, repetido acá a propósito: aquel
    # vive en el cerebro (y el aprendizaje puede mover las perillas de al lado), este es el segundo
    # cinturón, evaluado justo antes de mandar la orden. Mismo patrón que `live_min_credit` en el
    # condor, que existe porque el 2026-09-02 una perilla aprendida dejó abrir cobrando $95.
    # 0 = desactivado.
    min_premium_pct_of_strike: float = 0.0
    min_account_cash_buffer: float = 0.0     # dejar siempre este cash libre en la cuenta
    allowed_symbols: list[str] = []          # whitelist: si no está vacía, SOLO estos símbolos
    account_number: str = ""                 # cuenta Schwab a operar (vacío = la primera vinculada)
    price_cap_exempt_symbols: list[str] = []  # exentos del tope de precio (usuario 2026-08-09: SPY)
    # Negociación del spread ("caminar el precio", usuario 2026-08-09): órdenes límite que se reemplazan
    # de a pasos hasta el fill. Vender: arranca 1 paso bajo el ask y BAJA hasta el mid. Recomprar:
    # arranca 1 paso sobre el bid y SUBE hasta el mid. El paso es en $ de PRIMA (0.02 = 2 centavos =
    # $2/contrato). Nunca cruza el mid (piso/techo).
    price_walk_step: float = 0.02            # paso en spreads CHICOS = $2/contrato (usuario: "de 2 en 2")
    price_walk_wide_step: float = 0.05       # paso en spreads GRANDES = $5/contrato ("de 5 en 5")
    price_walk_wide_threshold: float = 0.50  # spread mayor a esto = "grande" → usa el paso de $5
    price_walk_interval_seconds: int = 10    # reemplazar la orden cada 10 segundos (usuario 2026-08-09)
    price_walk_stop_at_mid: bool = True      # no cruzar el mid price (no regalar más de medio spread)


class Settings(BaseModel):
    broker: BrokerSettings
    database: DatabaseSettings
    market: MarketSettings
    llm: LlmSettings
    scheduler: SchedulerSettings
    investor_profile: InvestorProfileSettings
    conviction_thresholds: ConvictionThresholds
    iv_rank: IvRankSettings
    strategy: StrategySettings
    simulator: SimulatorSettings
    # Estrategia 2 (Iron Butterfly 0DTE intradía). Opcional con default para no romper configs/
    # tests que no la definen; se activa desde config/settings.yaml cuando el motor esté listo.
    intraday_butterfly: IntradayButterflySettings = IntradayButterflySettings()
    intraday_condor: IntradayCondorSettings = IntradayCondorSettings()
    # Trading REAL en Schwab (Entrega en preparación, usuario 2026-08-07). Opcional con default para no
    # romper configs/tests existentes; SIEMPRE apagado hasta activarlo explícitamente.
    live_trading: LiveTradingSettings = LiveTradingSettings()


class SymbolsConfig(BaseModel):
    symbols: list[str]


def load_settings(path: Path | None = None) -> Settings:
    path = path or (PROJECT_ROOT / "config" / "settings.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Settings.model_validate(raw)


def load_symbols(path: Path | None = None) -> list[str]:
    path = path or (PROJECT_ROOT / "config" / "symbols.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return SymbolsConfig.model_validate(raw).symbols


def load_universe_symbols(path: Path | None = None) -> list[str]:
    """Universo amplio de referencia (large-caps líquidos estilo S&P 500) para el escaneo de
    "mejores oportunidades" — ver dashboard/pages/8_escaneo.py, que lo combina con
    load_symbols() (watchlist real) para armar el universo efectivo a escanear."""
    path = path or (PROJECT_ROOT / "config" / "universe_sp500.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return SymbolsConfig.model_validate(raw).symbols


def load_scan_symbols(scan_full_universe: bool) -> list[str]:
    """Símbolos que el robot/análisis debe evaluar: si `scan_full_universe`, TODO el S&P 500
    (503) MÁS el Nasdaq-100 (103) MÁS la watchlist a mano, deduplicado; si no, solo la watchlist
    de 15 (config/symbols.yaml). "Ver todo el mercado: Nasdaq y S&P 500" (usuario 2026-08-04)."""
    if scan_full_universe:
        # Nasdaq-100 (tech/volátil, ~103) + watchlist. Más liviano que el S&P 500 completo para no
        # sobrecargar/corromper la base (2026-08-04); se puede ampliar cuando esté estable.
        return sorted(set(load_movers_universe("$COMPX")) | set(load_symbols()))
    return load_symbols()


# Componentes REALES de cada índice para Market Movers (pedido 2026-07-29: "top 10 real por %",
# no el ranking por volumen que da /movers de Schwab — confirmado en vivo que ese endpoint
# siempre devuelve las mismas 10 acciones de mayor volumen sin importar sort/frequency, nunca
# 10 ganadoras + 10 perdedoras reales). Distinto de universe_sp500.yaml (386 símbolos, lista
# "de referencia" aproximada para Escaneo) — acá la exactitud importa (es la base del ranking
# real), así que son listas completas obtenidas de Wikipedia el 2026-07-29 (ver comentario de
# cada archivo). $COMPX usa Nasdaq-100 (103 símbolos), no el Nasdaq Composite completo
# (~3000+, impracticable de cotizar en batch).
_MOVERS_UNIVERSE_FILES = {
    "$SPX": "sp500.yaml",
    "$COMPX": "nasdaq100.yaml",
    "$DJI": "dow30.yaml",
}


def load_movers_universe(index: str) -> list[str]:
    path = PROJECT_ROOT / "config" / "movers_universe" / _MOVERS_UNIVERSE_FILES[index]
    with open(path) as f:
        raw = yaml.safe_load(f)
    return SymbolsConfig.model_validate(raw).symbols


def load_priority_watchlist_symbols(path: Path | None = None) -> list[str]:
    """Watchlist real del usuario (thinkorswim, ~95 símbolos) — universo PRIORITARIO para el
    escaneo, unido en dashboard/pages/8_escaneo.py con load_symbols() (13 fijos) y
    load_universe_symbols() (large-caps genéricos). Ver config/watchlist_thinkorswim.yaml
    para la lista de símbolos excluidos y por qué."""
    path = path or (PROJECT_ROOT / "config" / "watchlist_thinkorswim.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return SymbolsConfig.model_validate(raw).symbols


def configure_logging(path: Path | None = None) -> None:
    """Arma el logging del proceso a partir de config/logging.yaml.

    Convierte a ruta ABSOLUTA el `filename` de los handlers de archivo y crea la carpeta. Sin esto,
    la ruta relativa del YAML depende del directorio desde el que se arrancó el proceso: el robot
    lo lanza launchd (con WorkingDirectory) pero un `python scripts/run_scheduler.py` a mano desde
    otra carpeta escribiría el log en cualquier lado — o reventaría al arrancar por una carpeta
    inexistente, que con plata real significa el robot caído."""
    path = path or (PROJECT_ROOT / "config" / "logging.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f)
    for handler in (raw.get("handlers") or {}).values():
        nombre = handler.get("filename")
        if not nombre:
            continue
        destino = Path(nombre)
        if not destino.is_absolute():
            destino = PROJECT_ROOT / destino
        destino.parent.mkdir(parents=True, exist_ok=True)
        handler["filename"] = str(destino)
    logging.config.dictConfig(raw)
