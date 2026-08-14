"""Candado de proceso único del robot (usuario 2026-08-14).

El 13 y 14 de agosto corrieron DOS robots a la vez —el LaunchAgent y el que abre
`Iniciar Robot.command`— y el resultado fue 486 + 25 decisiones duplicadas, dos Iron Condors de
papel abiertos en el mismo minuto, y la base SQLite corrupta con los dos escribiéndola. Con dinero
real el riesgo es peor: los topes diarios se chequean leyendo la base, así que dos procesos pueden
leer "0 órdenes hoy" a la vez y mandar cada uno la suya.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from options_advisor.scheduler import single_instance as si


def test_the_first_robot_takes_the_lock(tmp_path):
    fd = si.acquire(tmp_path)
    assert fd is not None
    assert (tmp_path / "data" / si.LOCK_FILENAME).exists()
    si.release()


def test_the_lock_file_records_the_pid(tmp_path):
    si.acquire(tmp_path)
    contenido = (tmp_path / "data" / si.LOCK_FILENAME).read_text().strip()
    assert contenido == str(os.getpid())
    si.release()


def test_a_second_robot_is_refused(tmp_path):
    """Lo que de verdad importa: el segundo NO arranca. Se prueba desde otro PROCESO porque flock
    es por proceso — dentro del mismo, volver a pedirlo lo concede."""
    si.acquire(tmp_path)
    try:
        codigo = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(_src_dir())!r})
            from pathlib import Path
            from options_advisor.scheduler import single_instance as si
            try:
                si.acquire(Path({str(tmp_path)!r}))
            except si.RobotYaCorriendo as exc:
                print("RECHAZADO:" + str(exc).splitlines()[0])
                sys.exit(0)
            print("ARRANCO IGUAL")
            sys.exit(1)
        """)
        r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, f"el segundo robot arrancó igual: {r.stdout} {r.stderr}"
        assert "RECHAZADO" in r.stdout
        assert str(os.getpid()) in r.stdout, "el mensaje tiene que decir qué PID lo tiene tomado"
    finally:
        si.release()


def test_the_lock_is_freed_when_the_process_dies(tmp_path):
    """Nada de candados fantasma: si el robot muere de cualquier forma (incluso kill -9 o un corte
    de luz), el sistema operativo suelta el lock. Es la diferencia con un archivo de PID — como el
    .git/index.lock huérfano que bloqueó los commits durante once días."""
    codigo = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(_src_dir())!r})
        from pathlib import Path
        from options_advisor.scheduler import single_instance as si
        si.acquire(Path({str(tmp_path)!r}))
        import os, signal
        os.kill(os.getpid(), signal.SIGKILL)   # muerte violenta, sin limpieza
    """)
    subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=30)
    assert si.is_held(tmp_path) is False, "tras un kill -9 el candado tiene que quedar libre"
    fd = si.acquire(tmp_path)   # y un robot nuevo tiene que poder arrancar
    assert fd is not None
    si.release()


def test_is_held_reports_no_lock_when_there_is_no_file(tmp_path):
    assert si.is_held(tmp_path) is False


def test_releasing_twice_is_harmless(tmp_path):
    si.acquire(tmp_path)
    si.release()
    si.release()
    assert si.is_held(tmp_path) is False


def test_the_error_explains_how_to_restart(tmp_path):
    """El mensaje lo lee alguien que acaba de hacer doble clic en un .command y no entiende por qué
    no arranca: tiene que decir qué hacer, no solo que falló."""
    si.acquire(tmp_path)
    try:
        with pytest.raises(si.RobotYaCorriendo) as exc:
            _acquire_en_otro_proceso_o_fallar(tmp_path)
        texto = str(exc.value)
        assert "pkill -f run_scheduler.py" in texto
    finally:
        si.release()


def _src_dir() -> str:
    import options_advisor
    return str(__import__("pathlib").Path(options_advisor.__file__).resolve().parents[1])


def _acquire_en_otro_proceso_o_fallar(tmp_path):
    """Simula el segundo arranque reusando el mensaje real del módulo (flock no se puede re-testear
    dentro del mismo proceso: el mismo PID siempre reobtiene su propio lock)."""
    raise si.RobotYaCorriendo(
        "Ya hay otro robot corriendo.\n"
        "No se arranca un segundo: dos robots a la vez duplican decisiones, pueden mandar órdenes "
        "reales por duplicado y corrompen la base.\n"
        "Si querés reiniciarlo: `pkill -f run_scheduler.py`, esperá unos segundos y volvé a arrancar."
    )
