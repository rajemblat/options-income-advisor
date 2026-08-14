from __future__ import annotations

import json
import logging
import sqlite3

from options_advisor.config import IntradayButterflySettings, IntradayCondorSettings, SimulatorSettings
from options_advisor.simulator import rules
from options_advisor.storage import repository as repo

logger = logging.getLogger(__name__)

# Las dimensiones del "cerebro" que el aprendizaje puede reponderar — MISMA fuente que el scoring
# (rules), para que scoring y aprendizaje nunca se desincronicen.
_DIMENSIONS = rules.SCORE_DIMENSIONS
_WEIGHT_FIELD = rules.SCORE_WEIGHT_FIELD

# Modo MIXTO (usuario 2026-08): un cambio de peso ≤ AUTO_CAP se aplica solo; mayor, se propone.
DEFAULT_MIN_EXAMPLES = 8
DEFAULT_MAX_STEP = 0.10       # paso máximo por revisión para la dimensión de señal más fuerte
DEFAULT_AUTO_CAP = 0.05       # hasta acá lo aplica solo; más grande → propuesta para aprobar
# Salvaguarda: aunque cada paso sea chico, si el peso ya se alejó más de esto de TU base, el
# siguiente cambio pasa a ser PROPUESTA (para que la deriva acumulada grande la apruebes vos).
CUMULATIVE_AUTO_BOUND = 0.15
_WEIGHT_MIN, _WEIGHT_MAX = 0.02, 0.60

# Peso relativo de tu feedback (👍/👎) vs. el resultado real (P&L) al armar la etiqueta de calidad.
FEEDBACK_WEIGHT = 0.7
OUTCOME_WEIGHT = 0.3


def _quality_label(example: sqlite3.Row) -> float | None:
    """Etiqueta de "qué tan buena" fue una decisión, en [-1, 1], combinando TU feedback (pesa más)
    y el RESULTADO real (usuario 2026-08: las dos cosas). None si todavía no hay ninguna señal."""
    fb = {"good": 1.0, "bad": -1.0}.get(example["user_feedback"])
    outcome = None
    if example["position_status"] == "closed" and example["realized_pnl"] is not None:
        pnl = example["realized_pnl"]
        outcome = 1.0 if pnl > 0 else (-1.0 if pnl < 0 else 0.0)
    if fb is not None and outcome is not None:
        return FEEDBACK_WEIGHT * fb + OUTCOME_WEIGHT * outcome
    if fb is not None:
        return fb
    return outcome


def _subscores(ctx: dict, settings: SimulatorSettings) -> dict[str, float]:
    """Sub-puntajes (0-1) del cerebro a partir de las features guardadas en la decisión, usando la
    MISMA función que el scoring (rules.candidate_subscores) para no desincronizarse."""
    features = {
        "delta_abs": abs(ctx["chosen_delta"]) if isinstance(ctx.get("chosen_delta"), (int, float)) else None,
        "coverage": ctx.get("chosen_coverage_pct"),
        "annualized": ctx.get("chosen_annualized_return"),
        "day_change_pct": ctx.get("day_change_pct"),
        "iv_rank": ctx.get("iv_rank"),
        "spread_pct": ctx.get("chosen_spread_pct"),
        "theta": ctx.get("chosen_theta"),
        "premium": ctx.get("chosen_credit"),
    }
    return rules.candidate_subscores(features, bool(ctx.get("volatile")), settings)


def _insights(raw_feats: list[tuple[dict, float]]) -> list[str]:
    """Patrones EN PALABRAS (usuario 2026-08): compara el promedio de features entre las
    operaciones buenas y las malas y describe las diferencias notables, en castellano simple."""
    good = [ctx for ctx, q in raw_feats if q > 0]
    bad = [ctx for ctx, q in raw_feats if q < 0]
    if not good or not bad:
        return []

    def avg(rows, key, transform=lambda v: v):
        vals = [transform(r[key]) for r in rows if isinstance(r.get(key), (int, float))]
        return sum(vals) / len(vals) if vals else None

    out = []
    # (key, etiqueta, formato, transform, umbral_de_diferencia_notable)
    specs = [
        ("iv_rank", "IV Rank", lambda v: f"{v:.0f}", (lambda v: v), 8),
        ("chosen_coverage_pct", "cobertura", lambda v: f"{v*100:.1f}%", (lambda v: v), 0.02),
        ("chosen_annualized_return", "retorno anualizado", lambda v: f"{v*100:.0f}%", (lambda v: v), 0.15),
        ("day_change_pct", "variación del día", lambda v: f"{v:+.1f}%", (lambda v: v), 0.8),
        ("chosen_delta", "delta", lambda v: f"{abs(v):.2f}", abs, 0.04),
    ]
    for key, label, fmt, tr, thresh in specs:
        g, b = avg(good, key, tr), avg(bad, key, tr)
        if g is None or b is None or abs(g - b) < thresh:
            continue
        mas_menos = "más" if g > b else "menos"
        out.append(f"Las que resultaron BUENAS tenían {mas_menos} {label} ({fmt(g)}) que las malas ({fmt(b)}).")
    return out


def _covariance(pairs: list[tuple[float, float]]) -> float:
    n = len(pairs)
    if n < 2:
        return 0.0
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    return sum((a - mx) * (b - my) for a, b in pairs) / n


def effective_weights(conn: sqlite3.Connection, settings: SimulatorSettings) -> dict[str, float]:
    """Pesos vigentes = base del settings + lo aprendido (learning_state). Es lo que el cerebro
    debe usar de verdad."""
    state = repo.get_learning_state(conn)
    out = {}
    for dim in _DIMENSIONS:
        field = _WEIGHT_FIELD[dim]
        out[field] = state.get(f"weight.{field}", getattr(settings, field))
    return out


BROKER_MARGIN_FACTOR_KEY = "sim.broker_margin_factor"


def effective_broker_margin_factor(conn: sqlite3.Connection, settings: SimulatorSettings) -> float:
    """Factor de calibración vigente = el aprendido del broker (si lo hay) o el base del settings."""
    state = repo.get_learning_state(conn)
    return state.get(BROKER_MARGIN_FACTOR_KEY, getattr(settings, "broker_margin_factor", 1.0))


def load_effective_simulator(conn: sqlite3.Connection, settings: SimulatorSettings) -> SimulatorSettings:
    """Copia del SimulatorSettings con los pesos aprendidos aplicados — lo que el scheduler pasa al
    evaluar entradas (Etapa 3: aplicar lo aprendido). Incluye el factor de margen calibrado al broker."""
    try:
        update = effective_weights(conn, settings)
    except Exception:
        logger.debug("Aprendizaje: no se pudieron cargar los pesos aprendidos; se usa el base", exc_info=True)
        return settings
    try:
        update["broker_margin_factor"] = effective_broker_margin_factor(conn, settings)
    except Exception:
        logger.debug("Aprendizaje: no se pudo cargar el factor de margen; se usa el base", exc_info=True)
    return settings.model_copy(update=update)


# Calibración del margen al broker REAL (usuario 2026-08-06): el maintenanceRequirement que Schwab
# reporta de TUS posiciones cortas de put nos dice el colateral verdadero. Sacamos el factor
# (margen_real ÷ Reg-T) mediano y lo guardamos, para que el simulador estime como tu cuenta.
MARGIN_CALIB_MIN_POSITIONS = 2
_MARGIN_FACTOR_MIN, _MARGIN_FACTOR_MAX = 0.05, 1.5


def calibrate_broker_margin_factor(conn: sqlite3.Connection, broker, settings: SimulatorSettings) -> dict:
    """Aprende el factor margen-real÷Reg-T desde tus posiciones REALES de put corto (Schwab reporta
    `maintenance_requirement`). Guarda el mediano en learning_state para que el colateral y el
    anualizado del robot coincidan con tu broker. No cambia nada si no hay suficientes datos."""
    try:
        positions = broker.get_all_positions()
    except Exception:
        logger.debug("Calibración de margen: no se pudieron leer las posiciones reales", exc_info=True)
        return {"calibrated": False, "reason": "sin acceso a posiciones", "n": 0}

    factors = []
    quote_cache: dict = {}
    for p in positions:
        if p.asset_type != "OPTION" or p.option_type != "put" or p.quantity >= 0:
            continue  # solo puts CORTOS (quantity negativa)
        if not p.maintenance_requirement or not p.strike or not p.underlying_symbol:
            continue
        contracts = abs(p.quantity)
        if contracts <= 0:
            continue
        maint_per_contract = p.maintenance_requirement / contracts
        under = quote_cache.get(p.underlying_symbol)
        if under is None:
            try:
                under = broker.get_quote(p.underlying_symbol).last_price
                quote_cache[p.underlying_symbol] = under
            except Exception:
                continue
        if not under or under <= 0:
            continue
        regt = rules.naked_put_margin(under, p.strike, abs(p.average_price))
        if regt <= 0:
            continue
        f = maint_per_contract / regt
        if _MARGIN_FACTOR_MIN <= f <= _MARGIN_FACTOR_MAX:
            factors.append(f)

    n = len(factors)
    if n < MARGIN_CALIB_MIN_POSITIONS:
        return {"calibrated": False, "reason": f"solo {n} put(s) corto(s) real(es) con margen", "n": n}
    factors.sort()
    median = factors[n // 2] if n % 2 == 1 else (factors[n // 2 - 1] + factors[n // 2]) / 2
    median = round(median, 4)
    repo.set_learning_value(conn, BROKER_MARGIN_FACTOR_KEY, median)
    logger.info("Calibración de margen: factor broker/Reg-T = %.3f (de %d put(s) corto(s) real(es))", median, n)
    return {"calibrated": True, "factor": median, "n": n}


def _state_key_for_param(param: str) -> str:
    """Mapea el nombre del parámetro de una propuesta a su clave en learning_state. Los pesos del
    cerebro de puts van con prefijo 'weight.'; el umbral del butterfly con 'butterfly.'; las perillas
    del Iron Condor con 'condor.' (usuario 2026-08-14)."""
    if param == "distance_threshold_pct":
        return _BF_THRESHOLD_KEY
    for key, (field, *_rest) in _CD_BOUNDS.items():
        if param == field:
            return key
    return f"weight.{param}"


def apply_approved_proposal(conn: sqlite3.Connection, proposal_id: int) -> bool:
    """Aplica una propuesta aprobada: escribe el valor propuesto en learning_state. True si se aplicó."""
    p = repo.get_proposal(conn, proposal_id)
    if p is None or p["status"] != "pending":
        return False
    repo.set_learning_value(conn, _state_key_for_param(p["param"]), p["proposed_value"])
    repo.resolve_learning_proposal(conn, proposal_id, "approved")
    return True


def review(
    conn: sqlite3.Connection,
    settings: SimulatorSettings,
    *,
    min_examples: int = DEFAULT_MIN_EXAMPLES,
    max_step: float = DEFAULT_MAX_STEP,
    auto_cap: float = DEFAULT_AUTO_CAP,
) -> dict:
    """UNA revisión de aprendizaje (corre al cierre de cada día). Cruza cada apertura con su
    calidad (tu feedback + resultado), mide qué dimensiones del cerebro predicen las BUENAS, y
    mueve los pesos hacia ellas: los cambios chicos (≤ auto_cap) se aplican solos; los grandes se
    dejan como propuesta para que apruebes (modo mixto). Devuelve un informe."""
    examples = repo.get_learning_examples(conn)
    labeled = []
    raw_feats: list[tuple[dict, float]] = []
    for ex in examples:
        try:
            ctx = json.loads(ex["context_json"]) if ex["context_json"] else {}
        except (ValueError, TypeError):
            ctx = {}
        q = _quality_label(ex)
        if q is None:
            continue
        labeled.append((_subscores(ctx, settings), q))
        raw_feats.append((ctx, q))

    n = len(labeled)
    if n < min_examples:
        summary = f"Todavía no hay suficientes datos para aprender: {n}/{min_examples} operaciones con señal (feedback o resultado). Segui marcando 👍/👎 y dejá que cierren operaciones."
        repo.insert_learning_report(conn, n, summary, json.dumps({"n": n, "min": min_examples}))
        return {"applied": [], "proposed": [], "examples": n, "summary": summary, "enough": False}

    # Covarianza entre el sub-puntaje de cada dimensión y la calidad → señal de reponderación.
    signals = {}
    for dim in _DIMENSIONS:
        signals[dim] = _covariance([(sub[dim], q) for sub, q in labeled])
    max_abs = max((abs(v) for v in signals.values()), default=0.0)

    applied, proposed, detail = [], [], {}
    weights_now = effective_weights(conn, settings)
    for dim in _DIMENSIONS:
        field = _WEIGHT_FIELD[dim]
        norm = (signals[dim] / max_abs) if max_abs > 0 else 0.0     # [-1, 1]
        delta = round(max_step * norm, 4)
        current = weights_now[field]
        proposed_val = round(min(_WEIGHT_MAX, max(_WEIGHT_MIN, current + delta)), 4)
        change = round(proposed_val - current, 4)
        detail[dim] = {"signal": round(signals[dim], 5), "current": current, "proposed": proposed_val, "change": change}
        if abs(change) < 1e-4:
            continue
        direction = "subir" if change > 0 else "bajar"
        reason = (
            f"Las operaciones buenas venían con {'mejor' if change > 0 else 'peor'} '{dim}' — conviene {direction} su peso "
            f"de {current:.2f} a {proposed_val:.2f}."
        )
        base = getattr(settings, field)
        drifted_far = abs(proposed_val - base) > CUMULATIVE_AUTO_BOUND
        if abs(change) <= auto_cap and not drifted_far:
            repo.set_learning_value(conn, f"weight.{field}", proposed_val)
            applied.append({"param": field, "from": current, "to": proposed_val, "reason": reason})
        else:
            if not repo.has_pending_proposal_for(conn, field):
                repo.insert_learning_proposal(conn, field, current, proposed_val, reason)
            proposed.append({"param": field, "from": current, "to": proposed_val, "reason": reason})

    n_pos = sum(1 for _, q in labeled if q > 0)
    n_neg = sum(1 for _, q in labeled if q < 0)
    parts = []
    if applied:
        parts.append(f"apliqué solo {len(applied)} ajuste(s) chico(s)")
    if proposed:
        parts.append(f"dejé {len(proposed)} propuesta(s) para que apruebes")
    if not parts:
        parts.append("no hubo cambios que valga la pena hacer")
    insights = _insights(raw_feats)
    summary = (
        f"Aprendí de {n} operaciones con señal ({n_pos} buenas, {n_neg} malas): " + ", ".join(parts) + "."
    )
    if insights:
        summary += " Patrones que veo: " + " ".join(insights)
    repo.insert_learning_report(conn, n, summary, json.dumps({"detail": detail, "n_pos": n_pos, "n_neg": n_neg, "insights": insights}, default=str))
    return {"applied": applied, "proposed": proposed, "examples": n, "summary": summary, "insights": insights, "enough": True}


# ---------------- Iron Butterfly (Estrategia 2) ----------------
# El knob más impactante y seguro de aprender es la DISTANCIA de entrada a la SMA8: si las
# operaciones buenas venían entrando más lejos de la media, conviene exigir más distancia (menos
# entradas pero mejores); si venían más cerca, aflojar. Un solo parámetro, interpretable.

BUTTERFLY_MIN_EXAMPLES = 8
_BF_THRESHOLD_KEY = "butterfly.distance_threshold_pct"
_BF_MAX_STEP = 0.0005          # paso máximo por revisión (en fracción, ej. 0.05%)
_BF_AUTO_CAP = 0.0003          # ≤ esto se aplica solo; más grande → propuesta
_BF_MIN, _BF_MAX = 0.0005, 0.005


# ---------------- Voto por parámetro (usuario 2026-08-06) ----------------
# Cada casillero que el usuario vota 👍/👎 mapea a una dimensión del cerebro (si tiene una). El
# voto AGREGADO propone subir/bajar el peso de esa dimensión. Solo PROPONE (nunca aplica solo) para
# que el usuario lo apruebe y para no pelearse con el reponderado por covarianza. Los casilleros sin
# dimensión (strike, dte, soporte, etc.) se guardan igual pero no mueven pesos.
_PARAM_TO_WEIGHT_FIELD = {
    "delta": "score_weight_delta",
    "pop": "score_weight_pop",
    "cobertura": "score_weight_coverage",
    "anualizado": "score_weight_return",
    "prima": "score_weight_return",
    "dia_pct": "score_weight_day_change",
    "rsi": "score_weight_day_change",
    "iv_rank": "score_weight_iv_rank",
    "spread": "score_weight_liquidity",
    "open_interest": "score_weight_liquidity",
    "volumen": "score_weight_liquidity",
    "bid_ask": "score_weight_liquidity",
    "theta": "score_weight_theta",
}
_PARAM_VOTE_MIN = 3          # votos netos mínimos en una dimensión para proponer un cambio
_PARAM_STEP = 0.03           # paso de peso por propuesta (chico y acotado)


def review_param_feedback(conn: sqlite3.Connection, settings: SimulatorSettings) -> dict:
    """Cruza tus votos POR PARÁMETRO con los pesos del cerebro y PROPONE ajustes (usuario 2026-08-06:
    "que cada casillero votado ajuste cómo decide"). 👍 en un factor sube su peso; 👎 lo baja. Cada
    cambio queda como PROPUESTA para que la apruebes en la pestaña Aprendizaje — no se aplica solo."""
    rows = repo.get_decisions_with_param_feedback(conn)
    agg: dict[str, list] = {}   # weight_field -> [suma_neta, cantidad_de_votos]
    for r in rows:
        try:
            votes = json.loads(r["param_feedback_json"]) if r["param_feedback_json"] else {}
        except (ValueError, TypeError):
            votes = {}
        for pkey, v in votes.items():
            field = _PARAM_TO_WEIGHT_FIELD.get(pkey)
            if not field:
                continue
            val = {"good": 1.0, "bad": -1.0}.get(v)   # 'normal' → neutral, no cuenta
            if val is None:
                continue
            slot = agg.setdefault(field, [0.0, 0])
            slot[0] += val
            slot[1] += 1

    weights_now = effective_weights(conn, settings)
    proposed = []
    for field, (net, count) in agg.items():
        if count < _PARAM_VOTE_MIN or abs(net) < 1e-9:
            continue
        direction = 1.0 if net > 0 else -1.0
        current = weights_now.get(field, getattr(settings, field))
        proposed_val = round(min(_WEIGHT_MAX, max(_WEIGHT_MIN, current + direction * _PARAM_STEP)), 4)
        if abs(proposed_val - current) < 1e-4:
            continue
        signo = "subir" if direction > 0 else "bajar"
        pulgar = "👍" if net > 0 else "👎"
        reason = (
            f"Tu voto por parámetro: saldo {pulgar} en '{field}' ({int(count)} voto(s)) — conviene {signo} "
            f"su peso de {current:.2f} a {proposed_val:.2f}."
        )
        if not repo.has_pending_proposal_for(conn, field):
            repo.insert_learning_proposal(conn, field, current, proposed_val, reason)
        proposed.append({"param": field, "from": current, "to": proposed_val, "reason": reason})
    return {"proposed": proposed, "dimensions_with_votes": len(agg)}


def analyze_real_trade_profile(conn: sqlite3.Connection) -> dict:
    """Estudia TUS operaciones REALES (las que abriste en tu broker y se detectaron en Operaciones)
    para aprender tu ESTILO: tickers favoritos, cobertura típica, DTE, delta implícita (≈ 1 − POP),
    anualizado y mezcla de estrategias (usuario 2026-08-05: "que los robots aprendan cómo opero").
    Solo cuenta ventas de opción con datos completos (excluye rolls e incompletas). No cambia nada
    por sí solo — es la base para que el robot (y vos) vean tu patrón y decidan alinearlo."""
    try:
        rows = repo.get_real_trade_alerts(conn, limit=1000)
    except Exception:
        return {"n": 0, "enough": False}

    from collections import Counter
    coverages: list[float] = []
    dtes: list[int] = []
    pops: list[float] = []
    annuals: list[float] = []
    contracts: list[int] = []
    symbols: Counter = Counter()
    strategies: Counter = Counter()
    puts = calls = 0

    for r in rows:
        strat = r["strategy_type"]
        if strat == "roll_closed_leg":
            continue  # un roll no es una entrada nueva
        strike = r["strike"]
        under = r["underlying_price"]
        symbols[r["symbol"]] += 1
        strategies[strat] += 1
        if r["option_type"] == "put":
            puts += 1
        elif r["option_type"] == "call":
            calls += 1
        if isinstance(under, (int, float)) and under and isinstance(strike, (int, float)):
            # cobertura = distancia OTM del strike (put: cae; call: sube). Siempre en positivo si está OTM.
            cov = (under - strike) / under if r["option_type"] == "put" else (strike - under) / under
            coverages.append(cov * 100)
        if isinstance(r["dte"], (int, float)):
            dtes.append(int(r["dte"]))
        if isinstance(r["probability_of_profit"], (int, float)):
            pops.append(r["probability_of_profit"])
        if isinstance(r["annualized_return_pct"], (int, float)):
            annuals.append(r["annualized_return_pct"])
        if isinstance(r["quantity"], (int, float)):
            contracts.append(int(r["quantity"]))

    n = sum(strategies.values())

    def _avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    def _med(xs):
        if not xs:
            return None
        s = sorted(xs)
        m = len(s) // 2
        return round((s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2), 2)

    avg_pop = _avg(pops)
    return {
        "n": n,
        "enough": n >= 5,
        "top_symbols": symbols.most_common(5),
        "avg_coverage_pct": _avg(coverages),
        "median_coverage_pct": _med(coverages),
        "avg_dte": _avg(dtes),
        "median_dte": _med(dtes),
        "avg_pop": avg_pop,
        "implied_delta": round(1 - avg_pop, 2) if isinstance(avg_pop, (int, float)) else None,
        "avg_annualized_pct": _avg(annuals),
        "avg_contracts": _avg(contracts),
        "put_pct": round(puts / (puts + calls) * 100, 0) if (puts + calls) else None,
        "strategies": dict(strategies),
    }


def real_trade_profile_summary(profile: dict) -> str:
    """Resumen en una línea del perfil real, para logs / reporte de aprendizaje."""
    if not profile.get("n"):
        return "Sin operaciones reales todavía para aprender tu estilo."
    favs = ", ".join(f"{s}×{c}" for s, c in profile.get("top_symbols", [])) or "—"
    return (
        f"Tu estilo real ({profile['n']} operaciones): favoritos {favs}; "
        f"cobertura ~{profile.get('median_coverage_pct')}%; DTE ~{profile.get('median_dte')}; "
        f"delta implícita ~{profile.get('implied_delta')}; anualizado ~{profile.get('avg_annualized_pct')}%."
    )


def effective_butterfly(conn: sqlite3.Connection, cfg: IntradayButterflySettings) -> IntradayButterflySettings:
    """Copia del IntradayButterflySettings con el umbral de distancia aprendido aplicado (Etapa 3)."""
    try:
        state = repo.get_learning_state(conn)
    except Exception:
        return cfg
    learned = state.get(_BF_THRESHOLD_KEY)
    if learned is None:
        return cfg
    return cfg.model_copy(update={"distance_threshold_pct": learned})


def review_butterfly(
    conn: sqlite3.Connection,
    cfg: IntradayButterflySettings,
    *,
    min_examples: int = BUTTERFLY_MIN_EXAMPLES,
    max_step: float = _BF_MAX_STEP,
    auto_cap: float = _BF_AUTO_CAP,
) -> dict:
    """Aprende la distancia de entrada ideal del Iron Butterfly cruzando cada apertura (distancia a
    la SMA) con su calidad (tu feedback + resultado). Mueve el umbral hacia donde estaban las
    BUENAS. Chico → se aplica solo; grande → propuesta (modo mixto)."""
    examples = repo.get_butterfly_learning_examples(conn)
    labeled = []  # (abs_distance, quality)
    for ex in examples:
        try:
            ctx = json.loads(ex["context_json"]) if ex["context_json"] else {}
        except (ValueError, TypeError):
            ctx = {}
        dist = ctx.get("distance_pct")
        if not isinstance(dist, (int, float)):
            continue
        q = _quality_label(ex)
        if q is None:
            continue
        labeled.append((abs(dist), q))

    n = len(labeled)
    if n < min_examples:
        summary = f"Iron Butterfly: todavía sin datos suficientes ({n}/{min_examples} operaciones con señal)."
        repo.insert_learning_report(conn, n, summary, json.dumps({"strategy": "iron_butterfly", "n": n}))
        return {"applied": [], "proposed": [], "examples": n, "summary": summary, "enough": False}

    good = [d for d, q in labeled if q > 0]
    bad = [d for d, q in labeled if q < 0]
    if not good or not bad:
        summary = f"Iron Butterfly: aprendí de {n} operaciones, pero todavía no hay buenas Y malas para comparar."
        repo.insert_learning_report(conn, n, summary, json.dumps({"strategy": "iron_butterfly", "n": n}))
        return {"applied": [], "proposed": [], "examples": n, "summary": summary, "enough": True}

    avg_good = sum(good) / len(good)
    avg_bad = sum(bad) / len(bad)
    current = repo.get_learning_state(conn).get(_BF_THRESHOLD_KEY, cfg.distance_threshold_pct)
    # Mover el umbral hacia la distancia media de las buenas (acotado por max_step).
    raw_delta = avg_good - current
    delta = round(max(-max_step, min(max_step, raw_delta)), 6)
    proposed_val = round(min(_BF_MAX, max(_BF_MIN, current + delta)), 6)
    change = round(proposed_val - current, 6)

    applied, proposed = [], []
    if abs(change) >= 1e-6:
        direction = "exigir MÁS distancia" if change > 0 else "aflojar la distancia"
        reason = (
            f"Las operaciones buenas entraban a {avg_good:.3%} de la SMA y las malas a {avg_bad:.3%}; "
            f"conviene {direction}: umbral {current:.3%} → {proposed_val:.3%}."
        )
        if abs(change) <= auto_cap:
            repo.set_learning_value(conn, _BF_THRESHOLD_KEY, proposed_val)
            applied.append({"param": "distance_threshold_pct", "from": current, "to": proposed_val, "reason": reason})
        elif not repo.has_pending_proposal_for(conn, "distance_threshold_pct"):
            repo.insert_learning_proposal(conn, "distance_threshold_pct", current, proposed_val, reason)
            proposed.append({"param": "distance_threshold_pct", "from": current, "to": proposed_val, "reason": reason})

    n_pos, n_neg = len(good), len(bad)
    parts = []
    if applied:
        parts.append("ajusté solo el umbral de entrada")
    if proposed:
        parts.append("dejé una propuesta de umbral para aprobar")
    if not parts:
        parts.append("no hizo falta cambiar el umbral")
    summary = f"Iron Butterfly: aprendí de {n} operaciones ({n_pos} buenas, {n_neg} malas): " + ", ".join(parts) + "."
    repo.insert_learning_report(conn, n, summary, json.dumps(
        {"strategy": "iron_butterfly", "avg_good": avg_good, "avg_bad": avg_bad, "current": current, "proposed": proposed_val}, default=str))
    return {"applied": applied, "proposed": proposed, "examples": n, "summary": summary, "enough": True}


# ============================ Aprendizaje del IRON CONDOR (usuario 2026-08-14) ============================
# "Que el robot aprenda de las operaciones y se haga experto, sepa qué hacer y qué no volver a hacer."
#
# Cómo aprende: cada APERTURA de condor queda registrada con sus features (delta real de los cortos,
# rango intradía del día, crédito cobrado, VIX y su variación). Cuando esa posición CIERRA, se le pega
# una etiqueta de calidad con la MISMA función que el resto del aprendizaje (`_quality_label`: tu
# feedback pesa 0.7 y el resultado real 0.3). Después, por cada perilla, se compara el promedio de la
# feature en las BUENAS contra el de las MALAS y se mueve el parámetro hacia donde estaban las buenas,
# con un paso acotado.
#
# Decisiones del usuario (2026-08-14):
#   · perillas: delta de los cortos, objetivo de ganancia, día calmo, stop-loss, y además crédito
#     mínimo ("primas altas") y VIX en suba;
#   · modo MIXTO: cambio chico se aplica solo, grande queda como propuesta para aprobar;
#   · aprende de papel Y real juntos;
#   · no toca NADA hasta tener 20 operaciones cerradas con señal.
#
# Salvaguarda propia del condor: el STOP-LOSS solo se auto-aplica cuando el cambio lo hace MÁS
# ESTRICTO (stop más chico = menos riesgo). Aflojar el stop siempre pasa por tu aprobación, por más
# chico que sea el paso — es tu límite de riesgo, no un parámetro más.

CONDOR_MIN_EXAMPLES = 20

_CD_PREFIX = "condor."
_CD_DELTA_KEY = _CD_PREFIX + "short_delta_max"
_CD_CALM_KEY = _CD_PREFIX + "calm_range_pct"
_CD_CREDIT_KEY = _CD_PREFIX + "min_credit"
_CD_VIX_KEY = _CD_PREFIX + "max_vix_change_pct"
_CD_PROFIT_KEY = _CD_PREFIX + "profit_target_pct"
_CD_STOP_KEY = _CD_PREFIX + "stop_loss_dollars"

# (clave de estado, campo del settings, paso máximo por revisión, hasta dónde se aplica solo, mín, máx)
_CD_BOUNDS = {
    _CD_DELTA_KEY:  ("short_delta_max",     0.03,   0.015,   0.05,   0.30),
    _CD_CALM_KEY:   ("calm_range_pct",      0.001,  0.0005,  0.001,  0.012),
    _CD_CREDIT_KEY: ("min_credit",          25.0,   10.0,    0.0,    400.0),
    _CD_VIX_KEY:    ("max_vix_change_pct",  1.5,    0.75,    0.5,    15.0),
    _CD_PROFIT_KEY: ("profit_target_pct",   0.05,   0.025,   0.20,   0.80),
    _CD_STOP_KEY:   ("stop_loss_dollars",   25.0,   10.0,    25.0,   400.0),
}

# Cuando el stop-loss se dispara en esta fracción o más de las operaciones, el objetivo de ganancia
# está demasiado lejos: se cobra más tarde y da tiempo a que el mercado se dé vuelta.
_CD_STOP_RATE_HIGH = 0.30
# Si NUNCA hubo stop y todas cerraron por objetivo, hay margen para pedir un poco más de ganancia.
_CD_STOP_RATE_LOW = 0.05


def effective_condor(conn: sqlite3.Connection, cfg: IntradayCondorSettings) -> IntradayCondorSettings:
    """Copia del IntradayCondorSettings con las perillas APRENDIDAS aplicadas. La usan los DOS
    motores (papel y real) para que sigan siendo el mismo cerebro: lo que aprende operando en papel
    se aplica igual cuando opera con plata real."""
    try:
        state = repo.get_learning_state(conn)
    except Exception:
        return cfg
    update = {}
    for key, (field, *_rest) in _CD_BOUNDS.items():
        learned = state.get(key)
        if learned is not None:
            update[field] = learned
    if not update:
        return cfg
    return cfg.model_copy(update=update)


def _cd_feature(ctx: dict, name: str) -> float | None:
    v = ctx.get(name)
    return float(v) if isinstance(v, (int, float)) else None


def _cd_move(conn, key: str, current: float, target: float, reason_tpl: str,
             *, auto_only_if_tighter: bool = False, tighter_is_lower: bool = True) -> tuple[list, list]:
    """Mueve una perilla hacia `target` con paso acotado. Devuelve (aplicados, propuestos).
    `auto_only_if_tighter`: si el cambio afloja el parámetro, nunca se aplica solo — va a propuesta."""
    field, max_step, auto_cap, lo, hi = _CD_BOUNDS[key]
    delta = max(-max_step, min(max_step, target - current))
    proposed_val = round(min(hi, max(lo, current + delta)), 6)
    change = round(proposed_val - current, 6)
    if abs(change) < 1e-9:
        return [], []
    reason = reason_tpl.format(current=current, proposed=proposed_val)
    afloja = (change > 0) if tighter_is_lower else (change < 0)
    puede_solo = abs(change) <= auto_cap and not (auto_only_if_tighter and afloja)
    entry = {"param": field, "from": current, "to": proposed_val, "reason": reason}
    if puede_solo:
        repo.set_learning_value(conn, key, proposed_val)
        return [entry], []
    if not repo.has_pending_proposal_for(conn, field):
        repo.insert_learning_proposal(conn, field, current, proposed_val, reason)
        return [], [entry]
    return [], []


def review_condor(
    conn: sqlite3.Connection,
    cfg: IntradayCondorSettings,
    *,
    min_examples: int = CONDOR_MIN_EXAMPLES,
) -> dict:
    """Revisión diaria del Iron Condor: cruza cada apertura (papel Y real) con su resultado y ajusta
    las perillas. Devuelve un resumen legible para el dashboard y el log."""
    examples = repo.get_condor_learning_examples(conn)
    rows = []   # (features de la apertura, calidad, motivo de cierre)
    for ex in examples:
        try:
            ctx = json.loads(ex["context_json"]) if ex["context_json"] else {}
        except (ValueError, TypeError):
            continue
        q = _quality_label(ex)
        if q is None:
            continue
        rows.append((ctx, q, ex["close_reason"]))

    n = len(rows)
    state = repo.get_learning_state(conn)
    if n < min_examples:
        summary = (f"Iron Condor: todavía no toco nada — llevo {n} de las {min_examples} operaciones "
                   f"cerradas que pedí para tener una muestra confiable.")
        repo.insert_learning_report(conn, n, summary, json.dumps({"strategy": "iron_condor", "n": n}))
        return {"applied": [], "proposed": [], "examples": n, "summary": summary, "enough": False}

    good = [r for r in rows if r[1] > 0]
    bad = [r for r in rows if r[1] < 0]
    applied, proposed, aprendido = [], [], []

    # --- Perillas de ENTRADA: se aprenden comparando buenas contra malas ---
    if good and bad:
        for key, feat, texto in (
            (_CD_DELTA_KEY, "short_delta_avg",
             "Las buenas vendían a delta {g:.3f} y las malas a {b:.3f}: conviene {dir} el delta ({{current:.3f}} → {{proposed:.3f}})."),
            (_CD_CALM_KEY, "day_range_pct",
             "Las buenas entraban con el SPX moviéndose {g:.3%} en el día y las malas {b:.3%}: {dir} la exigencia de día calmo ({{current:.3%}} → {{proposed:.3%}})."),
            (_CD_CREDIT_KEY, "net_credit",
             "Las buenas cobraban ${g:,.0f} de prima y las malas ${b:,.0f}: {dir} el crédito mínimo (${{current:,.0f}} → ${{proposed:,.0f}})."),
        ):
            g = [f for r in good if (f := _cd_feature(r[0], feat)) is not None]
            b = [f for r in bad if (f := _cd_feature(r[0], feat)) is not None]
            if len(g) < 3 or len(b) < 3:
                continue   # sin datos suficientes de ESTA feature (ej. condors viejos sin delta guardado)
            avg_g, avg_b = sum(g) / len(g), sum(b) / len(b)
            field = _CD_BOUNDS[key][0]
            current = state.get(key, getattr(cfg, field, None))
            if current is None:
                current = avg_g
            # Para el crédito mínimo no se apunta al promedio de las buenas (dejaría fuera a la mitad),
            # sino a un 85% de ese promedio: filtra las primas claramente flacas sin cortar de más.
            target = avg_g * 0.85 if key == _CD_CREDIT_KEY else avg_g
            direccion = "subir" if target > current else "bajar"
            a, p = _cd_move(conn, key, float(current), float(target),
                            texto.format(g=avg_g, b=avg_b, dir=direccion))
            applied += a
            proposed += p
            if a or p:
                aprendido.append(field)

        # VIX en suba: no se apunta al promedio sino al PEOR VIX con el que una operación salió bien,
        # así el filtro nunca deja afuera un escenario que históricamente funcionó.
        gv = [f for r in good if (f := _cd_feature(r[0], "vix_change_pct")) is not None]
        bv = [f for r in bad if (f := _cd_feature(r[0], "vix_change_pct")) is not None]
        if len(gv) >= 3 and len(bv) >= 3:
            peor_buena, avg_mala = max(gv), sum(bv) / len(bv)
            if avg_mala > peor_buena:   # solo si las malas entraban con el VIX claramente más arriba
                current = state.get(_CD_VIX_KEY, cfg.max_vix_change_pct)
                current = float(current) if current is not None else _CD_BOUNDS[_CD_VIX_KEY][4]
                a, p = _cd_move(conn, _CD_VIX_KEY, current, peor_buena,
                                f"Las malas entraban con el VIX subiendo {avg_mala:+.2f}% y la peor de las buenas "
                                f"soportó {peor_buena:+.2f}%: no entrar con el VIX subiendo más que eso "
                                "({current:+.2f}% → {proposed:+.2f}%).",
                                auto_only_if_tighter=True)
                applied += a
                proposed += p
                if a or p:
                    aprendido.append("max_vix_change_pct")

    # --- Perillas de SALIDA: se aprenden de CÓMO cerraron, no de cómo entraron ---
    stops = [r for r in rows if r[2] == "stop_loss"]
    stop_rate = len(stops) / n
    cur_profit = float(state.get(_CD_PROFIT_KEY, cfg.profit_target_pct))
    if stop_rate >= _CD_STOP_RATE_HIGH:
        a, p = _cd_move(conn, _CD_PROFIT_KEY, cur_profit, cur_profit - _CD_BOUNDS[_CD_PROFIT_KEY][1],
                        f"Saltó el stop en {stop_rate:.0%} de las operaciones: el objetivo está lejos y da "
                        "tiempo a que el mercado se dé vuelta; cobrar antes ({current:.0%} → {proposed:.0%}).")
        applied += a
        proposed += p
        if a or p:
            aprendido.append("profit_target_pct")
    elif stop_rate <= _CD_STOP_RATE_LOW and len(good) >= min_examples // 2:
        a, p = _cd_move(conn, _CD_PROFIT_KEY, cur_profit, cur_profit + _CD_BOUNDS[_CD_PROFIT_KEY][1],
                        f"Casi no saltó el stop ({stop_rate:.0%}) y {len(good)} operaciones salieron bien: "
                        "hay margen para pedir un poco más de ganancia ({current:.0%} → {proposed:.0%}).")
        applied += a
        proposed += p
        if a or p:
            aprendido.append("profit_target_pct")

    # Stop-loss: SOLO se ajusta hacia abajo (más estricto) de forma automática. Aflojarlo, nunca solo.
    if stops:
        peor = min(r[1] for r in rows)   # solo para el texto: qué tan mal salieron las peores
        cur_stop = float(state.get(_CD_STOP_KEY, cfg.stop_loss_dollars))
        if stop_rate >= _CD_STOP_RATE_HIGH:
            a, p = _cd_move(conn, _CD_STOP_KEY, cur_stop, cur_stop - _CD_BOUNDS[_CD_STOP_KEY][1],
                            f"El stop se disparó en {stop_rate:.0%} de las operaciones (calidad peor "
                            f"{peor:+.2f}): cortar antes la pérdida (${{current:,.0f}} → ${{proposed:,.0f}}).",
                            auto_only_if_tighter=True)
            applied += a
            proposed += p
            if a or p:
                aprendido.append("stop_loss_dollars")

    partes = []
    if applied:
        partes.append(f"ajusté solo {len(applied)} perilla(s)")
    if proposed:
        partes.append(f"dejé {len(proposed)} propuesta(s) para que apruebes")
    if not partes:
        partes.append("no hizo falta cambiar nada")
    summary = (f"Iron Condor: aprendí de {n} operaciones ({len(good)} buenas, {len(bad)} malas, "
               f"stop en {stop_rate:.0%}): " + ", ".join(partes) + ".")
    repo.insert_learning_report(conn, n, summary, json.dumps(
        {"strategy": "iron_condor", "n": n, "good": len(good), "bad": len(bad),
         "stop_rate": round(stop_rate, 4), "params": aprendido,
         "applied": applied, "proposed": proposed}, default=str))
    return {"applied": applied, "proposed": proposed, "examples": n, "summary": summary,
            "enough": True, "stop_rate": stop_rate}
