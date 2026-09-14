"""¿Está todo listo para operar mañana? — la verificación completa, en un solo comando.

    python scripts/listo_para_operar.py

Nació el 2026-09-14, después de que el token de Schwab venciera un viernes y el robot pasara tres
días sin poder ver el mercado sin que nadie se enterara. Pero la razón de fondo es más simple: el
usuario pregunta "¿está todo al 100%?" antes de cada rueda, y hasta ahora la respuesta salía de
media docena de comandos distintos armados a mano cada vez. Eso no escala y, peor, se olvida un
chequeo justo el día que importaba.

Revisa, en orden de qué tan temprano te arruina el día:

    1. la máquina        — que esta sea la que opera en real (y que no haya dos robots)
    2. Schwab            — que el token esté vivo Y que la cuenta responda de verdad
    3. los servicios     — robot y dashboard arriba
    4. los maestros      — real encendido, sin dry-run, sin kill switch, sin pausas
    5. los naked         — armado del día, cupo, lista de símbolos
    6. el condor         — armado del día, cupo, crédito mínimo, filtro de calma
    7. el STOP del iron  — que exista y que el robot pueda ejecutarlo
    8. lo abierto        — posiciones que el robot tiene que cuidar mañana

Solo LEE: no manda órdenes, no arma nada, no escribe en la base. Se puede correr con el mercado
abierto y el robot operando.

Lo que está en manos del usuario (armar el día) sale como recordatorio, no como falla: a la noche
NUNCA va a estar armado, y marcarlo en rojo entrenaría a ignorar los rojos.
"""

from __future__ import annotations

import platform
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from options_advisor.broker import get_broker_client  # noqa: E402
from options_advisor.broker.schwab_auth import read_refresh_token_seconds_left  # noqa: E402
from options_advisor.config import load_settings  # noqa: E402
from options_advisor.execution import live_condor_engine as lce  # noqa: E402
from options_advisor.scheduler.maquina_real import es_la_maquina_real, nombre_de_esta_maquina  # noqa: E402
from options_advisor.storage import db  # noqa: E402
from options_advisor.storage import repository as repo  # noqa: E402

OK, MAL, OJO, TODO_TUYO = "🟢", "🔴", "🟡", "👉"

_problemas: list[str] = []
_pendientes: list[str] = []


def _l(estado: str, titulo: str, detalle: str = "") -> None:
    print(f"{estado}  {titulo}" + (f" — {detalle}" if detalle else ""))


def _check(ok: bool, titulo: str, detalle_ok: str = "", detalle_mal: str = "") -> bool:
    """Un chequeo que, si falla, impide operar. Se acumula para el veredicto final."""
    _l(OK if ok else MAL, titulo, detalle_ok if ok else detalle_mal)
    if not ok:
        _problemas.append(f"{titulo} — {detalle_mal}" if detalle_mal else titulo)
    return ok


def _recordatorio(hecho: bool, titulo: str, detalle_hecho: str, que_hacer: str) -> None:
    """Algo que depende del usuario. No es una falla: es una tarea de mañana a la mañana."""
    if hecho:
        _l(OK, titulo, detalle_hecho)
    else:
        _l(TODO_TUYO, titulo, que_hacer)
        _pendientes.append(f"{titulo}: {que_hacer}")


def _servicio_activo(unidad: str) -> bool:
    if platform.system() == "Darwin":
        return True   # en la Mac lo maneja launchd; este script vive en el servidor
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", unidad],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def main() -> int:
    settings = load_settings()
    lt = settings.live_trading
    cond = settings.intraday_condor
    hoy = date.today()
    conn = db.connect(settings.database.resolved_path())
    conn.row_factory = sqlite3.Row

    print(f"\n╔═ ¿LISTO PARA OPERAR? · {hoy} · {nombre_de_esta_maquina()} ═╗\n")

    # 1. LA MÁQUINA ────────────────────────────────────────────────────────────
    print("— La máquina —")
    esperado = getattr(lt, "real_machine_hostname", "") or None
    _check(es_la_maquina_real(esperado), "Esta es la máquina que opera en REAL",
           f"designada: {esperado or '(sin candado)'}",
           f"el config designa '{esperado}' y esta es '{nombre_de_esta_maquina()}' — "
           "acá NO se va a operar en real")

    # 2. SCHWAB ───────────────────────────────────────────────────────────────
    print("\n— Schwab —")
    seg = read_refresh_token_seconds_left()
    if seg is None:
        _check(False, "Token de Schwab", "", "no hay tokens guardados — corré scripts/schwab_login.py")
    elif seg <= 0:
        _check(False, "Token de Schwab", "",
               f"VENCIDO hace {abs(int(seg)) // 3600} h — corré scripts/schwab_login.py YA")
    else:
        dias, horas = int(seg // 86400), int((seg % 86400) // 3600)
        vence = f"vence en {dias}d {horas}h"
        if seg < 72 * 3600:
            _l(OJO, "Token de Schwab", f"{vence} — reconectá antes de que te agarre operando")
            _pendientes.append(f"Reconectar Schwab ({vence}): scripts/schwab_login.py")
        else:
            _l(OK, "Token de Schwab", vence)

    # Que el token no esté vencido NO alcanza: hay que pedirle algo real y ver que conteste.
    # El 11/09 el token figuraba guardado y la cuenta no respondía.
    try:
        broker = get_broker_client(settings)
        # La MISMA llamada que hace el motor antes de cada orden (`_resolve_account`): si esta
        # falla, no hay operatoria real, por más que el token figure guardado.
        cuenta = broker.resolve_account_hash(getattr(lt, "account_number", "") or None)
        _check(bool(cuenta), "La cuenta RESPONDE", "Schwab contesta y la cuenta resuelve",
               "Schwab no resolvió ninguna cuenta")
    except Exception as exc:
        _check(False, "La cuenta RESPONDE", "", f"{type(exc).__name__}: {exc}")

    # 3. LOS SERVICIOS ────────────────────────────────────────────────────────
    print("\n— Los servicios —")
    for unidad, que_es in (("lokshn-robot", "el robot"), ("lokshn-dashboard", "el dashboard")):
        _check(_servicio_activo(unidad), f"{que_es.capitalize()} está corriendo", unidad,
               f"apagado — `systemctl --user restart {unidad}`")

    # 4. LOS MAESTROS ─────────────────────────────────────────────────────────
    print("\n— Los maestros —")
    _check(lt.enabled, "Trading real ENCENDIDO", "", "live_trading.enabled está en false")
    _check(not lt.dry_run, "Sin dry-run", "manda órdenes de verdad",
           "dry_run=true: arma las órdenes y NO las manda")
    _check(not repo.is_live_kill_switch(conn), "Kill switch apagado", "",
           "el kill switch está ACTIVO — desactivalo desde Real Market")
    _check(not repo.is_all_paused(conn), "Sin pausa maestra", "",
           "hay una pausa maestra activa")

    # 5. LOS NAKED ────────────────────────────────────────────────────────────
    print("\n— Naked puts —")
    _recordatorio(repo.is_live_armed(conn, hoy), "Armado de hoy", "listo",
                  "apretá «START del día» en Real Market (se resetea cada medianoche)")
    _, marca_naked = repo.live_rearm_mark(conn, hoy)
    cupo_n = repo.get_max_live_orders_per_day(conn, lt.max_orders_per_day, hoy)
    usados_n = repo.count_live_approved_opens_today(conn, hoy, after_id=marca_naked)
    _l(OK if usados_n < cupo_n else OJO, "Cupo del día", f"{usados_n}/{cupo_n}")
    simbolos = list(getattr(lt, "allowed_symbols", None) or [])
    _check(bool(simbolos), "Lista de símbolos", f"{len(simbolos)} habilitados",
           "la whitelist está vacía: no va a mirar ningún símbolo")

    # 6. EL CONDOR ────────────────────────────────────────────────────────────
    print("\n— Iron Condor —")
    _check(cond.enabled and getattr(cond, "live_enabled", False), "Condor real encendido", "",
           f"enabled={cond.enabled} live_enabled={getattr(cond, 'live_enabled', False)}")
    _recordatorio(repo.is_condor_live_armed(conn, hoy), "Autorización de hoy", "lista",
                  "apretá «Autorizar condor HOY» en Real Market (botón aparte de los naked)")
    _check(not repo.is_condor_real_paused(conn), "Sin pausa del condor real", "",
           "el condor real está pausado")
    ts_c, id_c = repo.condor_rearm_mark(conn, hoy)
    cupo_c = repo.get_condor_live_max_per_day(conn, getattr(cond, "live_max_per_day", 1), hoy)
    usados_c = repo.count_real_condor_opens_today(conn, hoy, after_id=id_c)
    _l(OK if usados_c < cupo_c else OJO, "Cupo del día", f"{usados_c}/{cupo_c}")
    halt = getattr(cond, "stop_loss_streak_halt", 0)
    racha = repo.real_condor_consecutive_stop_losses_today(conn, hoy, since_ts=ts_c)
    _l(OK if not (halt and racha >= halt) else OJO, "Racha de stop-loss",
       f"{racha}" + (f"/{halt} (frena al llegar)" if halt else " (sin freno)"))
    _l(OK, "Crédito mínimo real", f"${getattr(cond, 'live_min_credit', 0):,.0f} por condor")
    _l(OK, "Filtro de día calmo", f"rango intradía ≤ {cond.calm_range_pct * 100:.2f}%"
       + (" · congelado, el aprendizaje no lo toca"
          if "calm_range_pct" in (getattr(cond, "learning_frozen", None) or []) else ""))

    # 7. EL STOP DEL IRON ─────────────────────────────────────────────────────
    # Lo que más le importa al usuario y lo que más caro salió cuando falló: el 02/09 un condor sin
    # stop ejecutable costó $420. El stop NO lo pone el broker — lo ejecuta el robot en cada tick,
    # así que sin visión del mercado no hay stop, por más que el número esté configurado.
    print("\n— El STOP del iron (lo ejecuta el robot, no el broker) —")
    stop = getattr(cond, "stop_loss_dollars", 0) or 0
    _check(stop > 0, "Stop configurado", f"${stop:,.0f} por condor",
           "stop_loss_dollars en 0: el condor quedaría SIN stop")
    ok_cuidar, porque = lce.puede_cuidar_la_posicion(conn)
    _check(ok_cuidar, "El robot PUEDE ejecutarlo", "tiene visión del mercado",
           f"{porque} — sin esto no abre, justamente para no quedar sin protección")

    # 8. LO ABIERTO ───────────────────────────────────────────────────────────
    print("\n— Lo que hay abierto —")
    puts = repo.get_open_real_put_positions(conn)
    condors = repo.get_open_real_condor_positions(conn)
    _l(OK, "Naked puts vivos", f"{len(puts)}")
    _l(OK, "Condors vivos", f"{len(condors)}")
    for c in condors:
        _l(OK if c["status"] == "open" else OJO,
           f"  condor #{c['id']} {c['underlying']}",
           f"{c['short_put_strike']:.0f}/{c['short_call_strike']:.0f} · {c['status']} · "
           f"vence {c['expiration_date']}")

    # VEREDICTO ───────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    if _problemas:
        print(f"\n{MAL} NO está listo. Hay que resolver:\n")
        for p in _problemas:
            print(f"   · {p}")
    else:
        print(f"\n{OK} Todo lo que depende del sistema está en orden.")
    if _pendientes:
        print(f"\n{TODO_TUYO} Te toca a vos, mañana a la mañana:\n")
        for p in _pendientes:
            print(f"   · {p}")
    print()
    return 1 if _problemas else 0


if __name__ == "__main__":
    raise SystemExit(main())
