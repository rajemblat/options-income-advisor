"""El cartel del token de Schwab: el estado más grave es el que más se ve (usuario 2026-09-14).

El caso real: el token venció el viernes 11 a las 16:35. El vigilante lo detectó y trató de avisar
por mail cada hora, pero el mail nunca salió. El usuario abrió el correo el lunes con miles de mails
de "el robot se colgó y lo reinicié solo" —el síntoma— y cero del token —la causa—. El robot estuvo
tres días sin poder ver el mercado, y el dashboard, que él sí mira todos los días, no decía nada:
la función se hacía a un lado justamente cuando el token ya estaba vencido.

Lo que se afirma acá:
  · VENCIDO grita, en todas las páginas, sin depender del mail;
  · se avisa desde 72 h antes, y se pone urgente a las 24 h;
  · sin tokens guardados no se inventa nada;
  · los pasos son los de la máquina donde el robot corre de verdad.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from options_advisor.dashboard import components

HORA = 3600.0
DIA = 86400.0


class _Captura:
    """Anota qué carteles se pintaron, sin necesitar un runtime de Streamlit."""

    def __init__(self):
        self.errores: list[str] = []
        self.warnings: list[str] = []
        self.infos: list[str] = []
        self.markdown: list[str] = []


@pytest.fixture()
def pantalla(monkeypatch):
    cap = _Captura()
    monkeypatch.setattr(components.st, "error", lambda t, **k: cap.errores.append(t))
    monkeypatch.setattr(components.st, "warning", lambda t, **k: cap.warnings.append(t))
    monkeypatch.setattr(components.st, "info", lambda t, **k: cap.infos.append(t))
    monkeypatch.setattr(components.st, "markdown", lambda t, **k: cap.markdown.append(t))

    class _Exp:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(components.st, "expander", lambda *a, **k: _Exp())
    return cap


def _con_segundos(segundos):
    return patch.object(components, "read_refresh_token_seconds_left", return_value=segundos)


# ─────────────────── vencido: lo que fallaba ───────────────────

def test_vencido_grita_en_vez_de_callarse(pantalla):
    """ESTE es el bug del 11/09: antes hacía return y no se veía nada."""
    with _con_segundos(-2 * DIA):
        components.render_schwab_token_warning()
    assert pantalla.errores, "un token vencido tiene que pintar un error, no quedarse callado"
    assert "VENCIDA" in pantalla.errores[0]
    assert not pantalla.warnings and not pantalla.infos


def test_vencido_dice_hace_cuanto_y_que_no_esta_operando(pantalla):
    with _con_segundos(-(3 * DIA + 5 * HORA)):
        components.render_schwab_token_warning()
    texto = pantalla.errores[0]
    assert "3 día(s)" in texto
    assert "NO está operando" in texto
    assert "stop" in texto            # lo que más duele: tampoco puede ejecutar el stop


def test_vencido_muestra_los_pasos_sin_esconderlos_en_un_expander(pantalla):
    with _con_segundos(-HORA):
        components.render_schwab_token_warning()
    assert pantalla.markdown, "los pasos para reconectar tienen que estar a la vista"


# ─────────────────── antes de vencer ───────────────────

def test_a_las_70_horas_avisa_suave(pantalla):
    with _con_segundos(70 * HORA):
        components.render_schwab_token_warning()
    assert pantalla.infos and not pantalla.warnings and not pantalla.errores


def test_a_las_20_horas_el_aviso_se_pone_urgente(pantalla):
    with _con_segundos(20 * HORA):
        components.render_schwab_token_warning()
    assert pantalla.warnings and not pantalla.infos
    assert "20 horas" in pantalla.warnings[0]


def test_con_una_semana_por_delante_no_molesta(pantalla):
    with _con_segundos(7 * DIA):
        components.render_schwab_token_warning()
    assert not (pantalla.errores or pantalla.warnings or pantalla.infos)


def test_el_aviso_temprano_agarra_el_vencimiento_de_un_viernes():
    """La razón de subir de 24 h a 72 h: con ~7 días de token, un vencimiento de viernes a la tarde
    daba un solo día hábil de margen. Tres días siempre agarran uno."""
    assert components.TOKEN_AVISO_TEMPRANO_HORAS >= 72
    assert components.TOKEN_WARN_HOURS == 24


# ─────────────────── casos borde ───────────────────

def test_sin_tokens_guardados_no_inventa_nada(pantalla):
    with _con_segundos(None):
        components.render_schwab_token_warning()
    assert not (pantalla.errores or pantalla.warnings or pantalla.infos)


# ─────────────────── los pasos, por máquina ───────────────────

def test_en_linux_los_pasos_son_los_del_servidor(monkeypatch):
    monkeypatch.setattr(components.platform, "system", lambda: "Linux")
    pasos = components._pasos_para_reconectar()
    assert "systemctl --user restart lokshn-robot" in pasos
    assert "ssh -t lokshn@" in pasos          # el -t hace falta para poder pegar la URL
    assert "launchctl" not in pasos


def test_en_la_mac_los_pasos_son_los_de_launchd(monkeypatch):
    monkeypatch.setattr(components.platform, "system", lambda: "Darwin")
    pasos = components._pasos_para_reconectar()
    assert "launchctl kickstart" in pasos
    assert "systemctl" not in pasos


def test_los_pasos_siempre_incluyen_reiniciar_el_robot(monkeypatch):
    """El paso que se olvida y deja al robot con el token viejo en memoria (pasó el 18/08)."""
    for sistema in ("Linux", "Darwin"):
        monkeypatch.setattr(components.platform, "system", lambda s=sistema: s)
        assert "cachea el token en memoria" in components._pasos_para_reconectar()
