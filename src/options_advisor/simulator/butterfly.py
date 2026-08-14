from __future__ import annotations

from dataclasses import dataclass, field

from options_advisor.broker.models import IntradayBar, OptionChain, OptionContract
from options_advisor.config import IntradayButterflySettings

# Estrategia 2 (2026-08-03): Iron Butterfly 0DTE intradía de REVERSIÓN a la media móvil 8 sobre
# SPX. Lógica PURA (sin I/O ni broker) para poder testearla aparte del motor en vivo. El motor
# que la ejecuta minuto a minuto (pedir barras/cadena de SPX, abrir, marcar y cerrar a
# +profit_target/-stop_loss) se construye en la Fase 2, cuando se pueda probar con mercado abierto.

CONTRACT_MULTIPLIER = 100


def sma(values: list[float], period: int) -> float | None:
    """Media móvil simple de los últimos `period` valores (cierres intradía). None si no hay
    suficientes barras todavía."""
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


@dataclass
class ButterflySignal:
    """Señal de entrada: `direction` = 'revert_down' (el precio subió y se alejó de la SMA8 → se
    apuesta a que baje) | 'revert_up' (bajó y se alejó → apuesta a que suba) | None (no operar)."""

    direction: str | None
    price: float
    sma8: float | None
    distance_pct: float


def evaluate_signal(bars: list[IntradayBar], settings: IntradayButterflySettings) -> ButterflySignal:
    """Evalúa la señal de reversión sobre las barras intradía (1 min). Entra cuando |precio − SMA8|
    supera `distance_threshold_pct`; la dirección apunta SIEMPRE hacia la SMA8 (reversión)."""
    closes = [b.close for b in bars]
    price = closes[-1] if closes else 0.0
    s = sma(closes, settings.sma_period)
    if s is None or s <= 0:
        return ButterflySignal(None, price, s, 0.0)
    distance = (price - s) / s
    direction: str | None = None
    if distance >= settings.distance_threshold_pct:
        direction = "revert_down"   # subió y se alejó por arriba → esperar baja
    elif distance <= -settings.distance_threshold_pct:
        direction = "revert_up"     # bajó y se alejó por abajo → esperar suba
    return ButterflySignal(direction, price, s, round(distance, 5))


@dataclass
class ButterflyBuild:
    """Un Iron Butterfly armado: cuerpo (put+call vendidos en el mismo strike) y alas (put+call
    comprados). Riesgo acotado por las alas. `legs` = lista de (side, option_type, contract)."""

    body_strike: float
    put_wing_width: float
    call_wing_width: float
    legs: list[tuple[str, str, OptionContract]] = field(default_factory=list)
    net_credit: float = 0.0      # crédito neto recibido, en dólares
    max_profit: float = 0.0      # ganancia máxima (= crédito neto), en dólares
    max_loss: float = 0.0        # pérdida máxima, en dólares
    lower_breakeven: float = 0.0
    upper_breakeven: float = 0.0


def _nearest(chain: OptionChain, option_type: str, target: float) -> OptionContract | None:
    cands = [c for c in chain.contracts if c.option_type == option_type]
    if not cands:
        return None
    return min(cands, key=lambda c: abs(c.strike - target))


def _leg_at(chain: OptionChain, option_type: str, strike: float, tol: float = 0.01) -> OptionContract | None:
    """Busca la pata EXACTA (mismo strike, con tolerancia de float) para re-marcar la posición."""
    for c in chain.contracts:
        if c.option_type == option_type and abs(c.strike - strike) < tol:
            return c
    return None


def butterfly_close_value(
    chain: OptionChain, body_strike: float, long_put_strike: float, long_call_strike: float
) -> float | None:
    """Costo en dólares de CERRAR el butterfly ahora: recomprar el cuerpo vendido (put+call en
    `body_strike`) y vender las alas compradas. = (short_put + short_call − long_put − long_call)
    × 100 a precios mid actuales. `None` si falta alguna pata en la cadena en vivo (hueco de datos
    — el que llama decide no marcar en vez de inventar un valor)."""
    short_put = _leg_at(chain, "put", body_strike)
    short_call = _leg_at(chain, "call", body_strike)
    long_put = _leg_at(chain, "put", long_put_strike)
    long_call = _leg_at(chain, "call", long_call_strike)
    if None in (short_put, short_call, long_put, long_call):
        return None
    net = (short_put.mid_price + short_call.mid_price) - (long_put.mid_price + long_call.mid_price)
    return round(net * CONTRACT_MULTIPLIER, 2)


def butterfly_intrinsic_close_value(
    spot: float, body_strike: float, long_put_strike: float, long_call_strike: float
) -> float:
    """Valor de cierre al VENCIMIENTO (0DTE) por valor intrínseco — cuando las patas ya no están
    en la cadena en vivo porque venció. Payoff del butterfly a un precio `spot` de liquidación."""
    short_put = max(body_strike - spot, 0.0)
    short_call = max(spot - body_strike, 0.0)
    long_put = max(long_put_strike - spot, 0.0)
    long_call = max(spot - long_call_strike, 0.0)
    net = (short_put + short_call) - (long_put + long_call)
    return round(net * CONTRACT_MULTIPLIER, 2)


def butterfly_unrealized(entry_net_credit: float, close_value: float) -> float:
    """P&L no realizado en dólares: crédito cobrado al abrir − costo de cerrar ahora."""
    return round(entry_net_credit - close_value, 2)


def should_close_butterfly(
    unrealized_pnl: float, expired: bool, settings: IntradayButterflySettings,
    entry_net_credit: float | None = None,
) -> tuple[bool, str | None]:
    """Regla de salida del day-trade: cierre de GANANCIA al `profit_target_pct` del crédito EN TODO
    momento (usuario 2026-08-07: 30%, para tener más entradas). Si profit_target_pct = 0, cae al
    objetivo en dólares (`profit_target`). También −stop_loss o vencimiento (0DTE)."""
    if expired:
        return True, "expired"
    pct = getattr(settings, "profit_target_pct", 0.0)
    if pct > 0 and entry_net_credit and unrealized_pnl >= pct * entry_net_credit:
        return True, "profit_target"
    if pct <= 0 and settings.profit_target > 0 and unrealized_pnl >= settings.profit_target:
        return True, "profit_target"
    if settings.stop_loss > 0 and unrealized_pnl <= -settings.stop_loss:
        return True, "stop_loss"
    return False, None


def build_iron_butterfly(
    chain: OptionChain, spot: float, direction: str, settings: IntradayButterflySettings
) -> ButterflyBuild | None:
    """Arma el Iron Butterfly para la reversión: cuerpo cerca del spot, corrido `breakeven_offset_pct`
    hacia la dirección de la reversión; alas de `wing_width` puntos. Devuelve None si no encuentra
    los strikes o si la pérdida máxima supera `max_collateral` (no cumple el tope de riesgo)."""
    if spot <= 0:
        return None
    offset = settings.breakeven_offset_pct * spot
    body_target = spot - offset if direction == "revert_down" else spot + offset

    short_put = _nearest(chain, "put", body_target)
    if short_put is None:
        return None
    body_strike = short_put.strike
    short_call = _nearest(chain, "call", body_strike)
    long_put = _nearest(chain, "put", body_strike - settings.wing_width)
    long_call = _nearest(chain, "call", body_strike + settings.wing_width)
    if short_call is None or long_put is None or long_call is None:
        return None
    # El cuerpo debe ser un strike (put y call en el mismo); las alas, distintas de él.
    if long_put.strike >= body_strike or long_call.strike <= body_strike or short_call.strike != body_strike:
        return None

    net_credit_ps = (short_put.mid_price + short_call.mid_price) - (long_put.mid_price + long_call.mid_price)
    if net_credit_ps <= 0:
        return None
    net_credit = net_credit_ps * CONTRACT_MULTIPLIER
    # El crédito máximo (= ganancia máxima) tiene que poder llegar al objetivo, si no la operación
    # nunca cerraría en ganancia (usuario 2026-08-05: "lo mejor de los robots").
    if settings.profit_target > 0 and net_credit < settings.profit_target:
        return None
    put_wing = body_strike - long_put.strike
    call_wing = long_call.strike - body_strike
    max_loss = max(put_wing, call_wing) * CONTRACT_MULTIPLIER - net_credit
    if max_loss > settings.max_collateral:
        return None  # las alas dejan un riesgo mayor al tope permitido

    return ButterflyBuild(
        body_strike=body_strike,
        put_wing_width=put_wing,
        call_wing_width=call_wing,
        legs=[
            ("sell", "put", short_put),
            ("sell", "call", short_call),
            ("buy", "put", long_put),
            ("buy", "call", long_call),
        ],
        net_credit=round(net_credit, 2),
        max_profit=round(net_credit, 2),
        max_loss=round(max_loss, 2),
        lower_breakeven=round(body_strike - net_credit_ps, 2),
        upper_breakeven=round(body_strike + net_credit_ps, 2),
    )
