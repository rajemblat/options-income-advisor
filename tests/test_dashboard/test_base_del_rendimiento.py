"""El % de los naked se mide sobre la exposición PROMEDIO (usuario 2026-09-15).

"Debería ser sobre la exposición, promedio; no la máxima sino la promedio."

La página de Real Market es un script de Streamlit: no se puede importar y llamar una función, así
que estas pruebas inspeccionan la fuente. Es a propósito — lo que hay que impedir es que alguien
vuelva a dividir por los $50.000 de capital sin darse cuenta, y eso se ve en el código.
"""
from pathlib import Path

PAGINA = (Path(__file__).resolve().parents[2] / "src" / "options_advisor" / "dashboard"
          / "pages" / "14_real_market.py")
FUENTE = PAGINA.read_text(encoding="utf-8")


def test_la_base_sale_del_promedio_no_del_maximo():
    assert 'BASE_RENDIMIENTO = _exp["promedio"]' in FUENTE
    assert 'BASE_RENDIMIENTO = _exp["maximo"]' not in FUENTE


def test_los_dos_carteles_de_naked_dividen_por_la_base():
    assert "_nak_pnl_per / BASE_RENDIMIENTO" in FUENTE
    assert "_realized_all / BASE_RENDIMIENTO" in FUENTE
    assert "_nak_pnl_per / CAPITAL_DISPONIBLE" not in FUENTE
    assert "_realized_all / CAPITAL_DISPONIBLE" not in FUENTE


def test_el_cartel_dice_contra_que_se_mide():
    """Un porcentaje sin denominador a la vista es peor que ningún porcentaje."""
    assert "BASE_RENDIMIENTO_TXT" in FUENTE
    assert "la exposición promedio de $" in FUENTE


def test_no_se_divide_por_cero_si_nunca_hubo_exposicion():
    assert "if not BASE_RENDIMIENTO_ES_EXPOSICION:" in FUENTE
    assert "BASE_RENDIMIENTO = float(CAPITAL_DISPONIBLE)" in FUENTE


def test_la_referencia_vieja_sigue_visible():
    """El número sobre los $50.000 no se borra: se baja a la letra chica."""
    assert "def _sobre_capital(" in FUENTE
    assert "de capital disponible sería" in FUENTE


def test_el_condor_no_se_toco():
    """El pedido fue sobre los naked. El condor sigue midiéndose sobre los $50.000."""
    assert "_cond_pl_total / CAPITAL_DISPONIBLE" in FUENTE
