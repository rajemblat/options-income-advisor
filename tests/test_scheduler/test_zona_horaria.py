"""El robot no puede arrancar con el reloj corrido.

Contexto (mudanza al servidor, 2026-08-23): 123 llamadas del código preguntan la hora sin zona
(`datetime.now()`, `date.today()`), así que siguen el reloj LOCAL de la máquina. Un servidor recién
creado viene en UTC: después de las 20:00 de Nueva York, `date.today()` ya devuelve el día
siguiente y el tope diario de órdenes se resetea en plena tarde. No hay excepción ni error en el
log — los números salen distintos y nadie se entera.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from options_advisor.scheduler import zona_horaria as zh


def _en_utc(mes: int = 1) -> datetime:
    """Un instante cualquiera con el reloj del sistema puesto en UTC."""
    return datetime(2026, mes, 15, 18, 0, tzinfo=timezone.utc)


def _en_nueva_york_invierno() -> datetime:
    """Enero: Nueva York está en UTC-5 (sin horario de verano)."""
    return datetime(2026, 1, 15, 13, 0, tzinfo=timezone(timedelta(hours=-5)))


def _en_nueva_york_verano() -> datetime:
    """Julio: Nueva York está en UTC-4 (con horario de verano)."""
    return datetime(2026, 7, 15, 14, 0, tzinfo=timezone(timedelta(hours=-4)))


def test_reconoce_el_reloj_correcto_en_invierno():
    assert zh.zona_del_sistema_coincide("America/New_York", _en_nueva_york_invierno()) is True


def test_reconoce_el_reloj_correcto_en_verano():
    """El horario de verano se resuelve solo: se comparan desfases, no nombres ni fechas fijas."""
    assert zh.zona_del_sistema_coincide("America/New_York", _en_nueva_york_verano()) is True


def test_un_servidor_en_utc_no_pasa():
    """Es exactamente el estado en que viene un servidor nuevo."""
    assert zh.zona_del_sistema_coincide("America/New_York", _en_utc()) is False


def test_el_desfase_de_verano_no_se_confunde_con_el_de_invierno():
    """Un reloj fijo en UTC-5 en pleno julio NO es Nueva York: allá son UTC-4. Es el caso de una
    zona puesta a mano como 'EST' en vez de 'America/New_York', que en verano queda una hora atrás."""
    julio_en_utc_menos_5 = datetime(2026, 7, 15, 13, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert zh.zona_del_sistema_coincide("America/New_York", julio_en_utc_menos_5) is False


def test_exigir_no_lanza_cuando_esta_bien():
    zh.exigir_zona_horaria("America/New_York", _en_nueva_york_invierno())  # no debe lanzar


def test_exigir_lanza_y_explica_como_arreglarlo():
    with pytest.raises(zh.ZonaHorariaIncorrecta) as err:
        zh.exigir_zona_horaria("America/New_York", _en_utc())
    mensaje = str(err.value)
    assert "America/New_York" in mensaje
    assert "tope diario" in mensaje          # dice QUÉ se rompe
    assert "timedatectl" in mensaje or "Fecha y hora" in mensaje   # dice CÓMO se arregla


def test_una_zona_inexistente_tambien_frena_el_arranque():
    """Mejor no arrancar que arrancar sin poder verificar nada."""
    with pytest.raises(zh.ZonaHorariaIncorrecta):
        zh.exigir_zona_horaria("Marte/Olympus_Mons", _en_utc())


def test_el_arranque_del_robot_llama_al_candado():
    """Guardia de fuente: el candado no sirve si nadie lo llama. Si mañana alguien reescribe
    run_scheduler.py y se lo lleva puesto, esto lo caza."""
    from options_advisor.config import PROJECT_ROOT
    fuente = (PROJECT_ROOT / "scripts" / "run_scheduler.py").read_text()
    assert "exigir_zona_horaria" in fuente
    assert "sys.exit(1)" in fuente
