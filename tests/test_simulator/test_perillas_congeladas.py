"""Perillas que el APRENDIZAJE no puede tocar (usuario 2026-09-09: "que quede fijo en mi número").

El caso real: el usuario tenía `calm_range_pct` en 0.40% en su settings.yaml, el aprendizaje lo fue
bajando solo hasta 0.20%, y con ese número el condor quedó prácticamente ciego — sobre 34 días de
barras de un minuto del SPX, en 21 no hubo ni un minuto habilitado en toda la ventana de entrada.
Nada lo avisaba: el archivo decía 0.40% y el robot aplicaba 0.20%.

Lo que se protege acá:
  · lo congelado se lee SIEMPRE del config, aunque haya un valor aprendido guardado en la base;
  · una perilla congelada no genera propuesta pendiente (un botón que no haría nada es peor que nada);
  · congelar UNA no congela las demás — el aprendizaje sigue funcionando para el resto;
  · sin `learning_frozen`, todo se comporta exactamente como antes.
"""

from __future__ import annotations

import pytest

from options_advisor.config import IntradayCondorSettings
from options_advisor.simulator import learning
from options_advisor.storage import db
from options_advisor.storage import repository as repo


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _cfg(**kw) -> IntradayCondorSettings:
    base = dict(calm_range_pct=0.006, min_credit=150.0, live_min_credit=150.0,
                short_delta_max=0.10, profit_target_pct=0.50, stop_loss_dollars=100.0)
    base.update(kw)
    return IntradayCondorSettings(**base)


# ─────────────────── aplicar lo aprendido ───────────────────

def test_lo_congelado_ignora_lo_aprendido_y_manda_el_config(conn):
    repo.set_learning_value(conn, "condor.calm_range_pct", 0.002)   # lo que el robot había aprendido
    cfg = _cfg(calm_range_pct=0.006, learning_frozen=["calm_range_pct"])
    assert learning.effective_condor(conn, cfg).calm_range_pct == pytest.approx(0.006)


def test_sin_congelar_sigue_ganando_lo_aprendido(conn):
    """El comportamiento de siempre queda intacto cuando la lista está vacía."""
    repo.set_learning_value(conn, "condor.calm_range_pct", 0.002)
    cfg = _cfg(calm_range_pct=0.006)
    assert learning.effective_condor(conn, cfg).calm_range_pct == pytest.approx(0.002)


def test_congelar_una_no_congela_las_otras(conn):
    repo.set_learning_value(conn, "condor.calm_range_pct", 0.002)
    repo.set_learning_value(conn, "condor.short_delta_max", 0.07)
    cfg = _cfg(calm_range_pct=0.006, short_delta_max=0.10, learning_frozen=["calm_range_pct"])
    eff = learning.effective_condor(conn, cfg)
    assert eff.calm_range_pct == pytest.approx(0.006)    # congelada: manda el config
    assert eff.short_delta_max == pytest.approx(0.07)    # libre: manda lo aprendido


def test_el_piso_de_credito_del_usuario_sigue_ganando(conn):
    """La regla del 2026-09-08 no se rompió al agregar el congelado."""
    repo.set_learning_value(conn, "condor.min_credit", 25.0)
    cfg = _cfg(min_credit=150.0, live_min_credit=150.0, learning_frozen=["calm_range_pct"])
    assert learning.effective_condor(conn, cfg).min_credit == pytest.approx(150.0)


# ─────────────────── escribir lo aprendido ───────────────────

def test_una_perilla_congelada_no_se_mueve_ni_se_propone(conn):
    congeladas = learning.perillas_congeladas(_cfg(learning_frozen=["calm_range_pct"]))
    aplicados, propuestos = learning._cd_move(
        conn, "condor.calm_range_pct", 0.006, 0.002, "bajar el tope ({current} → {proposed})",
        congeladas=congeladas,
    )
    assert (aplicados, propuestos) == ([], [])
    assert repo.get_learning_state(conn).get("condor.calm_range_pct") is None
    assert not repo.has_pending_proposal_for(conn, "calm_range_pct")


def test_la_misma_perilla_sin_congelar_si_se_mueve(conn):
    """Contraprueba: el test de arriba no pasa por un motivo ajeno al congelado."""
    aplicados, propuestos = learning._cd_move(
        conn, "condor.calm_range_pct", 0.006, 0.002, "bajar el tope ({current} → {proposed})",
    )
    assert aplicados or propuestos
    assert (repo.get_learning_state(conn).get("condor.calm_range_pct") is not None
            or repo.has_pending_proposal_for(conn, "calm_range_pct"))


def test_perillas_congeladas_tolera_que_no_este_configurado():
    assert learning.perillas_congeladas(_cfg()) == frozenset()
    assert learning.perillas_congeladas(_cfg(learning_frozen=["calm_range_pct", "min_credit"])) == \
        frozenset({"calm_range_pct", "min_credit"})


# ─────────────────── el número que eligió el usuario ───────────────────

def test_el_settings_real_trae_el_tope_en_060_y_congelado():
    """Guarda la decisión del 2026-09-09 contra un cambio accidental: 0.60% y sin que el robot lo mueva."""
    from options_advisor.config import load_settings
    cond = load_settings().intraday_condor
    assert cond.calm_range_pct == pytest.approx(0.006)
    assert "calm_range_pct" in cond.learning_frozen
