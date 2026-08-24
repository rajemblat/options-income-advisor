"""Backtest REAL del Iron Condor 0DTE: baja barras de un minuto y replica el motor en vivo.

    python scripts/backtest_condor_real.py                 # los últimos 120 días hábiles
    python scripts/backtest_condor_real.py --dias 250
    python scripts/backtest_condor_real.py --paso 1        # revisar la posición cada minuto (lento)

CORRELO CON EL MERCADO CERRADO. Baja una sesión por llamada a Schwab; con el robot operando le
estaría comiendo el cupo de peticiones justo cuando lo necesita.

Las sesiones bajadas quedan en data/cache_intradia/, así que la segunda corrida es instantánea y no
vuelve a pedirle nada a Schwab.

CÓMO SE CALIBRA, que es lo que hace que esto valga algo:
No existe cadena de opciones histórica, así que las patas se valúan con Black-Scholes y hace falta
una volatilidad implícita. En vez de suponerla, el script la deduce de TUS PROPIAS OPERACIONES: toma
los condors que abriste (reales y de papel), con sus strikes y el crédito que cobraste, y busca qué
IV explica ese crédito. Después compara esa IV con la volatilidad realizada de la apertura de esos
mismos días y saca el factor de corrección. Ese factor es el que se aplica a toda la historia.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from options_advisor.backtest import condor_intraday as ci  # noqa: E402
from options_advisor.broker import get_broker_client  # noqa: E402
from options_advisor.broker.models import IntradayBar  # noqa: E402
from options_advisor.config import load_settings  # noqa: E402
from options_advisor.scheduler.market_calendar import is_market_day  # noqa: E402
from options_advisor.simulator import learning  # noqa: E402

CACHE = PROJECT_ROOT / "data" / "cache_intradia"
SIMBOLO = "$SPX"


# --------------------------------------- datos ---------------------------------------

def _barras_de(broker, dia: date) -> list[IntradayBar]:
    """Barras de un minuto de esa sesión. Usa caché en disco; solo pega a Schwab si no la tiene."""
    CACHE.mkdir(parents=True, exist_ok=True)
    archivo = CACHE / f"spx_{dia.isoformat()}.json"
    if archivo.exists():
        crudo = json.loads(archivo.read_text())
        return [IntradayBar(symbol=SIMBOLO, timestamp=datetime.fromisoformat(b["t"]),
                            open=b["o"], high=b["h"], low=b["l"], close=b["c"], volume=b["v"])
                for b in crudo]
    barras = broker.get_intraday_bars(SIMBOLO, dia, interval_minutes=1)
    archivo.write_text(json.dumps([{"t": b.timestamp.isoformat(), "o": b.open, "h": b.high,
                                    "l": b.low, "c": b.close, "v": b.volume} for b in barras]))
    time.sleep(0.35)          # no atropellar la API
    return barras


def _sesiones(broker, dias: int) -> list[list[IntradayBar]]:
    salida, d, faltantes, pedidas = [], date.today() - timedelta(days=1), 0, 0
    while len(salida) < dias and faltantes < 25:
        if is_market_day(d):
            try:
                barras = _barras_de(broker, d)
            except Exception as exc:
                print(f"  {d}: no se pudo ({exc.__class__.__name__})")
                barras = []
            pedidas += 1
            if len(barras) >= 120:
                salida.append(barras)
                faltantes = 0
            else:
                faltantes += 1        # Schwab dejó de tener historia intradía tan atrás
            if pedidas % 20 == 0:
                print(f"  … {len(salida)} sesiones cargadas (hasta {d})")
        d -= timedelta(days=1)
    return list(reversed(salida))


# ------------------------------------- calibración -------------------------------------

def _iv_implicita_de_tus_condors(conn) -> dict[date, list[float]]:
    """Para cada día que operaste, qué IV explica el crédito que cobraste."""
    conn.row_factory = sqlite3.Row
    consulta = """select entry_date, entry_ts, entry_spot, short_put_strike, short_call_strike,
                  long_put_strike, entry_net_credit from {} where entry_spot > 0"""
    por_dia: dict[date, list[float]] = {}
    for tabla in ("iron_condor_positions", "real_condor_positions"):
        try:
            filas = conn.execute(consulta.format(tabla)).fetchall()
        except sqlite3.Error:
            continue
        for r in filas:
            if not r["entry_ts"]:
                continue
            ts = datetime.fromisoformat(str(r["entry_ts"]))
            minutos = max(1, 16 * 60 - (ts.hour * 60 + ts.minute))
            ancho = r["short_put_strike"] - r["long_put_strike"]
            iv = ci.calibrar_iv(r["entry_spot"], r["short_put_strike"], r["short_call_strike"],
                                ancho, minutos, r["entry_net_credit"])
            por_dia.setdefault(date.fromisoformat(str(r["entry_date"])), []).append(iv)
    return por_dia


def _factor(sesiones, implicitas: dict[date, list[float]], p: ci.CondorIntradiaParams) -> tuple[float, int]:
    """Cuánto hay que multiplicar la volatilidad REALIZADA de la apertura para llegar a la IMPLÍCITA
    que se observó ese mismo día. Devuelve (factor, cuántos días se pudieron comparar)."""
    razones = []
    for barras in sesiones:
        dia = barras[0].timestamp.date()
        if dia not in implicitas:
            continue
        realizada = ci.iv_de_la_apertura(barras[: p.minutos_de_apertura], 1.0)
        if not realizada:
            continue
        objetivo = sorted(implicitas[dia])[len(implicitas[dia]) // 2]
        razones.append(objetivo / realizada)
    if not razones:
        return 1.0, 0
    razones.sort()
    return razones[len(razones) // 2], len(razones)


# --------------------------------------- informe ---------------------------------------

def _informe(ops: list[ci.CondorIntradiaTrade], sesiones: int) -> None:
    if not ops:
        print("\nNo hubo ni una sesión que pasara el filtro de día calmo.")
        return
    ganadoras = [t for t in ops if t.pnl > 0]
    pnl = sum(t.pnl for t in ops)
    motivos: dict[str, list[float]] = {}
    for t in ops:
        motivos.setdefault(t.motivo, []).append(t.pnl)

    print("\n" + "=" * 70)
    print("  IRON CONDOR 0DTE — SIMULACIÓN CON TUS REGLAS, MINUTO A MINUTO")
    print("=" * 70)
    print(f"  Sesiones analizadas .......... {sesiones}")
    print(f"  Días que pasaron el filtro ... {len(ops)}  ({len(ops)/sesiones:.0%} de las ruedas)")
    print(f"  Aciertos ..................... {len(ganadoras)}/{len(ops)} = {len(ganadoras)/len(ops):.1%}")
    print(f"  P&L total .................... ${pnl:,.2f}")
    print(f"  P&L por operación ............ ${pnl/len(ops):,.2f}")
    print(f"  Crédito medio cobrado ........ ${sum(t.credito for t in ops)/len(ops):,.2f}")
    print(f"  Minutos hasta el cierre ...... mediana {sorted(t.salida_minuto for t in ops)[len(ops)//2]}")
    print("\n  Cómo terminó cada una:")
    for motivo, valores in sorted(motivos.items(), key=lambda kv: -len(kv[1])):
        print(f"    {motivo:22} {len(valores):>4}  ({len(valores)/len(ops):>5.1%})   "
              f"P&L ${sum(valores):>10,.2f}   media ${sum(valores)/len(valores):>8,.2f}")
    peor = min(ops, key=lambda t: t.pnl)
    print(f"\n  Peor día: {peor.fecha}  ${peor.pnl:,.2f}  ({peor.motivo})")
    equity, pico, caida = 0.0, 0.0, 0.0
    for t in ops:
        equity += t.pnl
        pico = max(pico, equity)
        caida = min(caida, equity - pico)
    print(f"  Caída máxima acumulada: ${caida:,.2f}")
    print("=" * 70)
    print("  Recordá: los strikes y la mecánica son reales; el precio de cada pata es modelado")
    print("  (no existe cadena de opciones histórica). No incluye comisiones ni deslizamiento,")
    print("  así que esto es el techo optimista.")
    print("=" * 70)


# Variantes del filtro de entrada, para poder MEDIR en vez de opinar. La hipótesis del usuario
# (2026-08-23) es que un día muy movido pero lateral es BUENO para vender prima, y que el filtro
# actual —que exige quietud— descarta justo los días que más pagan.
#
# Tiene sentido teórico: vender prima gana cuando la implícita supera al movimiento realizado, y el
# filtro actual mide el movimiento sin mirar el pago. Pero "tiene sentido" no es "funciona", así que
# se corre la misma historia por cada variante y se comparan los resultados.
# (etiqueta, rango mínimo, rango máximo, deriva máxima)
# La deriva separa "volátil y LATERAL" de "volátil y CON TENDENCIA": es la mitad de la hipótesis que
# el rango por sí solo no puede capturar.
VARIANTES = (
    ("Tu regla actual (quieto)",    0.0,    0.004, 1.0),
    ("Sin filtro (todos)",          0.0,    9.99,  1.0),
    ("Solo días movidos",           0.004,  9.99,  1.0),
    ("Movidos y LATERALES",         0.004,  9.99,  0.40),
    ("Movidos y MUY laterales",     0.004,  9.99,  0.25),
    ("Movidos CON TENDENCIA",       0.004,  9.99,  1.0),   # el control: se filtra abajo
    ("Cualquier rango, lateral",    0.0,    9.99,  0.40),
)


def _comparar(sesiones, p: ci.CondorIntradiaParams) -> None:
    from dataclasses import replace
    print("\n" + "=" * 88)
    print("  COMPARACIÓN DE FILTROS DE ENTRADA — la misma historia, distintas reglas para entrar")
    print("=" * 88)
    print(f"  {'variante':30} {'días':>6} {'acierto':>9} {'P&L total':>12} {'P&L/op':>10} {'crédito':>9}")
    print("  " + "-" * 84)
    for etiqueta, minimo, maximo, deriva in VARIANTES:
        ops = ci.backtest(sesiones, replace(p, rango_minimo=minimo, calm_range_pct=maximo,
                                            deriva_maxima=deriva))
        if "CON TENDENCIA" in etiqueta:      # el control: lo que la lateralidad DESCARTA
            laterales = {t.fecha for t in ci.backtest(
                sesiones, replace(p, rango_minimo=minimo, calm_range_pct=maximo, deriva_maxima=0.40))}
            ops = [t for t in ops if t.fecha not in laterales]
        if not ops:
            print(f"  {etiqueta:30} {'0':>6}   (ninguna sesión pasó el filtro)")
            continue
        ganadas = sum(1 for t in ops if t.pnl > 0)
        pnl = sum(t.pnl for t in ops)
        credito = sum(t.credito for t in ops) / len(ops)
        print(f"  {etiqueta:30} {len(ops):>6} {ganadas/len(ops):>8.1%} {pnl:>12,.0f} "
              f"{pnl/len(ops):>10,.2f} {credito:>9,.0f}")
    print("=" * 88)
    print("  Comparar POR OPERACIÓN, no por total: los días movidos son menos y el total los castiga.")
    print("  La fila que decide es 'Movidos y LATERALES' contra 'Movidos CON TENDENCIA': si la primera")
    print("  gana y la segunda pierde, la lateralidad es el filtro que faltaba y el rango solo no basta.")
    print("=" * 88)


# Barrido de SALIDAS. El backtest del 23/08 mostró que el problema del condor no está en cuándo
# entra —el filtro de quietud resultó el mejor de siete variantes— sino en la relación entre lo que
# gana y lo que pierde: +$88 contra -$113, que exige acertar 56% para empatar.
#
# Cerrar antes da ganancias más chicas pero más frecuentes, y menos posiciones llegan vivas al stop.
# Cerrar más tarde hace lo contrario. Dónde está el óptimo es una pregunta empírica.
OBJETIVOS = (0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50)
STOPS = (60.0, 80.0, 100.0, 140.0, 200.0)


def _barrer_salidas(sesiones, p: ci.CondorIntradiaParams) -> None:
    from dataclasses import replace
    print("\n" + "=" * 88)
    print("  BARRIDO DE SALIDAS — P&L por operación según objetivo de ganancia y stop")
    print("=" * 88)
    print(f"  {'objetivo':>10}" + "".join(f"{'stop $'+str(int(s)):>13}" for s in STOPS))
    print("  " + "-" * 84)
    mejor = None
    for objetivo in OBJETIVOS:
        fila = f"  {objetivo:>9.0%}"
        for stop in STOPS:
            ops = ci.backtest(sesiones, replace(p, profit_target_pct=objetivo, stop_loss_dollars=stop))
            if not ops:
                fila += f"{'—':>13}"
                continue
            por_op = sum(t.pnl for t in ops) / len(ops)
            fila += f"{por_op:>13,.2f}"
            if mejor is None or por_op > mejor[0]:
                acierto = sum(1 for t in ops if t.pnl > 0) / len(ops)
                mejor = (por_op, objetivo, stop, len(ops), acierto)
        print(fila)
    print("=" * 88)
    if mejor:
        por_op, objetivo, stop, n, acierto = mejor
        print(f"  Mejor combinación de esta muestra: cerrar al {objetivo:.0%} con stop ${stop:,.0f}")
        print(f"      {n} operaciones · acierto {acierto:.1%} · ${por_op:,.2f} por operación")
        print()
        print("  OJO: es la mejor de ESTA muestra, no la mejor. Con 34 sesiones, elegir la casilla")
        print("  ganadora de una tabla de 35 es la forma clásica de ajustarse al ruido y llevarse una")
        print("  sorpresa en vivo. Sirve para ver la FORMA de la tabla — si hay una zona ancha que")
        print("  funciona, o si el óptimo es un pico solitario, que es señal de que es casualidad.")
    print("=" * 88)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=120)
    ap.add_argument("--paso", type=int, default=5, help="cada cuántos minutos se revisa la posición")
    ap.add_argument("--comparar", action="store_true",
                    help="probar además otros filtros de entrada y comparar")
    ap.add_argument("--salidas", action="store_true",
                    help="barrer objetivo de ganancia y stop, y comparar el P&L por operación")
    args = ap.parse_args()

    settings = load_settings()
    from options_advisor.storage import db
    conn = db.connect(settings.database.resolved_path())
    cfg = learning.effective_condor(conn, settings.intraday_condor)

    p = ci.CondorIntradiaParams(
        short_delta=float(getattr(cfg, "short_delta_max", 0.15)),
        wing_width=float(getattr(cfg, "wing_width", 10.0)),
        profit_target_pct=float(getattr(cfg, "profit_target_pct", 0.35)),
        profit_target_early_pct=float(getattr(cfg, "profit_target_early_pct", 0.20)),
        early_window_minutes=float(getattr(cfg, "early_window_minutes", 30.0)),
        stop_loss_dollars=float(getattr(cfg, "stop_loss_dollars", 100.0)),
        calm_range_pct=float(getattr(cfg, "calm_range_pct", 0.004)),
        paso_minutos=args.paso,
    )
    print("REGLAS QUE SE VAN A SIMULAR (las tuyas, de la config efectiva):")
    print(f"  cortos a delta {p.short_delta} · alas de {p.wing_width:g} puntos")
    print(f"  cierra al {p.profit_target_pct:.0%} del crédito · {p.profit_target_early_pct:.0%} si llega "
          f"en los primeros {p.early_window_minutes:.0f} min · stop ${p.stop_loss_dollars:,.0f}")
    print(f"  solo días calmos: rango de la primera media hora <= {p.calm_range_pct:.2%}")

    print(f"\nBajando hasta {args.dias} sesiones de {SIMBOLO} (una llamada por sesión, con caché)…")
    broker = get_broker_client(settings)
    sesiones = _sesiones(broker, args.dias)
    if not sesiones:
        raise SystemExit("No se pudo bajar ninguna sesión intradía.")
    print(f"  {len(sesiones)} sesiones, de {sesiones[0][0].timestamp.date()} a {sesiones[-1][0].timestamp.date()}")

    implicitas = _iv_implicita_de_tus_condors(conn)
    factor, comparables = _factor(sesiones, implicitas, p)
    if comparables:
        print(f"\nCalibración: {comparables} día(s) donde operaste de verdad y se puede comparar.")
        print(f"  la volatilidad implícita fue {factor:.2f}× la realizada de la apertura")
    else:
        print("\nCalibración: no hay días en común con tus operaciones; se usa factor 1.0 (menos confiable).")
    p.iv_calibration = factor

    _informe(ci.backtest(sesiones, p), len(sesiones))
    if args.comparar:
        _comparar(sesiones, p)
    if args.salidas:
        _barrer_salidas(sesiones, p)


if __name__ == "__main__":
    main()
