"""Backtest de los 0DTE (Iron Condor / Iron Butterfly) y su inclusión en la revisión semanal.

Contexto (usuario 2026-08-23: "si quiero los iron, ¿cómo hacemos?"): el motor de backtest ya tenía
condor y butterfly, pero el trabajo semanal automático corría SOLO naked puts. Los 0DTE quedaban
disponibles nada más que a mano desde el dashboard, así que en la práctica nunca se medían.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from options_advisor.backtest import engine
from options_advisor.broker.models import PriceBar


def _ruedas(n: int = 400, semilla: int = 3) -> list[PriceBar]:
    """Histórico diario sintético y reproducible."""
    rnd = random.Random(semilla)
    barras, precio, d = [], 6000.0, date(2024, 1, 1)
    while len(barras) < n:
        if d.weekday() < 5:
            apertura = precio
            precio = max(1.0, precio * (1 + rnd.gauss(0.0004, 0.011)))
            barras.append(PriceBar(symbol="$SPX", trade_date=d, open=apertura,
                                   high=max(apertura, precio) * 1.004,
                                   low=min(apertura, precio) * 0.996,
                                   close=precio, volume=1_000_000))
        d += timedelta(days=1)
    return barras


# --- ganancia por MONTO fijo (la regla real del butterfly) --------------------

def test_el_butterfly_cierra_por_monto_fijo_no_por_porcentaje():
    """settings.yaml: `intraday_butterfly.profit_target: 50.0` — cierra a +$50 fijo, decisión del
    usuario del 10/08 ("scalp rápido"). Antes el backtest usaba un % del crédito estimado, así que
    medía una estrategia que no era la que el robot opera."""
    params = engine.IronParams(profit_dollars=50.0, stop_loss_dollars=70.0)
    operaciones = engine.backtest_iron_butterfly_daily(_ruedas(), "$SPX", params)
    assert operaciones
    assert {t.pnl for t in operaciones if t.won} == {50.0}
    assert {t.pnl for t in operaciones if not t.won} == {-70.0}


def test_sin_monto_fijo_el_condor_sigue_usando_el_porcentaje():
    """El condor SÍ cierra por porcentaje del crédito. El campo nuevo no puede cambiarle nada."""
    params = engine.IronParams(profit_target_pct=0.35, stop_loss_dollars=100.0)
    assert params.profit_dollars is None
    operaciones = engine.backtest_iron_condor_daily(_ruedas(), "$SPX", params)
    ganadoras = [t.pnl for t in operaciones if t.won]
    assert ganadoras and len(set(ganadoras)) > 1, "Con % del crédito, las ganancias deben variar"


def test_el_stop_se_respeta_en_las_dos_estrategias():
    for correr, params in (
        (engine.backtest_iron_condor_daily, engine.IronParams(stop_loss_dollars=100.0)),
        (engine.backtest_iron_butterfly_daily, engine.IronParams(profit_dollars=50.0, stop_loss_dollars=70.0)),
    ):
        perdedoras = [t.pnl for t in correr(_ruedas(), "$SPX", params) if not t.won]
        assert perdedoras
        assert set(perdedoras) == {-params.stop_loss_dollars}


# --- el punto de equilibrio, que es lo que el backtest existe para revelar ----

def test_el_resumen_permite_ver_si_la_estrategia_pierde_plata():
    """Ganando $50 y perdiendo $70 hace falta acertar >58.3% solo para empatar. El resumen tiene que
    dejar ver eso: acierto, ganancia media y pérdida media."""
    params = engine.IronParams(profit_dollars=50.0, stop_loss_dollars=70.0)
    k = engine.summarize(engine.backtest_iron_butterfly_daily(_ruedas(), "$SPX", params))
    assert k["avg_win"] == 50.0
    assert k["avg_loss"] == -70.0
    equilibrio = 70.0 / (50.0 + 70.0) * 100
    esperado = k["win_rate"] > equilibrio
    assert (k["total_pnl"] > 0) == esperado, "El P&L no concuerda con el punto de equilibrio"


# --- que el trabajo semanal realmente los corra -------------------------------

def test_la_revision_semanal_incluye_los_iron():
    """Guardia de fuente: si alguien vuelve a dejar el semanal corriendo solo naked puts, los 0DTE
    dejan de medirse y nadie se entera — que es como estuvo hasta el 23/08."""
    from options_advisor.config import PROJECT_ROOT
    fuente = (PROJECT_ROOT / "src" / "options_advisor" / "scheduler" / "jobs.py").read_text()
    assert "_backtest_semanal_de_los_iron" in fuente
    assert "backtest_iron_condor_daily" in fuente
    assert "backtest_iron_butterfly_daily" in fuente


def test_los_iron_no_pueden_tumbar_el_backtest_de_naked():
    """Van en su propio try: si falla el histórico de $SPX, el sweep de naked puts —que es el que
    venía funcionando— tiene que seguir dando su informe igual."""
    from options_advisor.config import PROJECT_ROOT
    import re
    fuente = (PROJECT_ROOT / "src" / "options_advisor" / "scheduler" / "jobs.py").read_text()
    cuerpo = re.search(r"def _backtest_semanal_de_los_iron.*?(?=\ndef )", fuente, re.S).group(0)
    assert cuerpo.count("except Exception") >= 2
    assert "return {}" in cuerpo or "return salida" in cuerpo


# --- el dashboard tiene que usar las reglas de CADA estrategia ---------------

def _fuente_del_dashboard() -> str:
    from options_advisor.config import PROJECT_ROOT
    return (PROJECT_ROOT / "src" / "options_advisor" / "dashboard" / "pages" / "13_backtesting.py").read_text()


def test_el_dashboard_no_mezcla_los_parametros_de_las_dos_estrategias():
    """Hasta el 2026-08-23 había UN solo `ipar`, armado con los valores del CONDOR, y se le pasaba
    también al butterfly. O sea que el backtest del butterfly medía 35% del crédito y stop de $100,
    cuando su regla real es +$50 fijos con stop de -$70. Describía una estrategia inexistente."""
    fuente = _fuente_del_dashboard()
    assert "backtest_iron_condor_daily(bars, s, ipar_condor)" in fuente
    assert "backtest_iron_butterfly_daily(bars, s, ipar_fly)" in fuente
    assert "backtest_iron_butterfly_daily(bars, s, ipar)" not in fuente, "Volvió a compartir parámetros"


def test_el_dashboard_usa_los_valores_aprendidos_no_los_de_config():
    """Si el aprendizaje ya movió el objetivo del condor, el backtest tiene que medir contra ESE
    valor: comparar el histórico con reglas que el robot ya no usa no sirve para decidir nada."""
    fuente = _fuente_del_dashboard()
    assert "learning.effective_condor(conn, settings.intraday_condor)" in fuente
    assert "learning.effective_butterfly(conn, settings.intraday_butterfly)" in fuente


def test_el_dashboard_aclara_que_delta_y_dte_son_de_los_naked():
    """El usuario lo señaló mirando la pantalla: los controles de delta objetivo y DTE están a la
    vista aunque solo estén tildados los iron, y no afectan a los 0DTE en absoluto."""
    fuente = _fuente_del_dashboard()
    assert "son de los **naked puts**" in fuente
    assert "no con el delta/DTE de arriba" in fuente


# --- el backtest tiene que correr con las reglas REALES ----------------------
# (usuario 2026-08-23: "quiero que uses lo que ya estás usando en simulador y en real")

# `iv_proxy` necesita una ventana de 20 ruedas para estimar la volatilidad; sin eso devuelve None y
# el backtest saltea el día. Por eso las series de prueba llevan un tramo de arranque.
_ARRANQUE = 25


def _barras_con_rango(rangos: list[float], *, semilla: int = 5) -> list[PriceBar]:
    """Ruedas cuyo rango intradía es EXACTAMENTE `2 * amp`, para probar el filtro de día calmo.

    El cierre se mueve apenas respecto de la apertura a propósito: si el precio saltara de verdad,
    ese salto entraría en el rango (high-low) y una rueda pedida como "calma" terminaría con 2% de
    rango — que es justo el error que tenía la primera versión de este fixture.

    Antepone `_ARRANQUE` ruedas con movimiento real, porque `iv_proxy` necesita 20 ruedas de
    variación para estimar la volatilidad; sin eso devuelve None y el backtest saltea el día."""
    rnd = random.Random(semilla)
    barras, d = [], date(2024, 1, 1)
    precio = 6000.0
    for i, amp in enumerate([0.02] * _ARRANQUE + list(rangos)):
        apertura = precio
        if i < _ARRANQUE:
            precio = max(1.0, apertura * (1 + rnd.gauss(0.0, 0.010)))   # da HV al arranque
            alto, bajo = max(apertura, precio) * (1 + amp), min(apertura, precio) * (1 - amp)
        else:
            precio = apertura * 1.0002                                   # cierre casi pegado
            alto, bajo = apertura * (1 + amp), apertura * (1 - amp)      # rango = 2*amp exacto
        barras.append(PriceBar(symbol="$SPX", trade_date=d, open=apertura, high=alto, low=bajo,
                               close=precio, volume=1_000_000))
        d += timedelta(days=1)
    return barras


def test_la_distancia_de_los_cortos_sale_del_delta_no_de_un_numero_clavado():
    """Antes había un `1.04 * sigma` escrito a mano. Era exactamente delta 0.15, pero quedaba fijo:
    cambiar la configuración no movía el backtest."""
    assert round(engine.sigmas_para_delta(0.15), 2) == 1.04
    assert engine.sigmas_para_delta(0.05) > engine.sigmas_para_delta(0.30), "Menos delta = más lejos"


def test_el_filtro_de_dia_calmo_mira_el_dia_ANTERIOR_no_el_de_hoy():
    """Es la trampa que hay que evitar: en vivo el robot decide con los primeros 30 minutos. Con
    datos diarios, usar el rango del día completo sería decidir con información del futuro."""
    # Día 0 violento, día 1 calmo, día 2 violento. Con el filtro, solo puede operarse el día 2
    # (porque el 1 -- el anterior -- fue calmo). Nunca el día 1, cuyo antecesor fue violento.
    # Tras el arranque (todo violento): [violento, CALMO, violento, violento]
    barras = _barras_con_rango([0.02, 0.0015, 0.02, 0.02])   # 2*0.0015 = 0.30% < 0.4% = calmo
    calmo, siguiente = barras[_ARRANQUE + 1], barras[_ARRANQUE + 2]
    params = engine.IronParams(filtrar_dias_calmos=True, calm_range_pct=0.004)
    fechas = {t.entry_date for t in engine.backtest_iron_condor_daily(barras, "$SPX", params)}

    assert siguiente.trade_date in fechas, "El día DESPUÉS del calmo tenía que poder operarse"
    assert calmo.trade_date not in fechas, ("Se operó el día calmo mirando SU PROPIO rango: "
                                            "eso es decidir con información del futuro")


def test_sin_el_filtro_opera_todos_los_dias():
    """El comportamiento viejo sigue disponible: el filtro nace apagado en IronParams."""
    barras = _barras_con_rango([0.02] * 30)
    assert engine.IronParams().filtrar_dias_calmos is False
    con = engine.backtest_iron_condor_daily(barras, "$SPX", engine.IronParams(filtrar_dias_calmos=True))
    sin = engine.backtest_iron_condor_daily(barras, "$SPX", engine.IronParams())
    assert len(sin) > len(con) == 0


def test_los_parametros_salen_de_la_configuracion_real():
    """UN solo lugar traduce config -> backtest. Antes el dashboard armaba los suyos y el job los
    suyos, y encima el dashboard le pasaba los del condor tambien al butterfly."""
    from options_advisor.config import load_settings
    s = load_settings()
    pc = engine.params_del_condor(s.intraday_condor)
    pf = engine.params_del_butterfly(s.intraday_butterfly)

    assert pc.profit_target_pct == s.intraday_condor.profit_target_pct
    assert pc.stop_loss_dollars == s.intraday_condor.stop_loss_dollars
    assert pc.short_delta == s.intraday_condor.short_delta_max
    assert pc.calm_range_pct == s.intraday_condor.calm_range_pct
    assert pc.filtrar_dias_calmos is True, "El condor en vivo solo entra en días calmos"

    assert pf.profit_dollars == s.intraday_butterfly.profit_target, "El butterfly cierra por monto fijo"
    assert pf.stop_loss_dollars == s.intraday_butterfly.stop_loss
    assert pf.filtrar_dias_calmos is False, "El butterfly no tiene gatillo de día calmo"


def test_el_condor_y_el_butterfly_no_comparten_parametros():
    """Cada uno tiene sus reglas y sus nombres de campo distintos en settings.yaml."""
    from options_advisor.config import load_settings
    s = load_settings()
    assert engine.params_del_condor(s.intraday_condor) != engine.params_del_butterfly(s.intraday_butterfly)


def test_el_credito_estimado_baja_al_alejar_los_cortos():
    """Cobrar lo mismo vendiendo a delta 0.05 que a delta 0.30 seria absurdo."""
    barras = _barras_con_rango([0.01] * 80)
    creditos = {}
    for delta in (0.05, 0.30):
        ops = engine.backtest_iron_condor_daily(barras, "$SPX", engine.IronParams(short_delta=delta))
        creditos[delta] = max(t.entry_premium for t in ops)
    # Sin volatilidad suficiente los dos chocan contra el piso de $0.20 y la prueba no diría nada.
    assert creditos[0.30] > 0.20, "La serie de prueba no tiene volatilidad suficiente"
    assert creditos[0.05] < creditos[0.30]


# --- el informe semanal no puede esconder el riesgo -------------------------
# (encontrado el 2026-08-23, la primera vez que el backtest semanal corrió de verdad)

def _fuente_del_job() -> str:
    from options_advisor.config import PROJECT_ROOT
    return (PROJECT_ROOT / "src" / "options_advisor" / "scheduler" / "jobs.py").read_text()


def test_el_titular_de_naked_puts_muestra_el_drawdown():
    """El ranking ordena por P&L total, así que SIEMPRE corona al delta más arriesgado: vender más
    cerca del dinero cobra más prima y gana casi siempre… hasta la vez que no.

    La primera corrida real mostró 'delta 0.35, win 98.7%, P&L $389.121' y se calló una caída máxima
    de $879.416 — más del doble de la ganancia. Un informe así no informa: empuja a tomar más riesgo
    escondiendo justamente el riesgo."""
    fuente = _fuente_del_job()
    assert "max_drawdown" in fuente
    assert "caída máxima" in fuente
    assert "premia al delta MÁS arriesgado" in fuente


def test_el_informe_de_los_iron_declara_el_credito_que_asume():
    """El crédito estimado es el supuesto más frágil del backtest de los 0DTE y quedaba invisible.
    Con $SPX a 7.700 la fórmula da créditos de más de $1.000; los condors REALES del usuario
    cobraron entre $167 y $202. Un P&L construido sobre eso no sirve para decidir."""
    fuente = _fuente_del_job()
    assert "credito_medio_modelado" in fuente
    assert "crédito medio" in fuente


def test_el_backtest_semanal_no_cambia_parametros_solo():
    """Es un INFORME. Con un crédito 7 veces mayor que el real y un ranking que premia el riesgo,
    que además aplicara cambios por su cuenta sería peligroso."""
    import re
    fuente = _fuente_del_job()
    cuerpo = re.search(r"def job_backtest_review.*?(?=\ndef )", fuente, re.S).group(0)
    assert "insert_learning_report" in cuerpo
    for prohibido in ("set_learning_value", "apply_approved_proposal", "set_robot_flag"):
        assert prohibido not in cuerpo, f"El backtest semanal está aplicando cambios ({prohibido})"
