"""Resumen compacto de lo que vio y decidió el robot en un día. Para COMPARAR dos máquinas.

Por qué existe (mudanza al servidor, agosto 2026): durante la validación conviven el robot de la
Mac —que opera de verdad— y el del servidor —que solo mira—. La pregunta que hay que poder
responder es "¿el servidor está viendo y decidiendo lo mismo?", y mirar dos dashboards en paralelo
no sirve: son cientos de símbolos y la vista es distinta según cuándo se refresque cada uno.

Esto imprime un bloque de texto corto y DETERMINISTA. Se corre igual en las dos máquinas y se
comparan las salidas línea por línea.

    python3 scripts/reporte_dia.py            # hoy
    python3 scripts/reporte_dia.py 2026-08-25 # otro día

Sin dependencias: solo la biblioteca estándar. Así corre con el `python3` del sistema, sin
necesidad de activar el entorno virtual ni de que estén instaladas las librerías del proyecto.
"""

from __future__ import annotations

import re
import socket
import sqlite3
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
BASE = RAIZ / "data" / "app.db"
AJUSTES = RAIZ / "config" / "settings.yaml"

# Símbolos de referencia para comparar precios entre máquinas. Fijos y ordenados a propósito: si
# se eligieran "los primeros que aparezcan" cada máquina podría mostrar unos distintos y la
# comparación no serviría de nada.
REFERENCIA = ("AAL", "AAPL", "AMZN", "C", "NCLH", "NU", "NVDA", "SPY")


def _modo() -> str:
    """MODO REAL o MODO PRUEBA, leído del bloque live_trading de settings.yaml."""
    try:
        texto = AJUSTES.read_text()
    except Exception:
        return "desconocido (no se pudo leer settings.yaml)"
    bloque = texto.split("live_trading:", 1)
    if len(bloque) < 2:
        return "desconocido (sin bloque live_trading)"
    cuerpo = bloque[1]
    # Cortar en la siguiente sección de primer nivel para no leer claves de otro bloque.
    corte = re.search(r"\n(?=[a-zA-Z_])", cuerpo)
    if corte:
        cuerpo = cuerpo[: corte.start()]

    def _val(clave: str) -> bool | None:
        m = re.search(rf"^\s*{clave}\s*:\s*(true|false)", cuerpo, re.M | re.I)
        return m.group(1).lower() == "true" if m else None

    enabled, dry, kill = _val("enabled"), _val("dry_run"), _val("kill_switch")
    if dry or kill or enabled is False:
        frenos = [n for n, v in (("dry_run", dry), ("kill_switch", kill)) if v]
        if enabled is False:
            frenos.append("enabled=false")
        return f"MODO PRUEBA (mira, no opera) · frenos: {', '.join(frenos) or 'ninguno'}"
    return "MODO REAL (opera con plata de verdad)"


def _linea(etiqueta: str, valor) -> str:
    return f"  {etiqueta:.<34} {valor}"


def main() -> None:
    dia = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    if not BASE.exists():
        raise SystemExit(f"No encuentro la base en {BASE}")

    conn = sqlite3.connect(f"file:{BASE}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    q = lambda sql, *a: conn.execute(sql, a).fetchall()          # noqa: E731
    uno = lambda sql, *a: (q(sql, *a) or [[None]])[0][0]         # noqa: E731

    print("=" * 66)
    print(f"  REPORTE DEL DIA {dia}")
    print("=" * 66)
    print(_linea("maquina", socket.gethostname()))
    print(_linea("modo", _modo()))
    print()

    print("-- LO QUE MIRO --")
    print(_linea("simbolos analizados", uno(
        "select count(distinct symbol) from indicator_snapshots where snapshot_date=?", dia)))
    print(_linea("primer analisis", uno(
        "select min(snapshot_ts) from indicator_snapshots where snapshot_date=?", dia)))
    print(_linea("ultimo analisis", uno(
        "select max(snapshot_ts) from indicator_snapshots where snapshot_date=?", dia)))
    print()

    print("-- PRECIOS DE REFERENCIA (deben coincidir entre maquinas) --")
    for s in REFERENCIA:
        fila = q("""select price, rsi_14, iv_rank from indicator_snapshots
                    where snapshot_date=? and symbol=? order by snapshot_ts desc limit 1""", dia, s)
        if fila:
            r = fila[0]
            precio = f"${r['price']:,.2f}" if r["price"] is not None else "—"
            rsi = f"{r['rsi_14']:.1f}" if r["rsi_14"] is not None else "—"
            ivr = f"{r['iv_rank']:.0f}" if r["iv_rank"] is not None else "—"
            print(_linea(s, f"{precio:>12}   RSI {rsi:>5}   IVR {ivr:>4}"))
        else:
            print(_linea(s, "sin datos hoy"))
    print()

    print("-- LO QUE DECIDIO --")
    acciones = q("""select action, count(*) n from robot_decisions
                    where decision_date=? group by action order by n desc""", dia)
    if acciones:
        for r in acciones:
            print(_linea(r["action"], r["n"]))
    else:
        print(_linea("(ninguna decision registrada)", ""))
    print()

    print("-- MOTIVOS MAS FRECUENTES --")
    motivos = q("""select substr(reason,1,52) m, count(*) n from robot_decisions
                   where decision_date=? group by m order by n desc limit 6""", dia)
    for r in motivos or []:
        print(_linea((r["m"] or "—"), r["n"]))
    if not motivos:
        print(_linea("(sin motivos)", ""))
    print()

    print("-- SALUD --")
    errores_red = uno("""select count(*) from robot_decisions where decision_date=?
                         and (reason like '%ConnectError%' or reason like '%nodename%'
                              or reason like '%Network is unreachable%')""", dia)
    print(_linea("decisiones falladas por RED", f"{errores_red}   <- deberia ser 0"))
    print(_linea("candidatos encontrados", uno(
        "select count(*) from candidate_contracts where snapshot_date=?", dia)))
    mejor = q("""select symbol, conviction_score from candidate_contracts
                 where snapshot_date=? order by conviction_score desc limit 1""", dia)
    print(_linea("mejor candidato", f"{mejor[0]['symbol']} ({mejor[0]['conviction_score']})" if mejor else "—"))
    print()

    print("-- POSICIONES ABIERTAS (segun ESTA base) --")
    # COALESCE(closed, 0), no `closed = 0`. En SQL un campo vacío no es igual a 0: es desconocido, y
    # `NULL = 0` no da ni verdadero ni falso, así que la fila queda AFUERA de la cuenta.
    #
    # Se vio el 2026-09-09, el primer día operando desde el servidor: el reporte decía 3 naked reales
    # abiertos cuando había 5. Las dos que faltaban —WFC y otra— nunca tuvieron ese campo escrito, y
    # el reporte las daba por inexistentes. El motor NUNCA se equivocó: sus consultas ya usaban
    # COALESCE y las estaba gestionando bien. El que mentía era este resumen, que es justamente el
    # que el usuario mira para saber si está todo en orden. Un tablero que subcuenta posiciones
    # reales es peor que no tener tablero.
    print(_linea("naked put reales", uno(
        "select count(*) from live_order_log where sent=1 and order_status='FILLED' "
        "and COALESCE(closed, 0) = 0")))
    print(_linea("iron condor reales", uno(
        "select count(*) from real_condor_positions where status not in ('closed','cancelled')")))
    print(_linea("simuladas", uno(
        "select count(*) from simulated_positions where status='open'")))
    print("=" * 66)


if __name__ == "__main__":
    main()
