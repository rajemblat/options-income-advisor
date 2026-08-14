"""Candado de PROCESO ÚNICO del robot (usuario 2026-08-14).

Por qué existe: el robot se puede arrancar de dos maneras — el LaunchAgent de macOS
(`com.robertoajemblat.options-income-advisor.scheduler`, que corre solo con la Mac) y el archivo
`Iniciar Robot.command`. Nada impedía que convivieran, y convivieron: el 13/08 quedaron 486
decisiones duplicadas, el 14/08 otras 25, dos Iron Condors de papel abiertos en el mismo minuto, y
finalmente la base SQLite se corrompió ("database disk image is malformed") con los dos procesos
escribiéndola a la vez. Hubo que reconstruirla entera.

Con dinero real el riesgo es peor que unos duplicados en un log: los topes ("1 condor por día",
"2 órdenes por día") se chequean LEYENDO la base, así que dos procesos pueden leer "0 mandadas" en
el mismo instante y mandar cada uno la suya.

Cómo funciona: un lock de archivo exclusivo y NO bloqueante (`flock`). El primer robot lo toma y lo
mantiene mientras vive; el segundo falla al instante y sale con un mensaje claro en vez de arrancar
en paralelo. Si el proceso muere de cualquier forma — incluso `kill -9` o un corte de luz — el
sistema operativo libera el lock solo, así que no quedan candados fantasma (que es justo el problema
que tienen los archivos con PID, y el mismo que dejó el `.git/index.lock` huérfano bloqueando los
commits once días).
"""

from __future__ import annotations

import atexit
import fcntl
import os
from pathlib import Path

LOCK_FILENAME = ".scheduler.lock"


class RobotYaCorriendo(RuntimeError):
    """Ya hay otro robot vivo con el candado tomado."""


def _lock_path(project_root: Path) -> Path:
    return Path(project_root) / "data" / LOCK_FILENAME


def acquire(project_root: Path) -> "int":
    """Toma el candado del robot. Devuelve el descriptor abierto (hay que MANTENERLO abierto: si se
    cierra, el lock se suelta). Lanza `RobotYaCorriendo` si ya hay otro proceso con el candado.

    El descriptor se guarda en una variable de módulo además de devolverse, para que nadie lo pierda
    por accidente al no asignar el resultado y el recolector de basura lo cierre."""
    ruta = _lock_path(project_root)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(ruta), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        otro = ""
        try:
            otro = os.read(fd, 64).decode("utf-8", "ignore").strip()
        except OSError:
            pass
        os.close(fd)
        raise RobotYaCorriendo(
            "Ya hay otro robot corriendo" + (f" (PID {otro})" if otro else "") + ".\n"
            "No se arranca un segundo: dos robots a la vez duplican decisiones, pueden mandar órdenes "
            "reales por duplicado y corrompen la base.\n"
            "Si querés reiniciarlo: `pkill -f run_scheduler.py`, esperá unos segundos y volvé a arrancar."
        ) from exc

    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    os.fsync(fd)
    _guardar(fd)
    atexit.register(release)
    return fd


_FD: int | None = None


def _guardar(fd: int) -> None:
    global _FD
    _FD = fd


def release() -> None:
    """Suelta el candado. No hace falta llamarlo en el camino normal (el SO lo libera al morir el
    proceso), pero deja el archivo limpio en una salida ordenada."""
    global _FD
    if _FD is None:
        return
    try:
        fcntl.flock(_FD, fcntl.LOCK_UN)
        os.close(_FD)
    except OSError:
        pass
    _FD = None


def is_held(project_root: Path) -> bool:
    """¿Hay un robot vivo con el candado? Solo para diagnóstico — no lo usa el arranque, que toma el
    candado directamente (preguntar y después tomar sería una carrera)."""
    ruta = _lock_path(project_root)
    if not ruta.exists():
        return False
    fd = os.open(str(ruta), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)
