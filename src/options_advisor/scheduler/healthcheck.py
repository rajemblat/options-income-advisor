from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from options_advisor.alerts.real_trades import detect_and_alert_real_trades
from options_advisor.broker.base import BrokerClient
from options_advisor.config import Settings
from options_advisor.scheduler.market_calendar import market_session

logger = logging.getLogger(__name__)

# Cadencia real de `real_trade_detection` (settings.scheduler.real_trade_poll_interval_minutes,
# 3 por defecto) — 2 ciclos sin ninguna línea nueva ya es sospechoso, pero se da margen extra
# (una llamada lenta puntual a Schwab no debería disparar un reinicio innecesario). Piso de 6
# min independiente del intervalo configurado, para no ser demasiado agresivo si algún día se
# configura un intervalo de 1-2 min.
STALE_MULTIPLIER = 3
MIN_STALE_MINUTES = 6

# Tope de la ventana de catch-up tras reparar — pedir de más es lento (timeout real observado
# pidiendo 3 días de órdenes, ver `alerts/real_trades.py`), así que no vale la pena ir más allá
# de esto aunque el cuelgue haya durado más — el próximo ciclo normal (15 min de solapamiento)
# termina de cubrir cualquier resto.
MAX_CATCHUP_MINUTES = 180


def is_stale(now: datetime, last_log_mtime: datetime, poll_interval_minutes: int) -> bool:
    """True si pasó más tiempo del esperado desde la última escritura al log del scheduler —
    durante horario de mercado, con el cron corriendo cada `poll_interval_minutes`, esto es
    evidencia fuerte de que el proceso está "colgado mudo": vivo para macOS (no lo mata
    `launchd`/`KeepAlive`, que solo reacciona a un proceso MUERTO), pero sin procesar nada.
    Incidente real recurrente 2026-07-27/28/29 — la causa confirmada las 3 veces fue la Mac
    entrando en sueño profundo y rompiendo la conexión TCP a Schwab a mitad de una llamada, sin
    matar el proceso."""
    threshold = max(MIN_STALE_MINUTES, poll_interval_minutes * STALE_MULTIPLIER)
    return (now - last_log_mtime) > timedelta(minutes=threshold)


def catchup_lookback_minutes(gap_minutes: float) -> int:
    """Ventana de catch-up proporcional a cuánto tiempo estuvo colgado (con margen de +5 min y
    piso de 15, el lookback normal de `real_trades.REAL_TRADE_LOOKBACK_MINUTES`), sin superar
    `MAX_CATCHUP_MINUTES`."""
    return int(min(max(gap_minutes + 5, 15), MAX_CATCHUP_MINUTES))


def run_healthcheck(
    settings: Settings,
    broker: BrokerClient,
    conn: sqlite3.Connection,
    now: datetime,
    log_path: Path,
    anthropic_api_key: str | None,
    finnhub_api_key: str | None,
    get_scheduler_pid: Callable[[], int | None],
    restart_scheduler: Callable[[], None],
    notify: Callable[[str], None],
) -> list[dict]:
    """Orquesta la detección + auto-reparación. Recibe `get_scheduler_pid`/`restart_scheduler`/
    `notify` como funciones inyectadas (en vez de llamar a `subprocess`/`osascript` directo acá)
    para poder probar toda la lógica de decisión con dobles de prueba, sin tocar procesos ni
    mandar notificaciones reales desde los tests — mismo criterio de inyección de dependencias
    que ya usa el resto del motor (`broker: BrokerClient` en `alerts/engine.py`, etc.).

    Solo actúa durante horario de mercado regular ("abierto", ver `market_calendar.py`): fuera
    de esa ventana el cron real no debería estar escribiendo nada al log de todas formas, así
    que un log "viejo" no es evidencia de nada. Devuelve la lista de operaciones reales
    encontradas en el catch-up (vacía si no hizo falta reparar nada)."""
    if market_session(now) != "abierto":
        return []

    pid = get_scheduler_pid()
    if pid is None:
        # launchd con KeepAlive ya debería haberlo revivido solo (proceso MUERTO, no colgado) —
        # esto es un respaldo por si algo le impide hacerlo. Sin log de referencia no hay forma
        # de saber cuánto estuvo caído, así que no se intenta un catch-up con ventana calculada
        # acá — el próximo ciclo normal del cron ya reiniciado cubre los últimos 15 min.
        notify("Scheduler de OptionsUp no está corriendo — reiniciando")
        restart_scheduler()
        return []

    if not log_path.exists():
        return []
    last_log_mtime = datetime.fromtimestamp(log_path.stat().st_mtime, tz=now.tzinfo)
    if not is_stale(now, last_log_mtime, settings.scheduler.real_trade_poll_interval_minutes):
        return []

    gap_minutes = (now - last_log_mtime).total_seconds() / 60
    notify(f"Scheduler colgado ({gap_minutes:.0f} min sin actividad) — reiniciando y corriendo catch-up de operaciones reales")
    restart_scheduler()

    share_positions = broker.get_all_share_positions()
    generated = detect_and_alert_real_trades(
        broker,
        conn,
        settings,
        date.today(),
        share_positions,
        anthropic_api_key,
        finnhub_api_key,
        lookback_minutes=catchup_lookback_minutes(gap_minutes),
    )
    if generated:
        symbols = ", ".join(g["symbol"] for g in generated)
        notify(f"Catch-up encontró {len(generated)} operación(es) real(es) que el scheduler colgado se había perdido: {symbols}")
    return generated
