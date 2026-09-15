"""El bloque de copiar tiene que andar en http:// (usuario 2026-09-15: "no funciona el copy paste").

El botón nativo de `st.code` usa `navigator.clipboard`, que el navegador SOLO expone en contexto
seguro (https:// o localhost). El dashboard se sirve por http://<IP de Tailscale>:8501, así que ese
botón no puede funcionar y encima falla sin decir nada. Estas pruebas cuidan las tres cosas que
hacen que el reemplazo no vuelva a romperse en silencio.
"""
from pathlib import Path

from options_advisor.dashboard.components import html_para_copiar

FUENTE = Path(__file__).resolve().parents[2] / "src" / "options_advisor" / "dashboard" / "components.py"


def test_el_texto_aparece_en_el_bloque():
    html = html_para_copiar("HOOD put $95 · crédito $1.55")
    assert "HOOD put $95" in html
    assert "crédito $1.55" in html


def test_el_texto_va_escapado():
    """Una narración con < o & no puede romper el HTML ni inyectar nada."""
    html = html_para_copiar("PLTR <b>put</b> & CRWD")
    assert "<b>put</b>" not in html
    assert "&lt;b&gt;put&lt;/b&gt;" in html
    assert "&amp; CRWD" in html


def test_texto_vacio_no_deja_el_bloque_en_blanco():
    assert "Sin texto disponible." in html_para_copiar("")
    assert "Sin texto disponible." in html_para_copiar("   ")


def test_hay_camino_sin_contexto_seguro():
    """El corazón del arreglo: si `navigator.clipboard` no está, todavía queda execCommand, y si
    tampoco, el texto queda seleccionado. Sin esto volvemos al botón muerto de antes."""
    html = html_para_copiar("cualquier cosa")
    assert "execCommand('copy')" in html, "falta el fallback que anda sin https"
    assert ".select()" in html, "falta dejar el texto seleccionado como último recurso"
    assert "<textarea" in html, "el texto tiene que ser seleccionable a mano"


def test_no_depende_solo_de_la_api_moderna():
    html = html_para_copiar("cualquier cosa")
    i_api = html.index("navigator.clipboard")
    i_viejo = html.index("execCommand")
    assert i_viejo > i_api or "alaVieja" in html, "el fallback tiene que existir además de la API"


def test_el_alto_viaja_al_estilo():
    assert "height: 210px" in html_para_copiar("x", alto=210)


def test_las_tarjetas_ya_no_usan_st_code_para_compartir():
    """Inspección de fuente: si alguien vuelve a poner `st.code` en los bloques de compartir, el
    botón vuelve a estar muerto en el servidor y nadie se entera hasta que lo aprieta."""
    fuente = FUENTE.read_text(encoding="utf-8")
    assert "st.code(shorten_for_sharing(" not in fuente
    assert fuente.count("bloque_para_copiar(") >= 3   # la definición + los dos llamados
