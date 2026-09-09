"""¿Por qué el condor real no abre? — el tick contado en voz alta.

    python scripts/por_que_no_abre_condor.py

Nació el 2026-09-09: el usuario re-autorizó el condor, el tick corría cada minuto y no abría nada, y
el log no decía por qué. Casi todas las compuertas de `process_real_condor_cycle` son un `return`
mudo — buena decisión para no llenar el log con una línea por minuto, mala cuando estás mirando el
reloj esperando que abra.

Este script recorre EXACTAMENTE las mismas compuertas, en el mismo orden, y dice cuál es la primera
que frena. No manda ninguna orden ni escribe nada: solo lee el mercado y la base. Se puede correr con
el robot operando.

Lee, no toca. Si algún día el motor cambia de orden, este script hay que actualizarlo con él — por eso
cada bloque cita la línea del motor que replica.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from options_advisor.broker import get_broker_client  # noqa: E402
from options_advisor.config import load_settings  # noqa: E402
from options_advisor.execution import live_condor_engine as lce  # noqa: E402
from options_advisor.scheduler.market_calendar import market_session  # noqa: E402
from options_advisor.simulator import iron_condor, iron_condor_engine, learning  # noqa: E402
from options_advisor.storage import db  # noqa: E402
from options_advisor.storage import repository as repo  # noqa: E402

FRENA = "🔴 FRENA AQUÍ"
PASA = "🟢"


def _p(ok: bool, titulo: str, detalle: str = "") -> bool:
    """Imprime una compuerta. Devuelve `ok` para poder cortar en la primera que frena."""
    print(f"{PASA if ok else FRENA}  {titulo}" + (f" — {detalle}" if detalle else ""))
    return ok


def main() -> int:
    settings = load_settings()
    cfg_base = settings.intraday_condor
    lt = settings.live_trading
    hoy = date.today()
    conn = db.connect(PROJECT_ROOT / "data" / "app.db")
    conn.row_factory = sqlite3.Row

    print(f"\n=== ¿Por qué no abre el condor real? · {hoy} ===\n")

    # --- Maestros y mercado (las compuertas del "camino rápido" del motor) ---
    if not _p(cfg_base.enabled and getattr(cfg_base, "live_enabled", False),
              "Sistema del condor real prendido",
              f"enabled={cfg_base.enabled} live_enabled={getattr(cfg_base, 'live_enabled', False)}"):
        return 1
    if not _p(lt.enabled and not lt.dry_run, "Trading real prendido y sin dry-run",
              f"enabled={lt.enabled} dry_run={lt.dry_run}"):
        return 1
    if not _p(not repo.is_live_kill_switch(conn), "Kill switch apagado"):
        return 1
    sesion = market_session()
    if not _p(sesion == "abierto", "Mercado abierto", f"sesión: {sesion}"):
        return 1

    # --- Armado del día y su marca de re-armado (usuario 2026-09-09) ---
    if not _p(repo.is_condor_live_armed(conn, hoy), "Condor AUTORIZADO hoy",
              "apretá «Autorizar condor HOY» en Real Market"):
        return 1
    ts, mid = repo.condor_rearm_mark(conn, hoy)
    print(f"    ↳ última autorización: {ts or '(sin marca)'} · cuenta desde el id {mid}")

    # --- Pausas ---
    pausas = {
        "pausa del condor REAL": repo.is_condor_real_paused(conn),
        "pausa del condor (compartida con papel)": repo.is_condor_paused(conn),
        "pausa MAESTRA de todo": repo.is_all_paused(conn),
    }
    if not _p(not any(pausas.values()), "Sin pausas activas",
              ", ".join(k for k, v in pausas.items() if v) or "ninguna"):
        return 1

    # --- Freno por racha de stop-loss y cupo del día, ambos DESDE la marca ---
    halt = getattr(cfg_base, "stop_loss_streak_halt", 0)
    racha = repo.real_condor_consecutive_stop_losses_today(conn, hoy, since_ts=ts)
    if not _p(not (halt > 0 and racha >= halt), "Freno por racha de stop-loss",
              f"{racha} seguidos desde la última autorización · frena con {halt}. "
              "Re-autorizá para ponerlo en cero."):
        return 1
    cupo = repo.get_condor_live_max_per_day(conn, getattr(cfg_base, "live_max_per_day", 1), hoy)
    usados = repo.count_real_condor_opens_today(conn, hoy, after_id=mid)
    if not _p(not (cupo > 0 and usados >= cupo), "Cupo del día",
              f"{usados}/{cupo} desde la última autorización "
              f"(en todo el día van {repo.count_real_condor_opens_today(conn, hoy)}). "
              "Re-autorizá para recuperarlo."):
        return 1

    # --- Checklist de despegue: sin visión no hay stop, así que no se abre ---
    ok_cuidar, porque = lce.puede_cuidar_la_posicion(conn)
    if not _p(ok_cuidar, "Puede cuidar la posición (el stop es del robot)", porque):
        return 1

    # --- Datos de mercado ---
    broker = get_broker_client(settings)
    cfg = learning.effective_condor(conn, cfg_base)   # perillas aprendidas, igual que el motor
    symbol = cfg.underlying
    bars = broker.get_intraday_bars(symbol, hoy, interval_minutes=cfg.timeframe_minutes)
    if not _p(bool(bars), f"Barras intradía de {symbol}", f"{len(bars or [])} barras"):
        return 1
    full_chain = broker.get_option_chain(symbol, expiration_range_days=lce.CHAIN_FETCH_RANGE_DAYS)
    expiration = lce._pick_0dte_expiration(full_chain, hoy, cfg.dte) if full_chain else None
    if not _p(expiration is not None, "Cadena del vencimiento 0DTE", str(expiration)):
        return 1
    chain = lce._chain_for_expiration(full_chain, expiration)
    spot = bars[-1].close
    print(f"    ↳ spot {symbol}: {spot:,.2f} · vencimiento {expiration}")

    # --- La señal: calma, ventana horaria, VIX ---
    vix_chg = iron_condor_engine.vix_change_pct(broker)
    signal = iron_condor.evaluate_condor_signal(bars, cfg, vix_change_pct=vix_chg)
    ok_senal = signal.calm and signal.in_window and signal.vix_ok
    _p(ok_senal, "Señal de entrada",
       f"calmo={signal.calm} · en ventana ({cfg.entry_window_start}–{cfg.entry_window_end})="
       f"{signal.in_window} · VIX ok={signal.vix_ok} (VIX {vix_chg:+.2f}% hoy)"
       if vix_chg is not None else
       f"calmo={signal.calm} · en ventana={signal.in_window} · VIX ok={signal.vix_ok}")
    if not ok_senal:
        motivos = []
        if not signal.calm:
            motivos.append(f"el día NO viene calmo: rango intradía {signal.day_range_pct * 100:.2f}%, "
                           f"el tope es {cfg.calm_range_pct * 100:.2f}%")
        if not signal.in_window:
            motivos.append(f"fuera de la ventana de entrada "
                           f"({cfg.entry_window_start}–{cfg.entry_window_end} hora de NY)")
        if not signal.vix_ok:
            motivos.append("el VIX está subiendo más de lo permitido")
        print(f"    ↳ {'; '.join(motivos)}")
        return 1

    # --- El armado concreto: strikes y, sobre todo, el crédito mínimo ---
    build = iron_condor.build_iron_condor(chain, spot, cfg)
    if not _p(build is not None, "Encuentra strikes que paguen el mínimo",
              f"crédito mínimo exigido: ${getattr(cfg, 'min_credit', 0):,.0f} "
              f"(piso real: ${getattr(cfg, 'live_min_credit', 0):,.0f}). "
              "Si frena acá, la cadena no paga lo suficiente ahora mismo."):
        return 1

    print(f"    ↳ {build.short_put_strike:.0f}/{build.short_call_strike:.0f} "
          f"alas {build.long_put_strike:.0f}/{build.long_call_strike:.0f} · "
          f"crédito ${build.net_credit:,.2f} por contrato · "
          f"riesgo máximo ${build.max_loss:,.2f}")

    print("\n🟢 TODAS LAS COMPUERTAS ABIERTAS — el próximo tick debería mandar la orden.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
