from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from options_advisor.broker.models import OptionChain, OptionContract, PriceBar
from options_advisor.config import SimulatorSettings
from options_advisor.indicators import levels
from options_advisor.simulator import rules
from options_advisor.storage.models import IndicatorSnapshot

# Robot de Trading Automático (paper trading). Criterios de entrada COMBINADOS confirmados con el
# usuario 2026-08-03 (OptionsUp_Parametros_Venta_de_Puts.pdf + criterios detallados en chat):
# soporte fuerte en diario Y mensual, IV Rank alto (real), volatilidad "por IV", cobertura
# DINÁMICA según contexto (volatilidad, cercanía a soporte, SMA200, Fed/earnings), delta objetivo
# por volatilidad (0.15/0.20), VWAP, y filtros de liquidez/POP del PDF. Cada decisión queda
# registrada (simulator/engine.py) con los datos que la motivaron — semilla para la capa de IA.

# Umbral para "la SMA200 está muy por encima del precio" (usuario: en ese caso alcanza 9% de
# cobertura). Precio al menos este % por debajo de la SMA200.
_SMA200_FAR_ABOVE_PCT = 0.10


@dataclass
class EntryEvaluation:
    """Resultado de evaluar la entrada para un símbolo en un día dado. `reasons` acumula TODOS
    los criterios que fallaron (no solo el primero). `context` guarda los datos que decidieron
    (volatilidad, cobertura pedida/obtenida, delta, etc.) para el log de decisiones del robot."""

    symbol: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    contract: OptionContract | None = None
    premium: float | None = None
    context: dict = field(default_factory=dict)


def _daily_supports(price_history: list[PriceBar], current_price: float) -> list[float]:
    supports, _ = levels.find_strong_support_resistance(price_history, current_price)
    return supports


def _support_strike_ceiling(price_history: list[PriceBar], current_price: float, settings) -> float | None:
    """Techo de strike por SOPORTE (usuario 2026-08-10): el strike del put debe quedar en/por debajo de un
    soporte fuerte del `put_min_support_rank`-ésimo para abajo (más profundo → más cobertura). Entre esos,
    elige el MÁS FUERTE (más toques); a empate, el más profundo. None si está desactivado (rank<1) o no hay
    soportes. Si no hay tan profundos como el rank pedido, cae al soporte más profundo disponible."""
    rank = getattr(settings, "put_min_support_rank", 0) or 0
    if rank < 1:
        return None
    sups = levels.find_strong_supports_with_touches(price_history, current_price)  # cercano→profundo, con toques
    if not sups:
        return None
    deep = sups[rank - 1:]
    pool = deep if deep else [sups[-1]]
    best = max(pool, key=lambda lt: (lt[1], -lt[0]))  # más fuerte; a empate, el más profundo (menor precio)
    return best[0]


def _monthly_supports(price_history: list[PriceBar], current_price: float) -> list[float]:
    monthly = rules.resample_monthly(price_history)
    if len(monthly) < 3:
        return []
    supports, _ = levels.find_strong_support_resistance(monthly, current_price)
    return supports


def _within(current_price: float, level: float, max_pct: float) -> bool:
    """El precio está dentro de `max_pct` POR ENCIMA de un soporte (no cruzado por debajo)."""
    if current_price <= 0:
        return False
    return 0 <= (current_price - level) / current_price <= max_pct


def _check_iv_rank(snapshot: IndicatorSnapshot, settings: SimulatorSettings) -> tuple[bool, str | None]:
    if snapshot.iv_rank is None or snapshot.iv_rank <= settings.iv_rank_min:
        return False, f"IV Rank <= {settings.iv_rank_min} (actual: {snapshot.iv_rank})"
    if settings.require_real_iv_rank and snapshot.iv_rank_source != "implied_volatility":
        return False, "IV Rank viene del proxy de HV, no de IV real (no operar con datos incompletos)"
    return True, None


def _liquidity_ok(ct: OptionContract, settings: SimulatorSettings) -> bool:
    if settings.require_positive_bid and ct.bid <= 0:
        return False
    if ct.open_interest < settings.min_open_interest:
        return False
    if ct.volume < settings.min_contract_volume:
        return False
    mid = ct.mid_price
    if mid <= 0:
        return False
    if (ct.ask - ct.bid) / mid > settings.max_bid_ask_spread_pct:
        return False
    return True


def _delta_eligible(ct: OptionContract, volatile: bool, settings: SimulatorSettings) -> bool:
    if settings.use_dynamic_delta:
        return rules.delta_in_band(ct.greeks.delta, volatile, settings)
    d = abs(ct.greeks.delta)
    return settings.delta_min <= d <= settings.max_delta


def _select_put_scored(
    chain: OptionChain, as_of: date, volatile: bool, underlying_price: float, settings: SimulatorSettings,
    day_change_pct: float | None = None, iv_rank: float | None = None, strike_ceiling: float | None = None,
) -> tuple[OptionContract | None, str | None]:
    """Cerebro flexible: entre los puts con IV/liquidez válidas en la ventana de DTE, elige el de
    MAYOR puntaje (delta, cobertura, retorno, POP, cómo viene la acción, IV Rank, liquidez y theta).
    Abre si el mejor supera `min_entry_score`. Busca el mejor equilibrio, no exige todo por separado.
    `strike_ceiling` (soporte del 2º para abajo): descarta strikes por ENCIMA de ese soporte (más cobertura)."""
    min_dte, max_dte = settings.dte_range
    scored: list[tuple[OptionContract, float]] = []
    n_dte = 0
    n_over_ceiling = 0
    n_prima_baja = 0
    for ct in chain.contracts:
        if ct.option_type != "put":
            continue
        dte = (ct.expiration - as_of).days
        if not (min_dte <= dte <= max_dte):
            continue
        n_dte += 1
        if strike_ceiling is not None and ct.strike > strike_ceiling:
            n_over_ceiling += 1
            continue  # strike demasiado alto: quedaría pegado al 1er soporte (poca cobertura)
        if ct.implied_volatility <= 0:
            continue
        if abs(ct.greeks.delta) >= 1.5:
            continue  # centinela -999 de Schwab
        if settings.require_positive_bid and ct.bid <= 0:
            continue
        if not _liquidity_ok(ct, settings):
            continue  # liquidez sí es un requisito duro (poder operarlo), no una "cercanía al ideal"
        coverage = rules.put_coverage_pct(underlying_price, ct.strike)
        fill = ct.bid if settings.use_bid_ask_fills else ct.mid_price
        # PRIMA MÍNIMA PROPORCIONAL A LA EXPOSICIÓN — filtro DURO, no una dimensión más del puntaje.
        #
        # Va acá y no en el score a propósito (usuario 2026-09-04, tras el AAPL 250 por $26). El
        # puntaje busca equilibrio y compensa: en ese put, cobertura, POP y theta sacaron 1.000
        # perfecto JUSTAMENTE porque el strike estaba lejísimos, y taparon el 0.000 del delta. Una
        # prima que no paga la exposición no es "una dimensión floja que las otras compensan": es
        # motivo para no abrir. Si el techo de soporte deja solo strikes que no pagan, no se abre
        # nada — usuario: "si no paga nada no la abran".
        if settings.min_premium_pct_of_strike > 0 and fill < settings.min_premium_pct_of_strike * ct.strike:
            n_prima_baja += 1
            continue
        cost = rules.per_contract_cost(underlying_price, ct.strike, fill, settings)
        annualized = rules.annualized_return_on_cost(fill, cost, dte)  # sobre el margen naked
        features = {
            "delta_abs": abs(ct.greeks.delta), "coverage": coverage, "annualized": annualized,
            "day_change_pct": day_change_pct, "iv_rank": iv_rank,
            "spread_pct": (ct.ask - ct.bid) / ct.mid_price if ct.mid_price > 0 else None,
            "theta": ct.greeks.theta, "premium": ct.mid_price,
        }
        score = rules.score_put_candidate(features, volatile, settings)
        scored.append((ct, score))
    if not scored:
        _extra = f"; {n_over_ceiling} descartados por strike sobre el soporte objetivo" if n_over_ceiling else ""
        if n_prima_baja:
            _extra += (f"; {n_prima_baja} descartados por prima menor al "
                       f"{settings.min_premium_pct_of_strike:.1%} del strike (no paga la exposición)")
        return None, f"Sin puts con IV/liquidez válidas en la ventana de DTE (evaluados {n_dte}{_extra})"
    best_ct, best_score = max(scored, key=lambda x: x[1])
    if best_score < settings.min_entry_score:
        return None, f"Mejor puntaje {best_score:.2f} < mínimo {settings.min_entry_score:.2f} (nada suficientemente cerca del ideal)"
    return best_ct, None


def _select_put(
    chain: OptionChain,
    as_of: date,
    volatile: bool,
    required_coverage: float,
    underlying_price: float,
    settings: SimulatorSettings,
    day_change_pct: float | None = None,
    iv_rank: float | None = None,
    strike_ceiling: float | None = None,
) -> tuple[OptionContract | None, str | None]:
    """Filtra puts por DTE, delta objetivo, cobertura, liquidez, POP (~1-|delta|) y crédito
    mínimo; devuelve el de MAYOR crédito entre los que pasan todo (criterio 'mejor prima').
    Con `use_soft_scoring` usa el cerebro flexible (puntaje) en vez de gates rígidos.
    `strike_ceiling`: descarta strikes por encima del soporte objetivo (2º para abajo, más cobertura)."""
    if settings.use_soft_scoring:
        return _select_put_scored(chain, as_of, volatile, underlying_price, settings, day_change_pct, iv_rank, strike_ceiling)
    min_dte, max_dte = settings.dte_range
    eligible: list[tuple[OptionContract, float]] = []  # (contrato, retorno anualizado)
    # Embudo de diagnóstico: cuántos puts sobreviven cada filtro (para ver DÓNDE se cae a 0).
    f = {"dte": 0, "iv": 0, "delta": 0, "cobertura": 0, "liquidez": 0, "pop": 0, "credito": 0, "retorno": 0}
    for ct in chain.contracts:
        if ct.option_type != "put":
            continue
        dte = (ct.expiration - as_of).days
        if not (min_dte <= dte <= max_dte):
            continue
        f["dte"] += 1
        if strike_ceiling is not None and ct.strike > strike_ceiling:
            continue  # strike sobre el soporte objetivo (poca cobertura) — buscamos más profundo
        if ct.implied_volatility <= 0:
            continue  # dato de IV inválido/ausente (centinela) — no operar con datos incompletos
        f["iv"] += 1
        if not _delta_eligible(ct, volatile, settings):
            continue
        f["delta"] += 1
        if settings.require_coverage and rules.put_coverage_pct(underlying_price, ct.strike) < required_coverage:
            continue
        f["cobertura"] += 1
        if not _liquidity_ok(ct, settings):
            continue
        f["liquidez"] += 1
        if (1 - abs(ct.greeks.delta)) < settings.min_probability_otm:
            continue
        f["pop"] += 1
        fill = ct.bid if settings.use_bid_ask_fills else ct.mid_price
        if fill < settings.min_credit:
            continue
        # Mismo piso proporcional que el cerebro flexible: la prima tiene que pagar la exposición.
        if settings.min_premium_pct_of_strike > 0 and fill < settings.min_premium_pct_of_strike * ct.strike:
            continue
        f["credito"] += 1
        annualized = rules.annualized_return_on_collateral(fill, ct.strike, dte)
        if annualized < settings.min_annualized_return:
            continue
        f["retorno"] += 1
        eligible.append((ct, annualized))
    if not eligible:
        funnel = f"DTE:{f['dte']}→delta:{f['delta']}→cobertura:{f['cobertura']}→liquidez:{f['liquidez']}→POP:{f['pop']}→crédito:{f['credito']}→retorno≥{settings.min_annualized_return:.0%}:{f['retorno']}"
        return None, f"Ningún put pasó el filtro. Embudo: {funnel}"
    # Elige el de MAYOR retorno anualizado sobre la garantía — no el de mayor crédito absoluto:
    # así el robot prefiere el DTE/strike que mejor rinde el capital (usuario 2026-08-03).
    best_ct, _best_ann = max(eligible, key=lambda pair: pair[1])
    return best_ct, None


def evaluate_entry(
    symbol: str,
    snapshot: IndicatorSnapshot,
    chain: OptionChain,
    price_history: list[PriceBar],
    settings: SimulatorSettings,
    *,
    price_above_vwap: bool | None = None,
    has_macro_event: bool = False,
    day_change_pct: float | None = None,
) -> EntryEvaluation:
    """Evalúa la entrada combinada del robot para `symbol`. `price_above_vwap` lo calcula el
    engine con barras intradía (None = no disponible). `has_macro_event` = evento Fed próximo
    (del contexto macro). `day_change_pct` = variación del subyacente HOY, en % (del quote):
    no abre si sube más de `max_entry_day_change_pct` (usuario 2026-08-04 — no entrar caro en algo
    que viene volando; prefiere las que caen). `passed=True` solo si TODO se cumple."""
    reasons: list[str] = []
    price = snapshot.price

    # --- No entrar en algo que sube demasiado hoy (usuario 2026-08-04) ---
    if day_change_pct is not None and day_change_pct > settings.max_entry_day_change_pct:
        reasons.append(
            f"Sube {day_change_pct:+.1f}% hoy (máx {settings.max_entry_day_change_pct:+.1f}%) — "
            f"no entrar caro; se prefieren las que caen"
        )

    # --- Soporte fuerte: diario Y mensual ---
    daily_supports = _daily_supports(price_history, price)
    daily_strong = any(_within(price, s, settings.support_max_distance_pct) for s in daily_supports)
    if not daily_strong:
        reasons.append("Sin soporte fuerte diario cerca del precio")
    if settings.require_monthly_support:
        monthly_supports = _monthly_supports(price_history, price)
        if not any(_within(price, s, settings.monthly_support_max_distance_pct) for s in monthly_supports):
            reasons.append("Sin soporte fuerte mensual cerca del precio")

    # --- IV Rank alto y real ---
    iv_ok, iv_reason = _check_iv_rank(snapshot, settings)
    if not iv_ok:
        reasons.append(iv_reason)

    # --- RSI (opcional en la lógica combinada) ---
    if settings.require_rsi:
        lo, hi = settings.rsi_range
        if snapshot.rsi_14 is None or not (lo <= snapshot.rsi_14 <= hi):
            reasons.append(f"RSI fuera de {settings.rsi_range} (actual: {snapshot.rsi_14})")

    # --- VWAP: precio cerca/por encima ---
    if settings.require_price_above_vwap:
        if price_above_vwap is None:
            reasons.append("Sin dato de VWAP intradía (no operar con datos incompletos)")
        elif not price_above_vwap:
            reasons.append("Precio por debajo del VWAP")

    # --- Confirmación de tendencia (opcional; la SMA200 se usa sobre todo para cobertura) ---
    if settings.confirm_uptrend:
        trend_sma = {8: snapshot.sma_8, 20: snapshot.sma_20, 50: snapshot.sma_50, 200: snapshot.sma_200}.get(
            settings.trend_sma_period
        )
        if trend_sma is None or price < trend_sma:
            reasons.append(f"Precio por debajo de la SMA{settings.trend_sma_period} (tendencia no confirmada)")

    # --- Contexto para cobertura dinámica ---
    volatile = rules.is_volatile(snapshot.iv_atm, snapshot.hv_20d, settings)
    near_daily = any(_within(price, s, settings.near_support_pct) for s in daily_supports)
    sma200 = snapshot.sma_200
    near_sma200 = sma200 is not None and price > 0 and abs(price - sma200) / price <= settings.near_sma200_pct
    sma200_far_above = sma200 is not None and price > 0 and (sma200 - price) / price >= _SMA200_FAR_ABOVE_PCT
    near_support = near_daily or near_sma200
    _, max_dte = settings.dte_range
    earnings_before = (
        snapshot.next_earnings_date is not None
        and snapshot.next_earnings_date <= snapshot.snapshot_date + timedelta(days=max_dte)
    )
    has_event = has_macro_event or earnings_before
    required_coverage = rules.required_coverage_pct(volatile, near_support, sma200_far_above, has_event, settings)

    # Soporte fuerte en números concretos (usuario 2026-08-05: "que me diga los soportes en número
    # que usa para el análisis"): el más cercano al precio (el "piso" en que se apoya la entrada) y
    # la lista completa de soportes diarios fuertes detectados, para mostrarlos en la tarjeta.
    _near_daily = [s for s in daily_supports if _within(price, s, settings.support_max_distance_pct)]
    _support_pool = _near_daily or daily_supports
    support_used = min(_support_pool, key=lambda s: abs(price - s)) if _support_pool else None

    # Techo de strike por SOPORTE (usuario 2026-08-10): el strike debe quedar en/por debajo de un soporte
    # fuerte del 2º para abajo (más profundo → más cobertura). El más FUERTE entre esos.
    strike_ceiling = _support_strike_ceiling(price_history, price, settings)

    context = {
        "volatile": volatile,
        "iv_atm": snapshot.iv_atm,
        "iv_rank": snapshot.iv_rank,
        "near_support": near_support,
        "support_used": round(support_used, 2) if support_used is not None else None,
        "supports_daily": [round(s, 2) for s in sorted(daily_supports, reverse=True)],
        "support_strike_ceiling": round(strike_ceiling, 2) if strike_ceiling is not None else None,
        "sma200_far_above": sma200_far_above,
        "has_event": has_event,
        "required_coverage_pct": required_coverage,
        "delta_target": rules.delta_target(volatile, settings),
        "day_change_pct": day_change_pct,
    }

    contract, sel_reason = _select_put(chain, snapshot.snapshot_date, volatile, required_coverage, price, settings, day_change_pct, snapshot.iv_rank, strike_ceiling=strike_ceiling)
    if sel_reason:
        reasons.append(sel_reason)

    if reasons or contract is None:
        return EntryEvaluation(symbol=symbol, passed=False, reasons=reasons, context=context)

    # Griegos completos (theta incluido) + IV del contrato elegido — datos que la capa de IA de
    # la Fase 2 va a cruzar para optimizar entrada/salida (pedido del usuario 2026-08-03).
    context["chosen_strike"] = contract.strike
    context["chosen_delta"] = contract.greeks.delta
    context["chosen_theta"] = contract.greeks.theta
    context["chosen_gamma"] = contract.greeks.gamma
    context["chosen_vega"] = contract.greeks.vega
    context["chosen_iv"] = contract.implied_volatility
    context["chosen_credit"] = contract.mid_price
    context["chosen_coverage_pct"] = round(rules.put_coverage_pct(price, contract.strike), 4)
    chosen_dte = (contract.expiration - snapshot.snapshot_date).days
    context["chosen_dte"] = chosen_dte
    fill = contract.bid if settings.use_bid_ask_fills else contract.mid_price
    chosen_cost = rules.per_contract_cost(price, contract.strike, fill, settings)
    context["chosen_margin"] = round(chosen_cost, 2)
    context["chosen_annualized_return"] = round(rules.annualized_return_on_cost(fill, chosen_cost, chosen_dte), 4)
    # Datos completos de la posición para que el usuario pueda juzgarla y puntuarla (2026-08):
    # liquidez (bid/ask/spread/OI/volumen), probabilidad OTM, precio del subyacente, HV, RSI.
    context["underlying_price"] = round(price, 2)
    context["chosen_bid"] = contract.bid
    context["chosen_ask"] = contract.ask
    spread = contract.ask - contract.bid
    context["chosen_spread_pct"] = round(spread / contract.mid_price, 4) if contract.mid_price > 0 else None
    context["chosen_open_interest"] = contract.open_interest
    context["chosen_volume"] = contract.volume
    context["chosen_pop"] = round(1 - abs(contract.greeks.delta), 4)  # prob. de expirar OTM ≈ 1-|delta|
    context["hv_20d"] = snapshot.hv_20d
    context["rsi_14"] = snapshot.rsi_14
    return EntryEvaluation(
        symbol=symbol, passed=True, contract=contract, premium=contract.mid_price, context=context
    )
