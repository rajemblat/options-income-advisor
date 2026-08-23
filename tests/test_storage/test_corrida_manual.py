"""Pedidos de corrida manual: el dashboard PIDE, el robot EJECUTA (auditoria 2026-08-22).

Los botones "Correr robot ahora" y "Analisis completo" ejecutaban job_robot_scan /
job_poll_and_analyze dentro del proceso de Streamlit. Esos jobs llegan a reprice_resting_orders,
close_real_positions, process_approved_ai_orders y maybe_log_live_order: abren y cierran posiciones
con plata real. El lock que los serializa es de PROCESO, asi que no cruzaba al dashboard — un clic
con el robot andando podia mandar la misma orden dos veces.
"""
from datetime import datetime, timedelta

import pytest

from options_advisor.storage import db
from options_advisor.storage import repository as repo


@pytest.fixture
def conn():
    return db.connect(":memory:")


def test_sin_pedido_no_hay_corrida(conn):
    assert repo.tomar_corrida_pendiente(conn) is None


def test_el_pedido_se_toma_UNA_sola_vez(conn):
    """Si dos ticks leyeran el mismo pedido, se dispararian dos escaneos en paralelo — justo lo
    que este cambio viene a evitar."""
    repo.pedir_corrida_manual(conn, "rapida")
    assert repo.tomar_corrida_pendiente(conn) == "rapida"
    assert repo.tomar_corrida_pendiente(conn) is None


def test_distingue_rapida_de_completa(conn):
    repo.pedir_corrida_manual(conn, "completa")
    assert repo.tomar_corrida_pendiente(conn) == "completa"


def test_un_pedido_viejo_se_descarta(conn):
    """Si el robot estuvo apagado, al arrancar no debe disparar un escaneo por un boton que
    alguien apreto hace horas — sobre todo porque force=True saltea el chequeo de horario."""
    repo.set_robot_flag(conn, repo.PEDIDO_CORRIDA,
                        f"rapida:{(datetime.now() - timedelta(hours=3)).isoformat()}")
    assert repo.tomar_corrida_pendiente(conn) is None


def test_un_pedido_corrupto_no_rompe(conn):
    repo.set_robot_flag(conn, repo.PEDIDO_CORRIDA, "cualquier-cosa")
    assert repo.tomar_corrida_pendiente(conn) is None
    repo.set_robot_flag(conn, repo.PEDIDO_CORRIDA, "rapida:no-es-una-fecha")
    assert repo.tomar_corrida_pendiente(conn) is None


def test_el_dashboard_ya_no_ejecuta_los_jobs():
    """Guardia contra la regresion: si alguien vuelve a llamar a los jobs desde el dashboard,
    vuelve el riesgo de doble orden. Este test lo cachea en el codigo fuente."""
    from pathlib import Path

    raiz = Path(repo.__file__).resolve().parents[1] / "dashboard"
    ofensores = []
    for archivo in raiz.rglob("*.py"):
        texto = archivo.read_text()
        for job in ("job_robot_scan(", "job_poll_and_analyze("):
            for linea in texto.splitlines():
                if job in linea and not linea.strip().startswith("#"):
                    ofensores.append(f"{archivo.name}: {linea.strip()[:70]}")
    assert not ofensores, (
        "El dashboard volvio a ejecutar jobs que mandan ordenes reales. Tiene que PEDIR la "
        "corrida con repo.pedir_corrida_manual() y dejar que el robot la ejecute:\n  "
        + "\n  ".join(ofensores)
    )
