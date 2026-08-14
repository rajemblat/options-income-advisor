"""Datos del Iron Condor para puntuarlo (usuario 2026-08-14: "para los iron necesito más datos para
puntuar, los mismos que los put, y a qué cobertura está el call vendido y el put vendido, y a cuántos
puntos de diferencia entre call vendido y comprado y put vendido y comprado").

Antes el expander solo decía "SP 7775 / SC 7835 · crédito $147.50 · cerrado" y no había forma de
juzgar la operación: no se veía cuánto podía moverse el SPX antes de tocar cada pata ni cuánto riesgo
tenía cada ala.
"""

from __future__ import annotations

from options_advisor.dashboard.rating import condor_data_rows


class _Fila(dict):
    """Se comporta como una fila de sqlite3 (acceso por clave, KeyError si no está)."""
    def __getitem__(self, k):
        if k not in self:
            raise KeyError(k)
        return dict.__getitem__(self, k)


def _condor(**over):
    base = dict(entry_spot=7800.0, short_put_strike=7770.0, short_call_strike=7835.0,
                long_put_strike=7760.0, long_call_strike=7845.0, entry_net_credit=165.0,
                max_loss=835.0, lower_breakeven=7768.35, upper_breakeven=7836.65,
                status="closed", close_reason="profit_target", realized_pnl=33.5,
                entry_ts="2026-08-14T09:35:02")
    base.update(over)
    return _Fila(base)


def _valor(filas, clave):
    return next(v for k, _, v in filas if k == clave)


def test_shows_how_far_each_sold_leg_is_from_the_price():
    """La cobertura de cada lado: cuánto puede caer y cuánto puede subir el SPX antes de tocar."""
    filas = condor_data_rows({}, _condor())
    # (7800 - 7770) / 7800 = 0.3846%
    assert _valor(filas, "cobertura_put") == "0.38%"
    # (7835 - 7800) / 7800 = 0.4487%
    assert _valor(filas, "cobertura_call") == "0.45%"


def test_shows_the_wing_width_in_points_on_both_sides():
    """Los puntos entre la pata vendida y la comprada — lo que fija la pérdida máxima."""
    filas = condor_data_rows({}, _condor())
    assert _valor(filas, "ala_put") == "10 pts  (7,770 → 7,760)"
    assert _valor(filas, "ala_call") == "10 pts  (7,835 → 7,845)"


def test_asymmetric_wings_are_reported_as_they_are():
    """Si un lado tiene el ala más ancha, el riesgo NO es simétrico y hay que verlo."""
    filas = condor_data_rows({}, _condor(long_put_strike=7745.0))   # ala put de 25 pts
    assert "25 pts" in _valor(filas, "ala_put")
    assert "10 pts" in _valor(filas, "ala_call")


def test_shows_credit_risk_and_their_ratio():
    filas = condor_data_rows({}, _condor())
    assert _valor(filas, "credito") == "$165.00"
    assert _valor(filas, "riesgo") == "$835.00"
    assert _valor(filas, "credito_riesgo") == "19.8%"


def test_shows_the_profit_range_in_points_and_percent():
    filas = condor_data_rows({}, _condor())
    rango = _valor(filas, "rango")
    assert "7,768" in rango and "7,837" in rango
    assert "pts" in rango and "%" in rango


def test_pulls_deltas_and_vix_from_the_decision_context():
    ctx = {"short_put_delta": 0.145, "short_call_delta": 0.13, "vix_change_pct": -1.162,
           "day_range_pct": 0.0008}
    filas = condor_data_rows(ctx, _condor())
    assert _valor(filas, "delta_put") == "0.145"
    assert _valor(filas, "delta_call") == "0.130"
    assert _valor(filas, "vix") == "-1.16%"
    assert _valor(filas, "dia_rango") == "0.08%"


def test_shows_the_outcome_of_a_closed_condor():
    filas = condor_data_rows({}, _condor())
    assert _valor(filas, "resultado") == "profit_target · $33.50"


def test_an_open_condor_says_open():
    filas = condor_data_rows({}, _condor(status="open", close_reason=None, realized_pnl=None))
    assert _valor(filas, "resultado") == "abierto"


def test_missing_data_never_breaks_the_panel():
    """Las filas viejas no tienen delta ni VIX guardados: tienen que mostrar — y no romper."""
    filas = condor_data_rows({}, _Fila(dict(entry_spot=None, short_put_strike=None,
                                            short_call_strike=None, long_put_strike=None,
                                            long_call_strike=None, entry_net_credit=None,
                                            max_loss=None, lower_breakeven=None,
                                            upper_breakeven=None, status="open",
                                            close_reason=None, realized_pnl=None, entry_ts=None)))
    assert all(isinstance(v, str) for _, _, v in filas)
    assert _valor(filas, "cobertura_put") == "—"
    assert _valor(filas, "ala_call") == "—"


def test_every_row_has_a_key_so_it_can_be_voted():
    """Cada dato se vota por separado, igual que en los puts: hace falta una clave estable."""
    filas = condor_data_rows({}, _condor())
    claves = [k for k, _, _ in filas]
    assert len(claves) == len(set(claves))
    assert "cobertura_call" in claves and "ala_put" in claves
