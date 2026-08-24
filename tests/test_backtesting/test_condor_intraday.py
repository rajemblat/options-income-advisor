"""Backtest del Iron Condor 0DTE que replica el motor en vivo, minuto a minuto.

Por qué existe (usuario 2026-08-23: "podemos hacer el backtesting real de iron condor como lo haces
en real market"): el backtest diario es una moneda cargada. Con una barra por día solo puede
preguntar "¿el cierre quedó dentro de 1σ?", lo que ignora que el motor cierra al 35% del crédito
apenas se toca, sale al 20% si llega rápido, y corta en -$100. Una posición que sube 40% a las 11 y
se da vuelta a las 15 cuenta como perdedora en el modelo diario; en la realidad se cerró ganando.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone

from options_advisor.backtest import condor_intraday as ci
from options_advisor.broker.models import IntradayBar


def _sesion(dia: date, vol_pct: float, semilla: int = 1, barras: int = 390) -> list[IntradayBar]:
    """Una rueda sintética de barras de un minuto con volatilidad controlada."""
    rnd = random.Random(semilla)
    precio = 7700.0
    t0 = datetime(dia.year, dia.month, dia.day, 13, 30, tzinfo=timezone.utc)
    salida = []
    for i in range(barras):
        apertura = precio
        precio = max(1.0, apertura * (1 + rnd.gauss(0, vol_pct / math.sqrt(barras))))
        salida.append(IntradayBar(symbol="$SPX", timestamp=t0 + timedelta(minutes=i), open=apertura,
                                  high=max(apertura, precio) * 1.0002,
                                  low=min(apertura, precio) * 0.9998, close=precio, volume=1000))
    return salida


# --- la calibración contra operaciones reales, que es lo que hace que esto valga algo ---

def test_la_iv_calibrada_reproduce_un_credito_real():
    """Condor REAL del usuario, 20/08/2026: $SPX a 7684.83, cortos 7640/7720, alas de 10 puntos,
    crédito cobrado $195. La IV que sale de ahí tiene que devolver exactamente ese crédito."""
    iv = ci.calibrar_iv(7684.83, 7640.0, 7720.0, 10.0, minutos_restantes=388, credito_real=195.0)
    t = 388 / ci.MINUTOS_POR_ANIO
    assert abs(ci.valor_del_condor(7684.83, 7640.0, 7720.0, 10.0, t, iv) - 195.0) < 0.5
    # Medida sobre las 32 operaciones reales y de papel del usuario: mediana 7.05%, rango 5-9%.
    assert 0.04 < iv < 0.12, f"IV fuera del rango observado en las operaciones reales: {iv:.2%}"


def test_el_credito_modelado_cae_en_el_rango_real():
    """El modelo diario asumía $1.332 de crédito donde la realidad fueron $150-$215. Ese error de
    7 veces hacía que su P&L no sirviera para nada."""
    operacion = ci.simular_sesion(_sesion(date(2026, 3, 2), 0.003), ci.CondorIntradiaParams())
    assert operacion is not None
    assert 80 < operacion.credito < 400, f"Crédito fuera de escala: ${operacion.credito}"


# --- el filtro de día calmo, sin mirar el futuro ---

def test_no_opera_un_dia_violento():
    p = ci.CondorIntradiaParams()
    assert ci.simular_sesion(_sesion(date(2026, 3, 3), 0.030, semilla=2), p) is None


def test_opera_un_dia_calmo():
    p = ci.CondorIntradiaParams()
    assert ci.simular_sesion(_sesion(date(2026, 3, 2), 0.003), p) is not None


def test_el_dia_calmo_se_juzga_SOLO_con_la_apertura():
    """La decisión de entrar usa únicamente las primeras barras. Cambiar lo que pasa DESPUÉS no
    puede alterar si se entró o no — si lo alterara, el backtest estaría operando con información
    del futuro y todos sus resultados serían mentira."""
    p = ci.CondorIntradiaParams()
    base = _sesion(date(2026, 3, 2), 0.003)
    torcida = list(base)
    for i in range(p.minutos_de_apertura + 5, len(torcida)):      # derrumbe posterior a la entrada
        b = torcida[i]
        torcida[i] = IntradayBar(symbol=b.symbol, timestamp=b.timestamp, open=b.open * 0.90,
                                 high=b.high * 0.90, low=b.low * 0.90, close=b.close * 0.90,
                                 volume=b.volume)
    assert (ci.simular_sesion(base, p) is None) == (ci.simular_sesion(torcida, p) is None)


# --- que cierre como cierra el motor ---

def test_respeta_el_stop_en_dolares():
    p = ci.CondorIntradiaParams(stop_loss_dollars=100.0)
    ops = ci.backtest([_sesion(date(2026, 1, 1) + timedelta(days=i), 0.003 if i % 3 else 0.02, i)
                       for i in range(120)], p)
    stops = [t for t in ops if t.motivo == "stop_loss"]
    assert stops, "Ninguna operación cortó por stop: el escenario de prueba no ejercita esa rama"
    for t in stops:
        assert t.pnl <= -p.stop_loss_dollars * 0.85, f"Stop mal aplicado: {t.pnl}"


def test_el_objetivo_de_ganancia_cierra_antes_del_final():
    """El motor no espera al vencimiento: cierra apenas toca el 35% del crédito. Es exactamente lo
    que el backtest diario no podía ver."""
    p = ci.CondorIntradiaParams()
    ops = ci.backtest([_sesion(date(2026, 1, 1) + timedelta(days=i), 0.003, i) for i in range(40)], p)
    ganancias = [t for t in ops if t.motivo.startswith("profit_target")]
    assert ganancias
    for t in ganancias:
        assert t.pnl >= t.credito * p.profit_target_early_pct * 0.9
        assert t.salida_minuto < 360, "Cerró tan tarde que no se distingue del vencimiento"


def test_la_salida_rapida_solo_aplica_en_la_ventana_temprana():
    p = ci.CondorIntradiaParams()
    ops = ci.backtest([_sesion(date(2026, 1, 1) + timedelta(days=i), 0.003, i) for i in range(60)], p)
    for t in ops:
        if t.motivo == "profit_target_early":
            assert t.salida_minuto <= p.early_window_minutes


# --- los strikes ---

def test_los_cortos_quedan_fuera_del_dinero_y_en_la_grilla():
    p = ci.CondorIntradiaParams(strike_step=5.0)
    t = ci.simular_sesion(_sesion(date(2026, 3, 2), 0.003), p)
    assert t is not None
    assert t.short_put < t.spot_entrada < t.short_call, "Los cortos tienen que estar OTM"
    for strike in (t.short_put, t.short_call):
        assert strike % p.strike_step == 0, f"{strike} no cae en la grilla de {p.strike_step}"


def test_menos_delta_aleja_los_cortos():
    lejos = ci.strike_por_delta(7700, 0.004, 0.07, 0.05, es_put=True, paso=5.0)
    cerca = ci.strike_por_delta(7700, 0.004, 0.07, 0.30, es_put=True, paso=5.0)
    assert lejos < cerca, "Delta más chico tiene que vender MÁS lejos del dinero"


# --- la banda de entrada, para poder MEDIR la hipótesis en vez de discutirla ---
# (usuario 2026-08-23: "quizás el mercado bajó mucho pero se mantiene lateral ahí abajo con mucha
#  volatilidad — eso es bueno para vender prima")

def _mezcla(n: int = 90) -> list[list[IntradayBar]]:
    """Sesiones alternando quietas, medias y movidas."""
    return [_sesion(date(2026, 1, 1) + timedelta(days=i), [0.002, 0.005, 0.012][i % 3], i)
            for i in range(n)]


def test_el_rango_minimo_nace_apagado():
    """La regla del usuario no cambia por defecto: el filtro nuevo tiene que ser inocuo hasta que
    alguien lo pida explícitamente."""
    assert ci.CondorIntradiaParams().rango_minimo == 0.0


def test_el_rango_minimo_descarta_los_dias_quietos():
    from dataclasses import replace
    p = ci.CondorIntradiaParams()
    sesiones = _mezcla()
    quietos = ci.backtest(sesiones, replace(p, rango_minimo=0.0, calm_range_pct=0.004))
    movidos = ci.backtest(sesiones, replace(p, rango_minimo=0.004, calm_range_pct=9.99))
    assert quietos and movidos
    assert {t.fecha for t in quietos}.isdisjoint({t.fecha for t in movidos}), \
        "Las dos bandas se solapan: no son mutuamente excluyentes"


def test_en_dias_movidos_se_cobra_mas_prima():
    """El mecanismo detrás de la hipótesis: como los cortos se eligen por DELTA y no por puntos
    fijos, más volatilidad los aleja del precio Y sube el crédito. Por eso un día movido puede pagar
    más sin que aumente la probabilidad de que lo toquen."""
    from dataclasses import replace
    p = ci.CondorIntradiaParams()
    sesiones = _mezcla()
    quietos = ci.backtest(sesiones, replace(p, rango_minimo=0.0, calm_range_pct=0.004))
    movidos = ci.backtest(sesiones, replace(p, rango_minimo=0.004, calm_range_pct=9.99))
    credito_quieto = sum(t.credito for t in quietos) / len(quietos)
    credito_movido = sum(t.credito for t in movidos) / len(movidos)
    assert credito_movido > credito_quieto


def test_en_dias_movidos_los_cortos_quedan_mas_lejos():
    """La otra mitad del mecanismo, y la razón por la que el acierto no se desploma."""
    from dataclasses import replace
    p = ci.CondorIntradiaParams()
    sesiones = _mezcla()

    def _distancia_media(ops):
        return sum((t.short_call - t.short_put) / t.spot_entrada for t in ops) / len(ops)

    quietos = ci.backtest(sesiones, replace(p, rango_minimo=0.0, calm_range_pct=0.004))
    movidos = ci.backtest(sesiones, replace(p, rango_minimo=0.004, calm_range_pct=9.99))
    assert _distancia_media(movidos) > _distancia_media(quietos)


# --- lateralidad: separar "volátil y lateral" de "volátil y con tendencia" ---
# El usuario lo describió así el 2026-08-23: "bajó mucho pero se mantiene LATERAL ahí abajo con
# mucha volatilidad". El rango por sí solo no distingue esos dos casos: los dos tienen rango grande.

def _sesion_con_deriva(dia: date, vol: float, deriva: float, semilla: int) -> list[IntradayBar]:
    """Como `_sesion`, pero con una tendencia sostenida hacia un lado."""
    rnd = random.Random(semilla)
    precio = 7700.0
    t0 = datetime(dia.year, dia.month, dia.day, 13, 30, tzinfo=timezone.utc)
    salida = []
    for i in range(390):
        apertura = precio
        precio = max(1.0, apertura * (1 + rnd.gauss(deriva / 390, vol / math.sqrt(390))))
        salida.append(IntradayBar(symbol="$SPX", timestamp=t0 + timedelta(minutes=i), open=apertura,
                                  high=max(apertura, precio) * 1.0002,
                                  low=min(apertura, precio) * 0.9998, close=precio, volume=1000))
    return salida


def test_la_deriva_distingue_oscilar_de_tender():
    """Dos días con la MISMA volatilidad: uno oscila y vuelve, el otro se va derecho."""
    lateral = _sesion_con_deriva(date(2026, 4, 1), 0.012, 0.0, 10)[:30]
    tendencia = _sesion_con_deriva(date(2026, 4, 2), 0.012, -0.025, 11)[:30]
    assert ci.deriva_de_la_apertura(lateral) < ci.deriva_de_la_apertura(tendencia)


def test_el_rango_solo_NO_los_distingue():
    """Este es el punto: por eso el filtro actual, que mira solo el rango, no puede aprovechar la
    hipótesis del usuario."""
    lateral = _sesion_con_deriva(date(2026, 4, 1), 0.012, 0.0, 10)[:30]
    tendencia = _sesion_con_deriva(date(2026, 4, 2), 0.012, -0.025, 11)[:30]
    r1, r2 = ci.rango_de_la_apertura(lateral), ci.rango_de_la_apertura(tendencia)
    assert 0.5 < r1 / r2 < 2.0, "Los rangos deberían ser comparables; si no, el test no prueba nada"


def test_el_filtro_de_lateralidad_nace_apagado():
    assert ci.CondorIntradiaParams().deriva_maxima == 1.0


def test_el_filtro_de_lateralidad_descarta_los_dias_con_tendencia():
    from dataclasses import replace
    p = ci.CondorIntradiaParams()
    sesiones = [_sesion_con_deriva(date(2026, 1, 1) + timedelta(days=i), 0.012,
                                   0.0 if i % 2 else -0.025, i) for i in range(60)]
    todos = ci.backtest(sesiones, replace(p, rango_minimo=0.004, calm_range_pct=9.99))
    laterales = ci.backtest(sesiones, replace(p, rango_minimo=0.004, calm_range_pct=9.99,
                                              deriva_maxima=0.40))
    assert 0 < len(laterales) < len(todos), "El filtro no descartó nada, o descartó todo"
    for t in laterales:
        assert t.fecha in {x.fecha for x in todos}


def test_el_credito_no_se_resiente_al_filtrar_por_lateralidad():
    """Filtrar por lateralidad no puede costarte prima: los días que quedan siguen siendo los
    volátiles, que son los que pagan. Si el filtro dejara solo días baratos, no serviría de nada."""
    from dataclasses import replace
    p = ci.CondorIntradiaParams()
    sesiones = [_sesion_con_deriva(date(2026, 1, 1) + timedelta(days=i), 0.012,
                                   0.0 if i % 2 else -0.025, i) for i in range(120)]
    base = replace(p, rango_minimo=0.004, calm_range_pct=9.99)
    todos = ci.backtest(sesiones, base)
    laterales = ci.backtest(sesiones, replace(base, deriva_maxima=0.40))
    assert todos and laterales
    medio = lambda ops: sum(t.credito for t in ops) / len(ops)   # noqa: E731
    assert medio(laterales) > medio(todos) * 0.85


def test_NO_se_puede_validar_la_lateralidad_con_datos_sinteticos():
    """Guardia contra mi propio error (2026-08-23).

    En una primera corrida sintética el filtro de lateralidad dio 53.8% de acierto contra 30.1% de
    los días con tendencia, y estuve a punto de presentarlo como evidencia. Con otra muestra del
    mismo generador el resultado SE DIO VUELTA: 46.7% contra 54.2%.

    La razón es estructural: un paseo aleatorio no tiene memoria. Que la primera media hora haya
    sido lateral no dice NADA sobre las seis horas siguientes, por construcción del generador. Los
    datos sintéticos no pueden responder esta pregunta ni a favor ni en contra, y cualquier
    resultado que den es ruido de la semilla.

    Este test fija esa conclusión: verifica que dos muestras distintas del mismo generador dan
    respuestas contradictorias. Si algún día dejara de ser cierto, sería porque alguien le puso
    memoria al generador — y entonces habría que revisar qué se está midiendo en realidad.

    La pregunta sólo se contesta con historia REAL de $SPX: scripts/backtest_condor_real.py --comparar
    """
    from dataclasses import replace
    p = ci.CondorIntradiaParams()
    base = replace(p, rango_minimo=0.004, calm_range_pct=9.99)

    def _ventaja(n: int, paso: int) -> float | None:
        sesiones = [_sesion_con_deriva(date(2026, 1, 1) + timedelta(days=i), 0.012,
                                       0.0 if i % paso else -0.025, i) for i in range(n)]
        laterales = ci.backtest(sesiones, replace(base, deriva_maxima=0.40))
        fechas = {t.fecha for t in laterales}
        otros = [t for t in ci.backtest(sesiones, base) if t.fecha not in fechas]
        if not laterales or not otros:
            return None
        acierto = lambda ops: sum(1 for t in ops if t.pnl > 0) / len(ops)   # noqa: E731
        return acierto(laterales) - acierto(otros)

    a, b = _ventaja(240, 3), _ventaja(120, 2)
    assert a is not None and b is not None
    assert (a > 0) != (b > 0), (
        "Las dos muestras coinciden en el signo. Antes se contradecían, que es lo que probaba que "
        "el resultado era ruido. Si ahora concuerdan, revisá si el generador cambió — NO tomes esto "
        "como validación de la estrategia."
    )
