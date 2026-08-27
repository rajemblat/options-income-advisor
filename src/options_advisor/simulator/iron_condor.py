from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from zoneinfo import ZoneInfo

from options_advisor.broker.models import IntradayBar, OptionChain, OptionContract
from options_advisor.config import IntradayCondorSettings

# Estrategia 3 (2026-08-05): Iron Condor 0DTE sobre SPX para DÍAS CALMOS. Lógica PURA (sin I/O ni
# broker) para poder testearla aparte del motor en vivo. Vende put+call OTM a delta <= short_delta_max
# (donde mejor pague), compra alas `wing_width` puntos afuera; gana con que SPX se quede en rango.

CONTRACT_MULTIPLIER = 100


def _parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


MERCADO = ZoneInfo("America/New_York")


def hora_de_mercado(ts: datetime) -> time:
    """Hora de Nueva York de esa barra — que es el reloj con el que piensa el usuario.

    Schwab devuelve las barras en UTC (`schwab_client.get_intraday_bars` las arma con tz=utc), y
    antes se comparaban TAL CUAL contra la ventana del settings. Eso tenía dos problemas:

      1. La ventana "10:00-14:00" era en realidad 09:30-10:00 hora del Este: media hora, no cuatro.
         El robot solo podía abrir en los primeros 30 minutos de la rueda.
      2. El 2026-11-02, al salir EEUU del horario de verano, el mercado pasa a abrir 14:30 UTC y la
         ventana ya habría cerrado: el condor dejaba de abrir para siempre, en silencio.

    Convirtiendo a hora del Este, la ventana significa lo mismo todo el año y el cambio de hora se
    resuelve solo (usuario 2026-08-27: "que opere cuando vea oportunidad, máximo hasta las 2 pm,
    horario de mercado").

    Una barra SIN huso se toma como si ya viniera en hora de mercado: es lo que arman los tests y
    las fixtures, y convertirla sería inventarle un huso que nadie le puso."""
    if ts.tzinfo is None:
        return ts.time()
    return ts.astimezone(MERCADO).time()


@dataclass
class CondorSignal:
    """Señal de entrada: `calm` (día de poco movimiento) e `in_window` (dentro de la ventana horaria
    de entrada). Solo se abre si ambos son True."""

    calm: bool
    in_window: bool
    price: float
    day_range_pct: float
    # Filtro de volatilidad en suba (usuario 2026-08-14). True cuando el VIX no está expandiéndose
    # más de lo tolerado, o cuando el filtro está apagado / no hay dato de VIX. Se evalúa aparte de
    # `calm` a propósito: son dos cosas distintas (el SPX quieto vs. la volatilidad quieta) y el log
    # tiene que poder decir cuál de las dos frenó la entrada.
    vix_ok: bool = True
    vix_change_pct: float | None = None


def evaluate_condor_signal(bars: list[IntradayBar], settings: IntradayCondorSettings,
                           vix_change_pct: float | None = None) -> CondorSignal:
    """Evalúa si conviene abrir un Iron Condor: el día viene CALMO (rango intradía máx-mín por
    debajo de `calm_range_pct`) y estamos dentro de la ventana horaria de entrada (hora de la última
    barra). Usa el timestamp de la última barra como 'ahora' del mercado (testeable).

    HUSO HORARIO (cambiado 2026-08-27): `entry_window_start/end` se leen en **hora de Nueva York**,
    no en UTC. Ver `hora_de_mercado`. Antes se comparaba contra la hora UTC de la barra y la ventana
    "10:00-14:00" valía, en los hechos, 09:30-10:00 ET — solo los primeros 30 minutos de la rueda."""
    if not bars:
        return CondorSignal(False, False, 0.0, 0.0)
    price = bars[-1].close
    hi = max(b.high for b in bars)
    lo = min(b.low for b in bars)
    ref = bars[0].open or price or 1.0
    day_range_pct = (hi - lo) / ref if ref else 1.0
    now = hora_de_mercado(bars[-1].timestamp)
    in_window = _parse_hhmm(settings.entry_window_start) <= now <= _parse_hhmm(settings.entry_window_end)
    calm = day_range_pct <= settings.calm_range_pct
    # VIX QUIETO, para cualquier lado (usuario 2026-08-14: "no tiene que estar bajando ni subiendo;
    # lo mejor es que no suba ni baje mucho ese día, que sea un día lateral estable"). Se mira el
    # movimiento ABSOLUTO: un VIX que se DERRUMBA 8% tampoco es un día tranquilo — suele ser un rally
    # fuerte, y al condor le duele igual que una suba, porque lo que lo mata es que el SPX se mueva.
    # Sin filtro configurado o sin dato de VIX, no bloquea nada.
    limit = getattr(settings, "max_vix_change_pct", None)
    vix_ok = True
    if limit is not None and vix_change_pct is not None:
        vix_ok = abs(vix_change_pct) <= abs(limit)
    return CondorSignal(calm, in_window, price, round(day_range_pct, 5), vix_ok, vix_change_pct)


@dataclass
class CondorBuild:
    """Un Iron Condor armado: put y call vendidos (OTM, delta <= tope) y sus alas compradas
    `wing_width` puntos afuera. `legs` = lista de (side, option_type, contract)."""

    short_put_strike: float
    short_call_strike: float
    long_put_strike: float
    long_call_strike: float
    legs: list[tuple[str, str, OptionContract]] = field(default_factory=list)
    net_credit: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    lower_breakeven: float = 0.0
    upper_breakeven: float = 0.0


def _leg_at(chain: OptionChain, option_type: str, strike: float, tol: float = 0.01) -> OptionContract | None:
    for c in chain.contracts:
        if c.option_type == option_type and abs(c.strike - strike) < tol:
            return c
    return None


def _best_short(chain: OptionChain, option_type: str, spot: float, settings: IntradayCondorSettings) -> OptionContract | None:
    """El strike OTM con |delta| <= short_delta_max que MEJOR paga (mayor prima mid). Put por debajo
    del spot, call por encima."""
    cands = []
    for c in chain.contracts:
        if c.option_type != option_type or c.greeks is None or c.greeks.delta is None:
            continue
        if option_type == "put" and c.strike >= spot:
            continue
        if option_type == "call" and c.strike <= spot:
            continue
        if abs(c.greeks.delta) <= settings.short_delta_max:
            cands.append(c)
    if not cands:
        return None
    return max(cands, key=lambda c: c.mid_price)   # "donde mejor pague"


def build_iron_condor(chain: OptionChain, spot: float, settings: IntradayCondorSettings) -> CondorBuild | None:
    """Arma el Iron Condor: vende put+call OTM a delta <= tope (mejor prima), compra alas a
    `wing_width` puntos. Devuelve None si faltan strikes, si el crédito no es positivo / no llega al
    mínimo, o si la pérdida máxima supera `max_collateral`."""
    if spot <= 0:
        return None
    short_put = _best_short(chain, "put", spot, settings)
    short_call = _best_short(chain, "call", spot, settings)
    if short_put is None or short_call is None:
        return None
    long_put = _leg_at(chain, "put", short_put.strike - settings.wing_width)
    long_call = _leg_at(chain, "call", short_call.strike + settings.wing_width)
    if long_put is None or long_call is None:
        return None
    # Las alas tienen que estar donde corresponde (long put por debajo del short put, etc.).
    if long_put.strike >= short_put.strike or long_call.strike <= short_call.strike:
        return None

    credit_ps = (short_put.mid_price + short_call.mid_price) - (long_put.mid_price + long_call.mid_price)
    if credit_ps <= 0:
        return None
    net_credit = credit_ps * CONTRACT_MULTIPLIER
    if settings.min_credit > 0 and net_credit < settings.min_credit:
        return None
    put_wing = short_put.strike - long_put.strike
    call_wing = long_call.strike - short_call.strike
    max_loss = max(put_wing, call_wing) * CONTRACT_MULTIPLIER - net_credit
    if settings.max_collateral > 0 and max_loss > settings.max_collateral:
        return None

    return CondorBuild(
        short_put_strike=short_put.strike,
        short_call_strike=short_call.strike,
        long_put_strike=long_put.strike,
        long_call_strike=long_call.strike,
        legs=[
            ("sell", "put", short_put),
            ("sell", "call", short_call),
            ("buy", "put", long_put),
            ("buy", "call", long_call),
        ],
        net_credit=round(net_credit, 2),
        max_profit=round(net_credit, 2),
        max_loss=round(max_loss, 2),
        lower_breakeven=round(short_put.strike - credit_ps, 2),
        upper_breakeven=round(short_call.strike + credit_ps, 2),
    )


def condor_close_value(
    chain: OptionChain, short_put_strike: float, short_call_strike: float,
    long_put_strike: float, long_call_strike: float,
) -> float | None:
    """Costo en dólares de CERRAR el condor ahora (recomprar los cortos, vender las alas), a precios
    mid. None si falta alguna pata en la cadena en vivo (hueco de datos)."""
    sp = _leg_at(chain, "put", short_put_strike)
    sc = _leg_at(chain, "call", short_call_strike)
    lp = _leg_at(chain, "put", long_put_strike)
    lc = _leg_at(chain, "call", long_call_strike)
    if None in (sp, sc, lp, lc):
        return None
    net = (sp.mid_price + sc.mid_price) - (lp.mid_price + lc.mid_price)
    return round(net * CONTRACT_MULTIPLIER, 2)


def condor_intrinsic_close_value(
    spot: float, short_put_strike: float, short_call_strike: float,
    long_put_strike: float, long_call_strike: float,
) -> float:
    """Valor de cierre al VENCIMIENTO (0DTE) por valor intrínseco, cuando las patas ya no están en la
    cadena en vivo porque venció."""
    net = (
        (max(short_put_strike - spot, 0.0) + max(spot - short_call_strike, 0.0))
        - (max(long_put_strike - spot, 0.0) + max(spot - long_call_strike, 0.0))
    )
    return round(net * CONTRACT_MULTIPLIER, 2)


def short_leg_deltas(build: CondorBuild) -> tuple[float | None, float | None]:
    """Delta ABSOLUTO del put corto y del call corto realmente elegidos. Es la feature que el
    aprendizaje necesita para saber si las operaciones buenas entraban más lejos o más cerca del
    dinero que las malas (usuario 2026-08-14). None si el broker no devolvió griegas."""
    sp = sc = None
    for side, otype, contract in build.legs:
        if side != "sell":
            continue
        greeks = getattr(contract, "greeks", None)
        delta = getattr(greeks, "delta", None) if greeks is not None else None
        if delta is None:
            continue
        if otype == "put":
            sp = round(abs(float(delta)), 4)
        elif otype == "call":
            sc = round(abs(float(delta)), 4)
    return sp, sc


def condor_unrealized(entry_net_credit: float, close_value: float) -> float:
    """P&L no realizado en dólares: crédito cobrado al abrir − costo de cerrar ahora."""
    return round(entry_net_credit - close_value, 2)


def should_close_condor(
    unrealized_pnl: float, entry_net_credit: float, expired: bool, settings: IntradayCondorSettings,
    age_minutes: float | None = None,
) -> tuple[bool, str | None]:
    """Regla de salida (usuario 2026-08-07). Se cierra con lo PRIMERO que ocurra:

      1. Ganancia rápida: +profit_target_early_pct del crédito dentro de los primeros
         `early_window_minutes` de vida (cerrar temprano permite reentrar el mismo día).
      2. Ganancia normal: +profit_target_pct del crédito, pasada esa ventana.
      3. Stop loss: −stop_loss_dollars, en cualquier momento.
      4. Vencimiento (0DTE).

    Los valores REALES viven en config/settings.yaml (`intraday_condor`) y hoy son 20% / 35% / 30 min
    / $100. Este docstring decía "40%... 50%... 20 min" — números de principios de agosto que ya no
    eran los vigentes, y confundían al verificar por qué el robot había cerrado (usuario 2026-08-24).
    No repetimos los números acá a propósito: la config manda."""
    if expired:
        return True, "expired"
    early = age_minutes is not None and age_minutes <= settings.early_window_minutes
    target = settings.profit_target_early_pct if early else settings.profit_target_pct
    if target > 0 and unrealized_pnl >= target * entry_net_credit:
        return True, "profit_target"
    if settings.stop_loss_dollars > 0 and unrealized_pnl <= -settings.stop_loss_dollars:
        return True, "stop_loss"
    return False, None
