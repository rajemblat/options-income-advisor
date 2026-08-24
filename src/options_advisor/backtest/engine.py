"""Motor de BACKTESTING histórico (usuario 2026-08-06: "el mejor robot de backtesting").

Reconstruye el comportamiento de las estrategias del robot sobre el histórico DIARIO real del
subyacente (barras de Schwab), valuando las opciones con Black-Scholes y una IV reconstruida desde
la volatilidad histórica (proxy). Es PURO y sin I/O: recibe barras y parámetros, devuelve trades y
estadísticas — se testea sin base ni red.

Honestidad sobre exactitud:
  · NAKED PUTS: riguroso. El precio del subyacente es REAL (histórico), el put se valúa con
    Black-Scholes; lo único estimado es la IV (proxy = HV × iv_mult, porque no hay IV histórica
    de la cadena). La ASIGNACIÓN en caídas se modela liquidando a valor intrínseco al vencimiento,
    así el backtest SÍ captura las pérdidas de un mercado en baja.
  · IRON CONDOR / BUTTERFLY (0DTE): APROXIMADOS. Schwab no guarda intradía de años atrás, así que
    se estiman día a día desde el rango diario (OHLC). Sirven de referencia, no son exactos.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from dataclasses import dataclass, field
from datetime import date, timedelta

from options_advisor.broker.models import PriceBar
from options_advisor.config import SimulatorSettings
from options_advisor.indicators import levels
from options_advisor.indicators.technical import compute_rsi
from options_advisor.indicators.volatility import compute_historical_volatility
from options_advisor.simulator import rules

RISK_FREE = 0.04
CONTRACT_MULTIPLIER = 100


# ----------------------------- Black-Scholes (put, sin dividendo) -----------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1(spot: float, strike: float, t: float, iv: float, r: float = RISK_FREE) -> float:
    return (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))


def bs_put_price(spot: float, strike: float, t_years: float, iv: float, r: float = RISK_FREE) -> float:
    """Precio de un put europeo (Black-Scholes, sin dividendo). t en años."""
    t = max(t_years, 1.0 / 365.0)
    iv = max(iv, 0.01)
    if spot <= 0 or strike <= 0:
        return 0.0
    d1 = _d1(spot, strike, t, iv, r)
    d2 = d1 - iv * math.sqrt(t)
    return max(0.0, strike * math.exp(-r * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1))


def bs_put_delta(spot: float, strike: float, t_years: float, iv: float, r: float = RISK_FREE) -> float:
    """Delta de un put (negativa, entre -1 y 0)."""
    t = max(t_years, 1.0 / 365.0)
    iv = max(iv, 0.01)
    if spot <= 0 or strike <= 0:
        return 0.0
    return _norm_cdf(_d1(spot, strike, t, iv, r)) - 1.0


def _strike_step(spot: float) -> float:
    if spot < 25:
        return 0.5
    if spot < 50:
        return 1.0
    if spot < 200:
        return 2.5
    return 5.0


def select_put_strike(spot: float, t_years: float, iv: float, target_delta: float) -> float | None:
    """Elige el strike OTM cuyo |delta| se acerca más al objetivo (como el robot en vivo), sobre una
    grilla de strikes realista según el precio."""
    step = _strike_step(spot)
    atm = round(spot / step) * step
    best = None
    best_diff = 1e9
    for i in range(0, 80):
        strike = atm - i * step
        if strike <= 0:
            break
        d = abs(bs_put_delta(spot, strike, t_years, iv))
        diff = abs(d - target_delta)
        if diff < best_diff:
            best_diff = diff
            best = strike
        if d < target_delta - 0.06:   # ya nos pasamos de OTM, no sigue bajando el delta útil
            break
    return best


def iv_proxy(bars_up_to: list[PriceBar], iv_mult: float, window: int = 20) -> float | None:
    """IV estimada = HV(20) × iv_mult (la IV implícita suele estar por encima de la realizada)."""
    hv = compute_historical_volatility(bars_up_to, window_days=window)
    return round(hv * iv_mult, 4) if hv is not None else None


# ----------------------------------- Resultado de un trade -----------------------------------

@dataclass
class BacktestTrade:
    symbol: str
    strategy: str
    entry_date: date
    exit_date: date
    days_held: int
    dte: int
    strike: float
    entry_underlying: float
    exit_underlying: float
    entry_premium: float          # crédito por acción al abrir
    exit_value: float             # costo por acción al cerrar (0 si expiró OTM)
    contracts: int
    margin_per_contract: float    # capital comprometido por contrato (calibrado al broker)
    pnl: float                    # $ total
    return_pct: float             # pnl / capital comprometido
    annualized_pct: float
    close_reason: str
    won: bool
    approximate: bool = False     # True para los irons (0DTE reconstruido)


# ----------------------------------- Naked puts (riguroso) -----------------------------------

@dataclass
class NakedPutParams:
    target_delta: float = 0.25
    target_dte: int = 40
    min_coverage: float = 0.0
    min_annualized: float = 0.0
    iv_mult: float = 1.15
    contracts: int = 1
    # Gatillos de entrada opcionales (estilo TradeMachine, usuario 2026-08-07):
    rsi_max: float | None = None          # solo abre si RSI(14) <= esto (comprar la caída)
    near_support_pct: float | None = None  # solo abre si el precio está <= este % ARRIBA de un soporte
    # Objetivo de ganancia (usuario 2026-08-07): si se fija (0.30/0.45/0.65), cierra PLANO a ese % de
    # la prima; si es None, usa las reglas escalonadas del robot (tiered_profit_target).
    profit_target_pct: float | None = None
    # Modelar ASIGNACIONES como la rueda (usuario 2026-08-07): al vencer ITM, te QUEDÁS con las 100
    # acciones y las vendés cuando el precio recupera el break-even (strike − prima), en vez de bookear
    # la pérdida al vencimiento. Baja el drawdown y refleja cómo se opera de verdad.
    model_assignment: bool = True


def _effective_target(p: NakedPutParams, coverage_now: float, dte_left: int, age: int,
                      settings: SimulatorSettings) -> tuple[float, bool]:
    """Objetivo de ganancia efectivo: plano (si p.profit_target_pct) o escalonado (reglas del robot)."""
    if p.profit_target_pct is not None:
        return p.profit_target_pct, False
    return rules.tiered_profit_target(coverage_now, dte_left, age, False, settings)


def _make_assigned_trade(symbol: str, pos: dict, exit_spot: float, exit_date, reason: str,
                         strategy: str = "naked_put") -> BacktestTrade:
    """Trade de un put ASIGNADO manejado como rueda: te quedaste con las acciones al `strike` y las
    vendés a `exit_spot`. P&L total = (prima + exit_spot − strike) × 100 × contratos (break-even en
    strike − prima). El capital pasa a ser cash-secured (strike × 100)."""
    contracts = pos["contracts"]
    pnl = (pos["entry_premium"] + exit_spot - pos["strike"]) * CONTRACT_MULTIPLIER * contracts
    capital = pos["strike"] * CONTRACT_MULTIPLIER * contracts
    days_held = max((exit_date - pos["entry_date"]).days, 1)
    return BacktestTrade(
        symbol=symbol, strategy=strategy, entry_date=pos["entry_date"], exit_date=exit_date,
        days_held=days_held, dte=pos["dte"], strike=pos["strike"],
        entry_underlying=round(pos["entry_underlying"], 2), exit_underlying=round(exit_spot, 2),
        entry_premium=round(pos["entry_premium"], 2), exit_value=round(max(pos["strike"] - exit_spot, 0.0), 2),
        contracts=contracts, margin_per_contract=round(pos["strike"] * CONTRACT_MULTIPLIER, 2),
        pnl=round(pnl, 2), return_pct=round(pnl / capital, 4) if capital else 0.0,
        annualized_pct=round(pnl / capital * 365.0 / days_held * 100, 2) if capital else 0.0,
        close_reason=reason, won=pnl > 0,
    )


def _make_put_trade(symbol, strategy, entry_date, exit_date, dte, strike, entry_underlying,
                    exit_underlying, entry_premium, close_value, contracts, margin, reason):
    pnl = (entry_premium - close_value) * CONTRACT_MULTIPLIER * contracts
    capital = margin * contracts
    ret = pnl / capital if capital > 0 else 0.0
    days_held = max((exit_date - entry_date).days, 1)
    return BacktestTrade(
        symbol=symbol, strategy=strategy, entry_date=entry_date, exit_date=exit_date, days_held=days_held,
        dte=dte, strike=strike, entry_underlying=round(entry_underlying, 2), exit_underlying=round(exit_underlying, 2),
        entry_premium=round(entry_premium, 2), exit_value=round(close_value, 2), contracts=contracts,
        margin_per_contract=round(margin, 2), pnl=round(pnl, 2), return_pct=round(ret, 4),
        annualized_pct=round(ret * 365.0 / days_held * 100, 2), close_reason=reason, won=pnl > 0,
    )


def _passes_triggers(bars_up_to: list[PriceBar], spot: float, p: NakedPutParams) -> bool:
    """¿Se dan los gatillos de entrada opcionales? (RSI bajo / precio cerca de un soporte)."""
    if p.rsi_max is not None:
        rsi = compute_rsi(bars_up_to)
        if rsi is None or rsi > p.rsi_max:
            return False
    if p.near_support_pct is not None:
        supports, _res = levels.find_strong_support_resistance(bars_up_to, spot)
        near = any(0 <= (spot - s) / spot <= p.near_support_pct for s in supports if s > 0)
        if not near:
            return False
    return True


def backtest_naked_puts(bars: list[PriceBar], symbol: str, settings: SimulatorSettings,
                        params: NakedPutParams | None = None) -> list[BacktestTrade]:
    """Camina el histórico diario: cuando no hay posición abierta, evalúa vender un put a delta
    objetivo (mismos gates de cobertura/anualizado que el robot); mientras hay una abierta, la valúa
    cada día y la cierra con las MISMAS reglas de ganancia escalonada, o la liquida a intrínseco al
    vencimiento. Devuelve la lista de trades cerrados."""
    p = params or NakedPutParams()
    bars = sorted(bars, key=lambda b: b.trade_date)
    trades: list[BacktestTrade] = []
    pos: dict | None = None

    for i, bar in enumerate(bars):
        spot = bar.close
        today = bar.trade_date

        if pos is not None and pos.get("phase") == "stock":
            # Tenés las acciones asignadas: vendé cuando recupere el break-even (strike − prima), o al
            # final del histórico (marcado a último precio).
            breakeven = pos["strike"] - pos["entry_premium"]
            if spot >= breakeven:
                trades.append(_make_assigned_trade(symbol, pos, spot, today, "assigned_recovered"))
                pos = None
            elif i == len(bars) - 1:
                trades.append(_make_assigned_trade(symbol, pos, spot, today, "assigned_open"))
                pos = None

        elif pos is not None:
            dte_left = (pos["expiration"] - today).days
            age = (today - pos["entry_date"]).days
            iv_now = iv_proxy(bars[: i + 1], p.iv_mult) or pos["entry_iv"]
            coverage_now = rules.put_coverage_pct(spot, pos["strike"])

            close_value = None
            reason = None
            if dte_left <= 0:
                if spot < pos["strike"] and p.model_assignment:
                    pos["phase"] = "stock"   # asignación: te quedás con las acciones (rueda)
                else:
                    close_value = max(pos["strike"] - spot, 0.0)   # OTM: vence sin valor (ganás la prima)
                    reason = "expired"
            else:
                value = bs_put_price(spot, pos["strike"], dte_left / 365.0, iv_now)
                profit_frac = (pos["entry_premium"] - value) / pos["entry_premium"] if pos["entry_premium"] > 0 else 0.0
                target, force = _effective_target(p, coverage_now, dte_left, age, settings)
                if profit_frac >= target or (force and profit_frac > 0):
                    close_value = value
                    reason = "profit_target" if profit_frac >= target else "near_strike"

            if close_value is not None:
                trades.append(_make_put_trade(
                    symbol, "naked_put", pos["entry_date"], today, pos["dte"], pos["strike"],
                    pos["entry_underlying"], spot, pos["entry_premium"], close_value, pos["contracts"],
                    pos["margin"], reason))
                pos = None

        if pos is None:
            iv = iv_proxy(bars[: i + 1], p.iv_mult)
            if iv is None:
                continue
            if not _passes_triggers(bars[: i + 1], spot, p):
                continue
            t = p.target_dte / 365.0
            strike = select_put_strike(spot, t, iv, p.target_delta)
            if strike is None:
                continue
            coverage = rules.put_coverage_pct(spot, strike)
            if coverage < p.min_coverage:
                continue
            premium = bs_put_price(spot, strike, t, iv)
            if premium <= 0:
                continue
            margin = rules.per_contract_cost(spot, strike, premium, settings)
            annualized = rules.annualized_return_on_cost(premium, margin, p.target_dte)
            if annualized < p.min_annualized:
                continue
            pos = {
                "phase": "put", "entry_date": today, "expiration": today + timedelta(days=p.target_dte),
                "dte": p.target_dte, "strike": strike, "entry_underlying": spot,
                "entry_premium": premium, "entry_iv": iv, "margin": margin, "contracts": p.contracts,
            }
    # Si quedaste con acciones asignadas al final del histórico, se marcan a último precio.
    if pos is not None and pos.get("phase") == "stock" and bars:
        trades.append(_make_assigned_trade(symbol, pos, bars[-1].close, bars[-1].trade_date, "assigned_open"))
    return trades


def _run_single_put(bars: list[PriceBar], i_entry: int, symbol: str, settings: SimulatorSettings,
                    p: NakedPutParams, strategy: str, target_dte: int) -> BacktestTrade | None:
    """Abre UN put en bars[i_entry] y lo simula hacia adelante hasta cerrarlo (reglas de ganancia o
    intrínseco al vencimiento). Devuelve el trade, o None si no se pudo abrir/quedó abierto al final.
    Base común del backtest por earnings/gatillo puntual."""
    entry = bars[i_entry]
    spot0 = entry.close
    iv0 = iv_proxy(bars[: i_entry + 1], p.iv_mult)
    if iv0 is None:
        return None
    t = target_dte / 365.0
    strike = select_put_strike(spot0, t, iv0, p.target_delta)
    if strike is None:
        return None
    premium = bs_put_price(spot0, strike, t, iv0)
    if premium <= 0:
        return None
    margin = rules.per_contract_cost(spot0, strike, premium, settings)
    expiration = entry.trade_date + timedelta(days=target_dte)
    pos = {"phase": "put", "entry_date": entry.trade_date, "strike": strike, "entry_underlying": spot0,
           "entry_premium": premium, "dte": target_dte, "contracts": p.contracts}
    for j in range(i_entry + 1, len(bars)):
        bar = bars[j]
        spot = bar.close
        if pos["phase"] == "stock":
            if spot >= strike - premium:   # recuperó el break-even
                return _make_assigned_trade(symbol, pos, spot, bar.trade_date, "assigned_recovered", strategy)
            if j == len(bars) - 1:
                return _make_assigned_trade(symbol, pos, spot, bar.trade_date, "assigned_open", strategy)
            continue
        dte_left = (expiration - bar.trade_date).days
        age = (bar.trade_date - entry.trade_date).days
        iv_now = iv_proxy(bars[: j + 1], p.iv_mult) or iv0
        coverage_now = rules.put_coverage_pct(spot, strike)
        if dte_left <= 0:
            if spot < strike and p.model_assignment:
                pos["phase"] = "stock"   # asignación: te quedás con las acciones (rueda)
                continue
            close_value, reason = max(strike - spot, 0.0), "expired"
        else:
            value = bs_put_price(spot, strike, dte_left / 365.0, iv_now)
            pf = (premium - value) / premium if premium > 0 else 0.0
            target, force = _effective_target(p, coverage_now, dte_left, age, settings)
            if pf >= target or (force and pf > 0):
                close_value, reason = value, ("profit_target" if pf >= target else "near_strike")
            else:
                continue
        return _make_put_trade(symbol, strategy, entry.trade_date, bar.trade_date, target_dte, strike,
                               spot0, spot, premium, close_value, p.contracts, margin, reason)
    if pos["phase"] == "stock":
        return _make_assigned_trade(symbol, pos, bars[-1].close, bars[-1].trade_date, "assigned_open", strategy)
    return None


def _open_close_put(bars: list[PriceBar], i_entry: int, i_exit: int, symbol: str,
                    settings: SimulatorSettings, p: NakedPutParams, strategy: str, reason: str) -> BacktestTrade | None:
    """Abre un put en i_entry y lo CIERRA en i_exit a valor Black-Scholes (sin esperar al objetivo ni
    al vencimiento). Para 'salir antes del earnings': cerrar el put el día previo al reporte."""
    entry = bars[i_entry]
    spot0 = entry.close
    iv0 = iv_proxy(bars[: i_entry + 1], p.iv_mult)
    if iv0 is None:
        return None
    t = p.target_dte / 365.0
    strike = select_put_strike(spot0, t, iv0, p.target_delta)
    if strike is None:
        return None
    premium = bs_put_price(spot0, strike, t, iv0)
    if premium <= 0:
        return None
    margin = rules.per_contract_cost(spot0, strike, premium, settings)
    xbar = bars[i_exit]
    spot = xbar.close
    dte_left = max(p.target_dte - (xbar.trade_date - entry.trade_date).days, 1)
    iv_now = iv_proxy(bars[: i_exit + 1], p.iv_mult) or iv0
    close_value = bs_put_price(spot, strike, dte_left / 365.0, iv_now)
    return _make_put_trade(symbol, strategy, entry.trade_date, xbar.trade_date, p.target_dte, strike,
                           spot0, spot, premium, close_value, p.contracts, margin, reason)


def backtest_earnings_puts(bars: list[PriceBar], earnings_dates: list[date], symbol: str,
                           settings: SimulatorSettings, params: NakedPutParams | None = None,
                           days_before: int = 5, hold_through: bool = True) -> list[BacktestTrade]:
    """Backtest estilo TradeMachine ALREDEDOR DE EARNINGS (usuario 2026-08-07): por cada earnings del
    histórico, vende un put ~`days_before` días hábiles antes. Dos modos:
      · hold_through=True: el vencimiento ABARCA el earnings → aguanta el reporte (captura el gap).
      · hold_through=False: CIERRA el put el día previo al earnings → cobra la prima/decaimiento y
        SE SALE antes del reporte (evita el gap)."""
    p = params or NakedPutParams()
    bars = sorted(bars, key=lambda b: b.trade_date)
    if not bars:
        return []
    trades: list[BacktestTrade] = []
    for e in sorted(set(earnings_dates)):
        target_entry = e - timedelta(days=int(days_before * 1.5))   # ~días hábiles ≈ calendario×1.5
        i_entry = None
        i_before = None   # última barra ANTES del earnings (para salir antes)
        for i, bar in enumerate(bars):
            if i < 25:
                continue                      # calentamiento de la HV
            if bar.trade_date <= target_entry:
                i_entry = i
            if bar.trade_date < e:
                i_before = i
            elif bar.trade_date >= e:
                break
        if i_entry is None:
            continue
        if hold_through:
            span = (e - bars[i_entry].trade_date).days + 10   # el vencimiento queda DESPUÉS del earnings
            dte = max(p.target_dte, span)
            tr = _run_single_put(bars, i_entry, symbol, settings, p, "naked_put_earnings", dte)
        else:
            if i_before is None or i_before <= i_entry:
                continue
            tr = _open_close_put(bars, i_entry, i_before, symbol, settings, p, "naked_put_earnings", "pre_earnings_exit")
        if tr is not None:
            trades.append(tr)
    return trades


# --------------------------- Irons 0DTE (APROXIMADO desde rango diario) ---------------------------

@dataclass
class IronParams:
    calm_range_pct: float = 0.004      # condor: día "calmo" si (high-low)/open <= esto
    profit_target_pct: float = 0.60
    stop_loss_dollars: float = 100.0
    iv_mult: float = 1.15
    max_per_day: int = 3               # cuántos por día (aprox: si aplica, cuenta como N iguales)
    # Ganancia FIJA en dólares, cuando la estrategia cierra por monto y no por porcentaje del
    # crédito. Es el caso del Iron Butterfly: cierra a +$50 fijo (settings.yaml
    # `intraday_butterfly.profit_target`, decisión del usuario 2026-08-10: "scalp rápido"). Sin
    # esto el backtest del butterfly usaba un % del crédito estimado y no se parecía a la regla
    # real. None = usar `profit_target_pct` (el caso del condor).
    profit_dollars: float | None = None
    # Delta de los cortos, tomado de la config real (`intraday_condor.short_delta_max`). Antes la
    # distancia estaba escrita a mano como 1.04 sigma; ese numero NO era arbitrario -- es exactamente
    # el equivalente en sigmas de delta 0.15 -- pero quedaba clavado, asi que cambiar la
    # configuracion no movia el backtest y se medía una estrategia distinta a la que el robot opera.
    short_delta: float = 0.15
    # Filtro de DIA CALMO. El condor en vivo solo entra si el mercado viene tranquilo
    # (`intraday_condor.calm_range_pct`). El backtest abria uno TODOS los dias, incluidos los
    # violentos que el robot habria salteado, y por eso subestimaba la estrategia.
    #
    # Ojo con la trampa: en vivo eso se decide mirando los primeros 30 minutos. Con datos diarios
    # solo se conoce el rango del dia ENTERO, y usarlo seria decidir con informacion del futuro
    # (look-ahead). Por eso el filtro mira el rango del dia ANTERIOR, que si esta disponible al
    # abrir. Es una aproximacion del gatillo real, no el gatillo real.
    filtrar_dias_calmos: bool = False


def sigmas_para_delta(delta: float) -> float:
    """A cuantos sigmas OTM esta un corto de este delta.

    Para una opcion OTM, delta ~= la probabilidad de terminar dentro del dinero, asi que la
    distancia en sigmas es el cuantil de la normal: z = inv_cdf(1 - delta). Con delta 0.15 da 1.036
    -- el mismo 1.04 que antes estaba escrito a mano, ahora derivado en vez de clavado."""
    d = min(max(float(delta), 0.001), 0.499)
    return NormalDist().inv_cdf(1.0 - d)


def _rango_del_dia(bar: PriceBar) -> float | None:
    """Rango intradia como fraccion de la apertura. None si la barra no sirve."""
    if not bar.open or bar.open <= 0 or bar.high is None or bar.low is None:
        return None
    return (bar.high - bar.low) / bar.open


def _daily_sigma_pct(iv: float) -> float:
    """Movimiento diario de 1σ como fracción del precio (IV anualizada → diaria)."""
    return iv / math.sqrt(252)


def _iron_trade(symbol, strategy, bar, est_credit, won, p):
    if not won:
        pnl = round(-p.stop_loss_dollars, 2)
    elif p.profit_dollars is not None:
        pnl = round(p.profit_dollars, 2)          # cierre por monto fijo (butterfly: +$50)
    else:
        pnl = round(est_credit * CONTRACT_MULTIPLIER * p.profit_target_pct, 2)
    capital = p.stop_loss_dollars
    return BacktestTrade(
        symbol=symbol, strategy=strategy, entry_date=bar.trade_date, exit_date=bar.trade_date,
        days_held=1, dte=0, strike=0.0, entry_underlying=bar.open, exit_underlying=bar.close,
        entry_premium=round(est_credit, 2), exit_value=0.0, contracts=1, margin_per_contract=capital,
        pnl=pnl, return_pct=round(pnl / capital, 4) if capital else 0.0,
        annualized_pct=round(pnl / capital * 365 * 100, 2) if capital else 0.0,
        close_reason=("profit_target" if won else "stop_loss"), won=won, approximate=True,
    )


def params_del_condor(cfg) -> IronParams:
    """Traduce la configuración REAL del Iron Condor a parámetros de backtest.

    Existe para que haya UN solo lugar donde se hace esta traducción. Antes el dashboard armaba los
    suyos y el trabajo semanal los suyos, y encima el dashboard le pasaba los del condor también al
    butterfly — o sea que cada pantalla medía una estrategia distinta, y ninguna era la que el robot
    opera (usuario 2026-08-23: "quiero que uses lo que ya estás usando en simulador y en real").

    Se le pasa la config EFECTIVA (`learning.effective_condor`), no la de settings.yaml, para que el
    histórico se mida contra las reglas vigentes y no contra las de fábrica.

    Lee con getattr para no acoplar el motor de backtest a las clases de configuración."""
    return IronParams(
        profit_target_pct=float(getattr(cfg, "profit_target_pct", 0.35) or 0.35),
        stop_loss_dollars=float(getattr(cfg, "stop_loss_dollars", 100.0) or 100.0),
        short_delta=float(getattr(cfg, "short_delta_max", 0.15) or 0.15),
        calm_range_pct=float(getattr(cfg, "calm_range_pct", 0.004) or 0.004),
        filtrar_dias_calmos=True,
    )


def params_del_butterfly(cfg) -> IronParams:
    """Traduce la configuración REAL del Iron Butterfly a parámetros de backtest.

    Dos diferencias con el condor que importan y que antes se perdían:
      - cierra por MONTO fijo (`profit_target`, +$50), no por porcentaje del crédito;
      - su stop es `stop_loss` (-$70), con otro nombre que el del condor.

    NO filtra por día calmo: el butterfly no tiene ese gatillo — entra cuando el precio se separa de
    la SMA8, que es otra condición y no se puede reproducir con datos diarios."""
    return IronParams(
        profit_dollars=float(getattr(cfg, "profit_target", 50.0) or 50.0),
        stop_loss_dollars=float(getattr(cfg, "stop_loss", 70.0) or 70.0),
        filtrar_dias_calmos=False,
    )


def backtest_iron_condor_daily(bars: list[PriceBar], symbol: str, params: IronParams | None = None) -> list[BacktestTrade]:
    """APROXIMACIÓN del Iron Condor 0DTE sobre histórico diario. Los cortos van a delta ~0.15, o sea
    ~1σ del movimiento diario esperado. GANA si el movimiento del día (close vs open) se queda dentro
    de ~1σ (los cortos vencen OTM → cobra el crédito); PIERDE (stop) si se pasa. El crédito se estima
    desde la IV. Es una aproximación — no hay intradía histórico de años atrás."""
    p = params or IronParams()
    bars = sorted(bars, key=lambda b: b.trade_date)
    trades: list[BacktestTrade] = []
    for i, bar in enumerate(bars):
        if bar.open <= 0:
            continue
        iv = iv_proxy(bars[: i + 1], p.iv_mult)
        if iv is None:
            continue
        # Filtro de dia calmo con el rango de AYER (nunca el de hoy: seria look-ahead).
        if p.filtrar_dias_calmos:
            if i == 0:
                continue
            rango_ayer = _rango_del_dia(bars[i - 1])
            if rango_ayer is None or rango_ayer > p.calm_range_pct:
                continue
        sigma = _daily_sigma_pct(iv)
        move = abs(bar.close - bar.open) / bar.open
        short_dist = sigmas_para_delta(p.short_delta) * sigma
        # El credito baja a medida que los cortos se alejan. Proporcional al delta es una regla
        # gruesa pero razonable (a delta 0.15 reproduce el valor que se usaba antes) y evita el
        # absurdo de cobrar lo mismo vendiendo a delta 0.05 que a delta 0.30.
        est_credit = max(0.20, bar.close * sigma * 0.20 * (p.short_delta / 0.15))
        won = move <= short_dist
        trades.append(_iron_trade(symbol, "iron_condor", bar, est_credit, won, p))
    return trades


def backtest_iron_butterfly_daily(bars: list[PriceBar], symbol: str, params: IronParams | None = None) -> list[BacktestTrade]:
    """APROXIMACIÓN del Iron Butterfly 0DTE: gana en días que cerraron cerca de su apertura (mercado
    plano / que revirtió); pierde en días que tendieron fuerte. Estimado desde OHLC diario."""
    p = params or IronParams()
    bars = sorted(bars, key=lambda b: b.trade_date)
    trades: list[BacktestTrade] = []
    for i, bar in enumerate(bars):
        if bar.open <= 0:
            continue
        iv = iv_proxy(bars[: i + 1], p.iv_mult)
        if iv is None:
            continue
        if p.filtrar_dias_calmos:
            if i == 0:
                continue
            rango_ayer = _rango_del_dia(bars[i - 1])
            if rango_ayer is None or rango_ayer > p.calm_range_pct:
                continue
        sigma = _daily_sigma_pct(iv)
        move = abs(bar.close - bar.open) / bar.open
        # El butterfly (cuerpo ATM) gana solo en días MUY quietos/que revirtieron: ~0.6σ. Cobra más
        # crédito que el condor pero acierta menos veces.
        won = move <= 0.6 * sigma
        est_credit = max(0.30, bar.close * sigma * 0.40)
        trades.append(_iron_trade(symbol, "iron_butterfly", bar, est_credit, won, p))
    return trades


def sweep_delta(bars_by_symbol: dict[str, list[PriceBar]], settings: SimulatorSettings,
                deltas=(0.15, 0.20, 0.25, 0.30, 0.35), base: NakedPutParams | None = None) -> list[dict]:
    """Corre el backtest de naked puts a varios deltas objetivo sobre todos los símbolos y devuelve
    el ranking — para que el robot APRENDA del histórico qué delta rindió mejor (usuario 2026-08-06).
    Liviano: un puñado de deltas por los símbolos elegidos."""
    base = base or NakedPutParams()
    out = []
    for d in deltas:
        params = NakedPutParams(target_delta=d, target_dte=base.target_dte, min_coverage=base.min_coverage,
                                min_annualized=base.min_annualized, iv_mult=base.iv_mult, contracts=base.contracts)
        all_trades: list[BacktestTrade] = []
        for sym, bars in bars_by_symbol.items():
            all_trades.extend(backtest_naked_puts(bars, sym, settings, params))
        s = summarize(all_trades)
        out.append({"delta": d, "n": s["n"], "win_rate": s["win_rate"], "total_pnl": s["total_pnl"],
                    "avg_annualized": s["avg_annualized"], "max_drawdown": s["max_drawdown"]})
    return out


# ------------------------------------- Estadísticas / resumen -------------------------------------

def portfolio_replay(trades: list[BacktestTrade], initial_capital: float = 100_000.0,
                     max_pct_per_trade: float = 0.10) -> dict:
    """Simula una CUENTA REAL con `initial_capital` (usuario 2026-08-07: "backtesting con 100K"): reproduce
    los trades en orden cronológico compartiendo un pozo de capital. Cada operación toma hasta
    `max_pct_per_trade` del equity actual y solo se abre si hay capital libre (si no, se SALTEA por falta
    de capital). El P&L se escala a los contratos que el capital permitió. Devuelve equity final, retorno
    %, pico de capital usado, cuántas tomó vs salteó, y la curva de equity de la cuenta."""
    if not trades or initial_capital <= 0:
        return {"initial_capital": initial_capital, "final_equity": initial_capital, "return_pct": 0.0,
                "realized_pnl": 0.0, "taken": 0, "skipped": 0, "peak_capital_used": 0.0,
                "peak_capital_pct": 0.0, "peak_exposure": 0.0, "peak_exposure_pct": 0.0,
                "return_on_exposure_pct": 0.0, "equity_curve": []}
    # Eventos: en cada fecha procesamos primero las SALIDAS (liberan capital) y después las ENTRADAS.
    events = []
    for idx, t in enumerate(trades):
        events.append((t.exit_date, 0, idx))    # 0 = salida (primero)
        events.append((t.entry_date, 1, idx))   # 1 = entrada (después)
    events.sort(key=lambda e: (e[0], e[1]))

    equity = initial_capital
    committed = 0.0        # colateral/margen trabado
    exposure = 0.0         # EXPOSICIÓN = notional si te asignan (strike×100×contratos)
    chosen: dict[int, int] = {}
    taken = skipped = 0
    peak_commit = 0.0
    peak_exposure = 0.0
    curve = [{"date": trades[0].entry_date.isoformat(), "equity": round(initial_capital, 2)}]
    for _date, typ, idx in events:
        t = trades[idx]
        m = t.margin_per_contract
        # Exposición por contrato: el notional del subyacente (strike×100). Para los irons (strike 0)
        # cae al riesgo definido (el margen/stop), que ES su exposición real.
        notional_pc = (t.strike * CONTRACT_MULTIPLIER) if t.strike and t.strike > 0 else m
        base_contracts = t.contracts or 1
        pnl_per_contract = t.pnl / base_contracts
        if typ == 1:   # ENTRADA
            free = equity - committed
            budget = equity * max_pct_per_trade
            n = int(min(budget, free) // m) if m > 0 else 0
            if n < 1:
                skipped += 1
                chosen[idx] = 0
                continue
            chosen[idx] = n
            committed += n * m
            exposure += n * notional_pc
            peak_commit = max(peak_commit, committed)
            peak_exposure = max(peak_exposure, exposure)
            taken += 1
        else:          # SALIDA
            n = chosen.get(idx, 0)
            if n <= 0:
                continue
            equity += pnl_per_contract * n
            committed -= n * m
            exposure -= n * notional_pc
            curve.append({"date": _date.isoformat(), "equity": round(equity, 2)})
    realized = equity - initial_capital
    return {
        "initial_capital": round(initial_capital, 2),
        "final_equity": round(equity, 2),
        "return_pct": round(realized / initial_capital * 100, 2),
        "realized_pnl": round(realized, 2),
        "taken": taken,
        "skipped": skipped,
        "peak_capital_used": round(peak_commit, 2),
        "peak_capital_pct": round(peak_commit / initial_capital * 100, 1),
        # Exposición: cuánto notional del subyacente llegaste a tener a la vez (riesgo real si te asignan).
        "peak_exposure": round(peak_exposure, 2),
        "peak_exposure_pct": round(peak_exposure / initial_capital * 100, 1),   # >100% = apalancado
        "return_on_exposure_pct": round(realized / peak_exposure * 100, 2) if peak_exposure > 0 else 0.0,
        "equity_curve": curve,
    }


def _max_drawdown(equity: list[float]) -> float:
    peak = -1e18
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    return round(mdd, 2)


def summarize(trades: list[BacktestTrade]) -> dict:
    """KPIs del backtest: cantidad, win rate, P&L total, anualizado medio, peor caída (drawdown) y la
    curva de equity acumulada (ordenada por fecha de salida)."""
    if not trades:
        return {"n": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_annualized": 0.0,
                "max_drawdown": 0.0, "wins": 0, "losses": 0, "equity_curve": [], "worst_trade": None}
    ordered = sorted(trades, key=lambda t: t.exit_date)
    equity = []
    cum = 0.0
    for t in ordered:
        cum += t.pnl
        equity.append({"date": t.exit_date.isoformat(), "equity": round(cum, 2)})
    wins = [t for t in trades if t.won]
    losses = [t for t in trades if not t.won]
    anns = [t.annualized_pct for t in trades]
    worst = min(trades, key=lambda t: t.pnl)
    # Desglose de CÓMO se ganó (usuario 2026-08-07): motivo de cierre, $ de ganadoras vs perdedoras y
    # cuántas fueron ASIGNACIONES (put que venció ITM: spot < strike al vencimiento → pérdida).
    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t.close_reason] = by_reason.get(t.close_reason, 0) + 1
    # "Asignaciones": puts que terminaron ITM. Con la rueda quedan como assigned_recovered/assigned_open;
    # con la rueda apagada, como 'expired' con la acción por debajo del strike.
    assignments = [t for t in trades if t.close_reason.startswith("assigned")
                   or (t.close_reason == "expired" and t.strike and t.exit_underlying < t.strike)]
    gross_win = round(sum(t.pnl for t in wins), 2)
    gross_loss = round(sum(t.pnl for t in losses), 2)
    return {
        "n": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(sum(t.pnl for t in trades), 2),
        "avg_annualized": round(sum(anns) / len(anns), 1),
        "max_drawdown": _max_drawdown([e["equity"] for e in equity]),
        "equity_curve": equity,
        "worst_trade": {"symbol": worst.symbol, "pnl": worst.pnl, "date": worst.exit_date.isoformat(),
                        "strategy": worst.strategy},
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
        "by_reason": by_reason,
        "assignments": len(assignments),
        "assignment_loss": round(sum(t.pnl for t in assignments), 2),
    }
