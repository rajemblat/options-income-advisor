"""El healthcheck tiene que funcionar igual en la Mac y en el servidor.

Es el componente que REPARA al robot cuando se cuelga. Si al mudar de máquina se olvida uno de sus
dos ganchos —cómo reiniciar el proceso y cómo avisarle al usuario— el robot queda sin red de
seguridad justo cuando más hace falta, y en silencio.
"""

from __future__ import annotations

import re

from options_advisor.config import PROJECT_ROOT

FUENTE = (PROJECT_ROOT / "scripts" / "healthcheck_scheduler.py").read_text()


def _cuerpo(nombre: str) -> str:
    """El texto de una función del script."""
    return re.search(rf"def {nombre}\(.*?(?=\ndef )", FUENTE, re.S).group(0)


def _solo_codigo(texto: str) -> str:
    """El mismo texto sin docstrings ni comentarios.

    Hace falta porque estos tests buscan cadenas literales, y los comentarios de este archivo
    NOMBRAN las cosas que se están verificando ("va SIN sudo a propósito", "`osascript` no existe
    en Linux"). Sin limpiar, un test pasaba o fallaba por lo que dice un comentario en vez de por
    lo que hace el código."""
    sin_docstrings = re.sub(r'"""[\s\S]*?"""', "", texto)
    return "\n".join(l.split("#", 1)[0] for l in sin_docstrings.splitlines())


def test_sabe_reiniciar_en_las_dos_plataformas():
    cuerpo = _solo_codigo(_cuerpo("_restart_scheduler"))
    assert "launchctl" in cuerpo, "Perdió el reinicio en macOS"
    assert "systemctl" in cuerpo, "No sabe reiniciar en el servidor"
    assert 'platform.system() == "Darwin"' in cuerpo, "Elige a ciegas en vez de mirar el sistema"


def test_el_reinicio_de_linux_no_usa_sudo():
    """El robot corre como servicio de USUARIO. Con sudo el comando fallaría, y el healthcheck se
    quedaría sin poder repararlo."""
    cuerpo = _solo_codigo(_cuerpo("_restart_scheduler"))
    linea_systemd = [l for l in cuerpo.splitlines() if "systemctl" in l][0]
    assert "sudo" not in linea_systemd


def test_avisa_por_email_y_no_solo_por_pantalla():
    """En la Mac alcanzaba la notificación nativa porque el usuario estaba delante. En el servidor
    no hay pantalla y Telegram nunca se configuró: sin email, un robot colgado se reparaba en
    silencio y nadie se enteraba."""
    assert "send_email_robot_real" in _solo_codigo(_cuerpo("_notify"))


def test_la_notificacion_de_macos_no_corre_en_linux():
    """`osascript` no existe en Linux: sin el guardia, cada corrida del healthcheck escribiría un
    traceback completo en el log."""
    cuerpo = _solo_codigo(_cuerpo("_notify"))
    antes_de_osascript = cuerpo.split("osascript")[0]
    assert 'platform.system() == "Darwin"' in antes_de_osascript


# ---------------------------------------------------------------------------
# El healthcheck no se puede suicidar (incidente 2026-08-20, descubierto el 23).

def test_el_reinicio_nunca_lanza():
    """`_restart_scheduler` NO puede usar check=True ni propagar excepciones.

    El 20/08 el LaunchAgent del robot estaba descargado. El healthcheck lo detectó bien y quiso
    revivirlo, pero `subprocess.run(..., check=True)` convirtió el `exit 113` de launchctl en una
    excepción que mató el proceso entero. El healthcheck no volvió a correr durante TRES DÍAS, en
    silencio — o sea que el robot quedó sin red de seguridad justo por culpa de la red de seguridad.
    """
    cuerpo = _solo_codigo(_cuerpo("_restart_scheduler"))
    assert "check=True" not in cuerpo, (
        "Volvió el check=True: un fallo de launchctl/systemctl mata al healthcheck otra vez."
    )
    assert "returncode" in cuerpo, "No revisa el resultado del comando"


def test_un_reinicio_fallido_avisa_por_email():
    """'Tu robot está muerto y no lo puedo revivir' es el mensaje más importante que este sistema
    puede mandar. Si falla el reinicio y nadie se entera, no sirve de nada haberlo detectado."""
    cuerpo = _solo_codigo(_cuerpo("_restart_scheduler"))
    assert "send_email_robot_real" in cuerpo


def test_el_aviso_dice_como_arreglarlo_en_las_dos_plataformas():
    """El email tiene que traer el comando exacto, no un 'revisá el servicio'."""
    cuerpo = _cuerpo("_restart_scheduler")
    assert "launchctl bootstrap" in cuerpo, "Sin la instrucción de recarga para macOS"
    assert "systemctl --user enable" in cuerpo, "Sin la instrucción de habilitación para el servidor"
