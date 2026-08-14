from __future__ import annotations

from datetime import date

from options_advisor.broker.models import PriceBar
from options_advisor.config import SimulatorSettings

# Reglas puras del robot (paper trading), confirmadas con el usuario 2026-08-03
# (OptionsUp_Parametros_Venta_de_Puts.pdf + criterios detallados en chat): clasificación de
# volatilidad "por IV", cobertura dinámica según contexto, delta objetivo por volatilidad, y
# salidas escalonadas. Todo puro y sin I/O para poder testearlo aparte de la base y el broker.

# Un delta de Schwab de -999 es el centinela de "sin dato" (contrato ilíquido) — nunca un delta
# real. Cualquier |delta| por encima de esto es basura, no una opción profundamente ITM.
_DELTA_SENTINEL = 1.5


def is_volatile(iv_atm: float | None, hv_20d: float | None, settings: SimulatorSettings) -> bool:
    """"Volátil" por IV (decisión del usuario 2026-08-03): IV ATM anualizada por encima del
    umbral; si no hay IV, cae a la HV como respaldo. Sin ninguno de los dos → no volátil."""
    iv = iv_atm if iv_atm is not None else hv_20d
    if iv is None:
        return False
    return iv >= settings.volatile_iv_threshold


def put_coverage_pct(underlying_price: float, strike: float) -> float:
    """Cobertura de un put vendido: cuánto tiene que caer el subyacente para tocar el strike,
    como fracción del precio. Positivo = OTM (strike por debajo del precio)."""
    if underlying_price <= 0:
        return 0.0
    return (underlying_price - strike) / underlying_price


def naked_put_margin(underlying_price: float, strike: float, premium: float) -> float:
    """Requisito de margen de un NAKED put (fórmula Reg-T estándar del broker), en dólares por
    contrato. Es el mayor de: (a) 20% del subyacente − lo que está OTM + prima, (b) 10% del strike
    + prima. MUCHO menor que la garantía cash-secured (strike*100), por eso el rendimiento sobre el
    capital es mucho mayor. (usuario 2026-08-04: opera naked, no cash-secured)."""
    otm = max(0.0, underlying_price - strike)  # un put está OTM cuando el subyacente > strike
    method_a = 0.20 * underlying_price - otm + premium
    method_b = 0.10 * strike + premium
    per_share = max(method_a, method_b, 0.0)
    return round(per_share * 100, 2)


def per_contract_cost(underlying_price: float, strike: float, premium: float, settings: SimulatorSettings) -> float:
    """Capital que compromete UN contrato: el margen naked si `margin_mode == 'naked'`, si no la
    garantía cash-secured (strike*100). En modo naked se multiplica por `broker_margin_factor` para
    calibrar al margen REAL del broker (portfolio margin < Reg-T; usuario 2026-08-06)."""
    if settings.margin_mode == "naked":
        factor = getattr(settings, "broker_margin_factor", 1.0) or 1.0
        return round(naked_put_margin(underlying_price, strike, premium) * factor, 2)
    return strike * 100.0


def annualized_return_on_cost(credit_per_share: float, cost_per_contract: float, dte: int) -> float:
    """Retorno anualizado sobre el capital REAL comprometido (margen naked o garantía CSP)."""
    if cost_per_contract <= 0 or dte <= 0:
        return 0.0
    return (credit_per_share * 100.0 / cost_per_contract) * (365.0 / dte)


def annualized_return_on_collateral(credit: float, strike: float, dte: int) -> float:
    """Retorno anualizado sobre la garantía cash-secured de una CSP: (prima/strike) * (365/DTE).
    Es la métrica que el usuario optimiza (2026-08-03): a igual strike, 30 días suele anualizar
    MÁS que 90 aunque el crédito absoluto sea menor, porque el capital está menos tiempo
    comprometido. El robot elige el contrato que MAXIMIZA esto entre los que pasan los filtros."""
    if strike <= 0 or dte <= 0:
        return 0.0
    return (credit / strike) * (365.0 / dte)


def delta_target(volatile: bool, settings: SimulatorSettings) -> float:
    return settings.delta_target_volatile if volatile else settings.delta_target_normal


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def day_change_score(day_change_pct: float | None, settings: SimulatorSettings) -> float:
    """Puntaje 0-1 según cómo viene la acción HOY (usuario 2026-08-04): prefiere las que están
    CAYENDO (mejor entrada, más chance de rebote), penaliza las que suben. En el tope permitido
    (+max_entry_day_change_pct) el puntaje es 0; cayendo `day_change_pref_span_pct` o más, es 1.
    Sin dato → 0.5 (neutral)."""
    if day_change_pct is None:
        return 0.5
    cap = settings.max_entry_day_change_pct          # ej. +2% (máximo de suba permitido)
    span = settings.day_change_pref_span_pct          # ej. 4% (qué tan abajo es "ideal")
    denom = cap + span
    if denom <= 0:
        return 0.5
    return _clip01((cap - day_change_pct) / denom)


# Dimensiones del "cerebro" y el campo de settings con su peso base. Una sola fuente de verdad,
# compartida por el scoring (elegir la mejor) y por el aprendizaje (reponderar). Sumadas IV Rank,
# liquidez y theta (usuario 2026-08-05: "ajustar lo mejor" — que además prefiera IV alta, spreads
# ajustados y buen decaimiento de prima).
SCORE_DIMENSIONS = ("delta", "coverage", "return", "pop", "day_change", "iv_rank", "liquidity", "theta")
SCORE_WEIGHT_FIELD = {
    "delta": "score_weight_delta",
    "coverage": "score_weight_coverage",
    "return": "score_weight_return",
    "pop": "score_weight_pop",
    "day_change": "score_weight_day_change",
    "iv_rank": "score_weight_iv_rank",
    "liquidity": "score_weight_liquidity",
    "theta": "score_weight_theta",
}
_TARGET_THETA_RATIO = 0.02  # |theta|/prima por día considerado "ideal" (decaimiento fuerte)


def candidate_subscores(features: dict, volatile: bool, settings: SimulatorSettings) -> dict[str, float]:
    """Sub-puntaje 0-1 de CADA dimensión para un candidato, a partir de sus features crudas.
    `features`: delta_abs, coverage, annualized, day_change_pct, iv_rank, spread_pct, theta, premium.
    Un dato ausente cae a 0.5 (neutral). Es la base del scoring Y del aprendizaje."""
    delta_abs = features.get("delta_abs")
    coverage = features.get("coverage")
    annualized = features.get("annualized")
    iv_rank = features.get("iv_rank")
    spread_pct = features.get("spread_pct")
    theta = features.get("theta")
    premium = features.get("premium")

    target = delta_target(volatile, settings)
    tol = settings.delta_score_tol if settings.delta_score_tol > 0 else 0.15
    cov_ideal = settings.coverage_volatile if volatile else settings.coverage_normal

    delta_score = _clip01(1 - abs(delta_abs - target) / tol) if isinstance(delta_abs, (int, float)) else 0.5
    cov_score = _clip01(coverage / cov_ideal) if isinstance(coverage, (int, float)) and cov_ideal > 0 else 0.5
    ann_score = _clip01(annualized / settings.target_annualized) if isinstance(annualized, (int, float)) and settings.target_annualized > 0 else 0.5
    pop_score = _clip01(((1 - delta_abs) - 0.5) / 0.4) if isinstance(delta_abs, (int, float)) else 0.5
    day_score = day_change_score(features.get("day_change_pct") if isinstance(features.get("day_change_pct"), (int, float)) else None, settings)
    # IV Rank más ALTA = mejor (más prima). Piso en el mínimo exigido.
    if isinstance(iv_rank, (int, float)) and settings.iv_rank_min < 100:
        ivr_score = _clip01((iv_rank - settings.iv_rank_min) / (100 - settings.iv_rank_min))
    else:
        ivr_score = 0.5
    # Liquidez: spread más AJUSTADO = mejor (mejor fill y salida).
    if isinstance(spread_pct, (int, float)) and settings.max_bid_ask_spread_pct > 0:
        liq_score = _clip01(1 - spread_pct / settings.max_bid_ask_spread_pct)
    else:
        liq_score = 0.5
    # Theta: más decaimiento por día relativo a la prima = mejor ingreso.
    if isinstance(theta, (int, float)) and isinstance(premium, (int, float)) and premium > 0:
        theta_score = _clip01((abs(theta) / premium) / _TARGET_THETA_RATIO)
    else:
        theta_score = 0.5
    return {"delta": delta_score, "coverage": cov_score, "return": ann_score, "pop": pop_score,
            "day_change": day_score, "iv_rank": ivr_score, "liquidity": liq_score, "theta": theta_score}


def score_put_candidate(features: dict, volatile: bool, settings: SimulatorSettings) -> float:
    """Cerebro flexible: puntúa un put 0-1 promediando (con pesos) qué tan cerca está del IDEAL en
    cada dimensión — delta al objetivo, más cobertura, más retorno, más POP, la acción cayendo, IV
    Rank alta, spread ajustado y buen theta. No exige todo por separado: busca el mejor equilibrio."""
    subs = candidate_subscores(features, volatile, settings)
    weights = [getattr(settings, SCORE_WEIGHT_FIELD[d]) for d in SCORE_DIMENSIONS]
    total_w = sum(weights) or 1.0
    score = sum(weights[i] * subs[SCORE_DIMENSIONS[i]] for i in range(len(SCORE_DIMENSIONS))) / total_w
    return round(score, 4)


def delta_in_band(delta: float, volatile: bool, settings: SimulatorSettings) -> bool:
    """|delta| dentro de target ± band (criterio 9: 0.15 muy volátil, 0.20 menos)."""
    d = abs(delta)
    if d >= _DELTA_SENTINEL:
        return False  # centinela -999 de Schwab
    target = delta_target(volatile, settings)
    return (target - settings.delta_band) <= d <= (target + settings.delta_band)


def required_coverage_pct(
    volatile: bool,
    near_support: bool,
    sma200_far_above: bool,
    has_event: bool,
    settings: SimulatorSettings,
) -> float:
    """Cobertura mínima dinámica ("un poco de todo", usuario 2026-08-03):
    base 8% (poco volátil) / 10% (volátil); si NO está cerca de un soporte fuerte, 12%+; si la
    SMA200 está muy por encima, al menos 9%; si hay Fed/earnings antes del vencimiento, +2%
    (entrar igual, aprovechando la vola, pero con más colchón)."""
    cov = settings.coverage_volatile if volatile else settings.coverage_normal
    if not near_support:
        cov = max(cov, settings.coverage_far_from_support)
    if sma200_far_above:
        cov = max(cov, settings.coverage_sma200_far_above)
    if has_event:
        cov += settings.event_coverage_bump
    return round(cov, 4)


# "Cualquier ganancia": objetivo ínfimo (0.01% de la prima) para que cierre con CUALQUIER ganancia
# real (>0) pero NUNCA en pérdida (un P&L negativo nunca supera esto). Usado en la excepción de
# "cerca del strike y faltando poco al vencimiento" (usuario 2026-08-05: "cerrar pero no en pérdidas").
ANY_PROFIT_TARGET = 0.0001


def tiered_profit_target(
    coverage_now: float, dte: int, age_days: int, important_news: bool, settings: SimulatorSettings
) -> tuple[float, bool]:
    """Objetivo de ganancia ESCALONADO por ANTIGÜEDAD de la operación (regla del usuario 2026-08-12),
    como fracción de la prima cobrada, y si hay que forzar el cierre. Aplica IGUAL al simulador y al
    Real Market (venta de puts):
      · días 0 a (profit_age_step2_days-1): profit_target_pct (30%) — desde el día 0.
      · días profit_age_step2_days a (profit_age_step3_days-1): profit_target_step2_pct (35%).
      · profit_age_step3_days o más: profit_target_step3_pct (40%).
    Con los defaults: 30% (días 0-2), 35% (días 3-6), 40% (día 7+). `coverage_now`/`dte` ya no entran en
    el objetivo (el esquema anterior de semana1/semana2 + 45% cerca de vencimiento fue reemplazado). La
    ÚNICA salvaguarda que sigue acá es la NOTICIA importante muy cerca del vencimiento → cierre forzado
    (el stop-loss y el cierre por DTE mínimo se evalúan aparte, en evaluate_put_close)."""
    base = settings.profit_target_pct

    # Salvaguarda: noticia importante muy cerca del vencimiento → cerrar ya (protección, no es ganancia).
    if settings.near_exp_dte > 0 and dte <= settings.near_exp_dte and important_news:
        return base, True

    step2_days = getattr(settings, "profit_age_step2_days", 3)
    step3_days = getattr(settings, "profit_age_step3_days", 7)
    step2_pct = getattr(settings, "profit_target_step2_pct", 0.35) or base
    step3_pct = getattr(settings, "profit_target_step3_pct", 0.40) or base

    if step3_days > 0 and age_days >= step3_days:
        return step3_pct, False        # día 7+ → 40%
    if step2_days > 0 and age_days >= step2_days:
        return step2_pct, False        # días 3-6 → 35%
    return base, False                 # días 0-2 → 30%


def resample_monthly(price_history: list[PriceBar]) -> list[PriceBar]:
    """Resamplea barras diarias a mensuales (OHLC) para buscar soporte mensual además del diario
    (usuario 2026-08-03: "soporte en diario y mensual"). Agrupa por (año, mes)."""
    buckets: dict[tuple[int, int], list[PriceBar]] = {}
    for bar in price_history:
        buckets.setdefault((bar.trade_date.year, bar.trade_date.month), []).append(bar)
    monthly: list[PriceBar] = []
    for (year, month), bars in sorted(buckets.items()):
        bars_sorted = sorted(bars, key=lambda b: b.trade_date)
        monthly.append(
            PriceBar(
                symbol=bars_sorted[0].symbol,
                trade_date=date(year, month, 1),
                open=bars_sorted[0].open,
                high=max(b.high for b in bars_sorted),
                low=min(b.low for b in bars_sorted),
                close=bars_sorted[-1].close,
                volume=sum(b.volume for b in bars_sorted),
            )
        )
    return monthly
