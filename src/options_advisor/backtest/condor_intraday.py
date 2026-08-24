"""Backtest del Iron Condor 0DTE replicando el motor REAL, minuto a minuto.

Por qué existe (usuario 2026-08-23: "podemos hacer ahora el backtesting real de iron condor como lo
haces en real market"):

`backtest_iron_condor_daily` es una moneda cargada. Con una sola barra por día solo puede preguntar
"¿el cierre quedó dentro de ~1σ de la apertura?", y de ahí saca ganó/perdió. Eso ignora TODO lo que
hace el motor de verdad: entrar solo en días calmos, cerrar al 35% del crédito apenas se toca, salir
al 20% si llega rápido, cortar en -$100. Una posición que sube 40% a las 11 y se da vuelta a las 15
cuenta como perdedora en el modelo diario, cuando en la realidad se habría cerrado ganando a las 11.

Este módulo usa las barras de UN MINUTO que Schwab sí devuelve (`get_intraday_bars`) y recorre la
sesión como la recorre el robot: mira si el día viene calmo, arma el condor a los strikes que
corresponden por delta, y después revisa la posición cada pocos minutos preguntando lo mismo que
pregunta el motor. La primera condición que se cumple es la que cierra.

QUÉ SIGUE SIENDO UNA APROXIMACIÓN, para que nadie le crea de más:
  - No existe cadena de opciones histórica, así que las 4 patas se valúan con Black-Scholes. Los
    strikes y la mecánica son reales; el precio de cada pata es modelado.
  - La volatilidad implícita se estima de la volatilidad realizada de la propia sesión, multiplicada
    por `iv_calibration`. Ese factor NO se inventa: se calibra contra condors REALES del usuario
    (ver `calibrar_iv`), que es la única forma honesta de fijarlo.
  - No modela deslizamiento ni comisiones. El resultado es el techo optimista.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from statistics import NormalDist

from options_advisor.broker.models import IntradayBar

RISK_FREE = 0.045
MULTIPLICADOR = 100.0          # un contrato de SPX = 100 x el índice
MINUTOS_DE_RUEDA = 390         # 9:30 a 16:00
MINUTOS_POR_ANIO = 252 * MINUTOS_DE_RUEDA


@dataclass
class CondorIntradiaParams:
    """Las reglas del motor en vivo (`config/settings.yaml`, bloque `intraday_condor`)."""
    short_delta: float = 0.15
    wing_width: float = 10.0
    profit_target_pct: float = 0.35
    profit_target_early_pct: float = 0.20
    early_window_minutes: float = 30.0
    stop_loss_dollars: float = 100.0
    calm_range_pct: float = 0.004      # rango MÁXIMO de la apertura para entrar (tu regla actual)
    # Rango MÍNIMO. Nace en 0 (sin efecto), pero existe para poder probar la hipótesis del usuario
    # (2026-08-23): "quizás el mercado bajó mucho pero se mantiene lateral ahí abajo con mucha
    # volatilidad — eso es bueno para vender prima".
    #
    # Tiene fundamento: vender prima gana cuando la volatilidad IMPLÍCITA (lo que te pagan) supera al
    # movimiento REALIZADO (lo que te amenaza). El filtro actual mide el movimiento y no mira el
    # pago, así que descarta justo los días que más pagan. Y como los cortos se eligen por DELTA y no
    # por puntos fijos, un día volátil ya coloca los cortos más lejos por sí solo.
    #
    # Si esto sirve o no es una pregunta empírica, no de opinión. Por eso se puede medir en vez de
    # discutirse.
    rango_minimo: float = 0.0
    # LATERALIDAD. Cuánto del rango de la apertura terminó siendo desplazamiento NETO:
    #     |cierre - apertura| / (máximo - mínimo)
    # Cerca de 0 = el precio osciló y volvió (choppy, lateral): el mejor escenario del condor, porque
    # cobrás la prima de un mercado nervioso sin que el nerviosismo vaya a ningún lado.
    # Cerca de 1 = el precio fue derecho para un lado (tendencia): el mismo rango, pero es el día que
    # te barre un corto.
    #
    # El rango solo NO los distingue —los dos tienen rango grande— y por eso el filtro actual, que
    # mira solo el rango, no puede aprovechar lo que el usuario describió el 2026-08-23: "bajó mucho
    # pero se mantiene LATERAL ahí abajo con mucha volatilidad".
    #
    # 1.0 = sin efecto (nace apagado).
    deriva_maxima: float = 1.0
    # Cuántos minutos de apertura se miran para juzgar "día calmo" y cuándo se entra. En vivo la
    # ventana efectiva es 09:30-10:00 ET (ver el comentario del huso en settings.yaml), así que el
    # robot decide con lo que vio en la primera media hora. Acá se hace igual: nunca se mira una
    # barra posterior al momento de decidir — eso sería operar con información del futuro.
    minutos_de_apertura: int = 30
    strike_step: float = 5.0        # SPX cotiza strikes de 5 en 5
    iv_calibration: float = 1.0     # se fija con `calibrar_iv`, no a ojo
    paso_minutos: int = 5           # cada cuántos minutos se revisa la posición


@dataclass
class CondorIntradiaTrade:
    fecha: date
    spot_entrada: float
    short_put: float
    short_call: float
    credito: float                  # en dólares, por contrato
    salida_minuto: int              # minutos desde la entrada
    motivo: str                     # profit_target / profit_target_early / stop_loss / expiracion
    pnl: float
    iv_usada: float


# --------------------------------- Black-Scholes (4 patas) ---------------------------------

def _d1_d2(spot: float, strike: float, t: float, iv: float) -> tuple[float, float]:
    if t <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return (float("inf") if spot > strike else float("-inf")), 0.0
    d1 = (math.log(spot / strike) + (RISK_FREE + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    return d1, d1 - iv * math.sqrt(t)


def precio_put(spot: float, strike: float, t: float, iv: float) -> float:
    if t <= 0:
        return max(0.0, strike - spot)
    d1, d2 = _d1_d2(spot, strike, t, iv)
    N = NormalDist().cdf
    return strike * math.exp(-RISK_FREE * t) * N(-d2) - spot * N(-d1)


def precio_call(spot: float, strike: float, t: float, iv: float) -> float:
    if t <= 0:
        return max(0.0, spot - strike)
    d1, d2 = _d1_d2(spot, strike, t, iv)
    N = NormalDist().cdf
    return spot * N(d1) - strike * math.exp(-RISK_FREE * t) * N(d2)


def valor_del_condor(spot: float, sp: float, sc: float, ancho: float, t: float, iv: float) -> float:
    """Lo que COSTARÍA recomprar el condor ahora, en dólares. El P&L es credito - este valor."""
    corto = precio_put(spot, sp, t, iv) + precio_call(spot, sc, t, iv)
    largo = precio_put(spot, sp - ancho, t, iv) + precio_call(spot, sc + ancho, t, iv)
    return (corto - largo) * MULTIPLICADOR


def strike_por_delta(spot: float, t: float, iv: float, delta: float, es_put: bool, paso: float) -> float:
    """Strike cuyo delta es ~`delta`, redondeado a la grilla. Se invierte la normal en vez de
    buscar por tanteo: delta de un corto OTM ≈ N(-d2) para el put y N(d2) para el call."""
    z = NormalDist().inv_cdf(1.0 - min(max(delta, 0.001), 0.499))
    despl = math.exp((-z if es_put else z) * iv * math.sqrt(max(t, 1e-9)))
    crudo = spot * despl
    # Se redondea SIEMPRE alejándose del dinero: vender más cerca de lo pedido sería cobrar una
    # prima que el robot no habría cobrado.
    return (math.floor(crudo / paso) if es_put else math.ceil(crudo / paso)) * paso


# --------------------------------- Volatilidad de la sesión ---------------------------------

def iv_de_la_apertura(barras: list[IntradayBar], calibracion: float = 1.0) -> float | None:
    """Volatilidad anualizada estimada con los retornos minuto a minuto de la apertura.

    Es lo único disponible sin cadena histórica. `calibracion` corrige el sesgo estructural entre
    volatilidad realizada e implícita (la implícita suele ser mayor), y se fija midiendo contra
    operaciones reales — nunca a ojo."""
    if len(barras) < 10:
        return None
    retornos = []
    for previa, actual in zip(barras, barras[1:]):
        if previa.close > 0 and actual.close > 0:
            retornos.append(math.log(actual.close / previa.close))
    if len(retornos) < 5:
        return None
    media = sum(retornos) / len(retornos)
    var = sum((r - media) ** 2 for r in retornos) / (len(retornos) - 1)
    iv = math.sqrt(var * MINUTOS_POR_ANIO) * calibracion
    return iv if iv > 0.01 else None


def deriva_de_la_apertura(barras: list[IntradayBar]) -> float | None:
    """Qué fracción del rango terminó siendo desplazamiento neto: |cierre-apertura| / (máx-mín).

    Es el número que separa "volátil y lateral" de "volátil y con tendencia". Cerca de 0 el precio
    fue y volvió; cerca de 1 se fue derecho para un lado. Para vender prima, el primero es amigo y
    el segundo es el que te barre un corto — y el rango, por sí solo, no los distingue."""
    if not barras:
        return None
    alto, bajo = max(b.high for b in barras), min(b.low for b in barras)
    if alto <= bajo:
        return None
    return abs(barras[-1].close - barras[0].open) / (alto - bajo)


def rango_de_la_apertura(barras: list[IntradayBar]) -> float | None:
    """(máximo - mínimo) / apertura de las barras dadas. Es el gatillo de 'día calmo' del motor."""
    if not barras or barras[0].open <= 0:
        return None
    return (max(b.high for b in barras) - min(b.low for b in barras)) / barras[0].open


# --------------------------------- La simulación de una rueda ---------------------------------

def simular_sesion(barras: list[IntradayBar], p: CondorIntradiaParams) -> CondorIntradiaTrade | None:
    """Recorre UNA sesión como la recorre el robot. None si ese día no habría operado."""
    if len(barras) < p.minutos_de_apertura + 30:
        return None

    apertura = barras[: p.minutos_de_apertura]
    rango = rango_de_la_apertura(apertura)
    if rango is None or rango > p.calm_range_pct or rango < p.rango_minimo:
        return None                                   # el día no entra en la banda de rango pedida
    if p.deriva_maxima < 1.0:
        deriva = deriva_de_la_apertura(apertura)
        if deriva is None or deriva > p.deriva_maxima:
            return None                               # se movió, pero con tendencia: no es lateral

    iv = iv_de_la_apertura(apertura, p.iv_calibration)
    if iv is None:
        return None

    resto = barras[p.minutos_de_apertura:]
    spot = resto[0].close
    minutos_restantes = len(resto)
    t_entrada = minutos_restantes / MINUTOS_POR_ANIO

    sp = strike_por_delta(spot, t_entrada, iv, p.short_delta, True, p.strike_step)
    sc = strike_por_delta(spot, t_entrada, iv, p.short_delta, False, p.strike_step)
    credito = valor_del_condor(spot, sp, sc, p.wing_width, t_entrada, iv)
    if credito <= 0:
        return None

    def _cerrar(minuto: int, valor: float, motivo: str) -> CondorIntradiaTrade:
        return CondorIntradiaTrade(
            fecha=barras[0].timestamp.date(), spot_entrada=spot, short_put=sp, short_call=sc,
            credito=round(credito, 2), salida_minuto=minuto, motivo=motivo,
            pnl=round(credito - valor, 2), iv_usada=round(iv, 4))

    for i in range(p.paso_minutos, minutos_restantes, p.paso_minutos):
        t = (minutos_restantes - i) / MINUTOS_POR_ANIO
        valor = valor_del_condor(resto[i].close, sp, sc, p.wing_width, t, iv)
        pnl = credito - valor
        # El orden importa y es el del motor: primero la salida rápida, después el objetivo normal,
        # y el stop en cualquier momento. La PRIMERA condición que se cumple es la que cierra.
        if i <= p.early_window_minutes and pnl >= credito * p.profit_target_early_pct:
            return _cerrar(i, valor, "profit_target_early")
        if pnl >= credito * p.profit_target_pct:
            return _cerrar(i, valor, "profit_target")
        if pnl <= -p.stop_loss_dollars:
            return _cerrar(i, valor, "stop_loss")

    # Llegó al cierre: liquida a valor intrínseco.
    final = valor_del_condor(resto[-1].close, sp, sc, p.wing_width, 0.0, iv)
    return _cerrar(minutos_restantes, final, "expiracion")


def backtest(sesiones: list[list[IntradayBar]], p: CondorIntradiaParams | None = None) -> list[CondorIntradiaTrade]:
    p = p or CondorIntradiaParams()
    salida = []
    for barras in sesiones:
        try:
            t = simular_sesion(barras, p)
        except Exception:
            continue
        if t is not None:
            salida.append(t)
    return salida


# --------------------------------- Calibración contra la realidad ---------------------------------

def calibrar_iv(spot: float, short_put: float, short_call: float, ancho: float,
                minutos_restantes: int, credito_real: float,
                iv_baja: float = 0.02, iv_alta: float = 3.0) -> float:
    """Qué volatilidad implícita reproduce un crédito REAL observado.

    Es el ancla honesta de todo esto: en vez de suponer un factor, se toma un condor que el usuario
    abrió de verdad —con sus strikes y su crédito— y se busca la IV que lo explica. Búsqueda binaria
    porque el valor del condor crece de forma monótona con la volatilidad."""
    t = minutos_restantes / MINUTOS_POR_ANIO
    for _ in range(200):
        medio = (iv_baja + iv_alta) / 2
        if valor_del_condor(spot, short_put, short_call, ancho, t, medio) < credito_real:
            iv_baja = medio
        else:
            iv_alta = medio
    return (iv_baja + iv_alta) / 2
