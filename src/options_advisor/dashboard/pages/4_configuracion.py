from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from options_advisor.dashboard.components import ACCENT, CRITICAL, GOOD, get_connection, get_settings, get_symbols, icon, inject_theme, render_header, render_notification_bell
from options_advisor.dashboard.compound_interest import project_compound_growth
from options_advisor.dashboard.inflation_simulator import project_inflation_scenarios
from options_advisor.storage import repository as repo
from options_advisor.storage.models import InvestorProfile

st.set_page_config(page_title="Perfil y Simulación", page_icon="⚙️", layout="wide")
inject_theme()
render_header(icon("settings", size=24, color=ACCENT), "Perfil y Simulación")

conn = get_connection()
render_notification_bell(conn)
settings = get_settings()

st.subheader("Perfil de inversor")
current = repo.get_investor_profile(conn)
defaults = settings.investor_profile

with st.form("investor_profile_form"):
    capital = st.number_input(
        "Capital disponible", min_value=0.0, value=current.capital_available if current else defaults.capital_available
    )
    loss_tolerance = st.number_input(
        "Tolerancia a pérdida máxima por operación (%)",
        min_value=0.0,
        max_value=100.0,
        value=current.loss_tolerance_pct if current else defaults.loss_tolerance_pct,
    )
    experience = st.selectbox(
        "Experiencia declarada",
        ["principiante", "intermedio", "avanzado"],
        index=["principiante", "intermedio", "avanzado"].index(current.experience_level if current else defaults.experience_level),
    )
    risk_preference = st.selectbox(
        "Preferencia de riesgo",
        ["defined", "undefined"],
        index=["defined", "undefined"].index(current.risk_preference if current else defaults.risk_preference),
    )
    risk_level_labels = {"conservador": "Conservador", "moderado": "Normal", "agresivo": "Agresivo"}
    risk_level_keys = ["conservador", "moderado", "agresivo"]
    risk_level = st.selectbox(
        "Perfil de riesgo por default (ya NO decide qué alertas se generan, ver nota abajo)",
        risk_level_keys,
        format_func=lambda k: risk_level_labels[k],
        index=risk_level_keys.index(current.risk_level if current else defaults.risk_level),
    )
    threshold_override = st.number_input(
        "Umbral de convicción manual — solo aplica a este perfil por default (vacío = usar el default del perfil)",
        min_value=0,
        max_value=100,
        value=current.conviction_threshold_override if current and current.conviction_threshold_override else 0,
    )
    submitted = st.form_submit_button("Guardar")

    if submitted:
        repo.upsert_investor_profile(
            conn,
            InvestorProfile(
                capital_available=capital,
                loss_tolerance_pct=loss_tolerance,
                experience_level=experience,
                risk_preference=risk_preference,
                risk_level=risk_level,
                conviction_threshold_override=threshold_override or None,
                updated_at=datetime.now(),
            ),
        )
        st.success("Perfil guardado.")
        st.rerun()

st.info(
    "Desde el 2026-07-24, cada corrida de análisis (\"Correr análisis ahora\", el scheduler "
    "automático, y el escaneo) evalúa **los 3 perfiles a la vez** — Conservador/Normal/Agresivo "
    "ya no compiten por un único \"perfil activo\", los 3 generan sus propias alertas siempre. "
    "El perfil que elijas acá arriba **no decide qué se genera**; para ver solo las alertas de "
    "un perfil puntual, usá el filtro de perfil en la página **Alertas** (ahí sí filtra, y se "
    "puede combinar con el filtro de estrategia). Este selector queda como valor por default "
    "para el único caso donde todavía importa un perfil único (tests y llamadas directas al "
    "motor sin especificar perfil, no la operación normal del dashboard).",
    icon="ℹ️",
)

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
st.subheader("Qué cambia cada perfil (config/settings.yaml)")
st.caption(
    "No es solo un filtro visual: el delta objetivo y el IV Rank mínimo cambian qué strikes "
    "elige el motor al armar cada candidato, antes de llegar a puntuarlo. Esto aplica a los 3 "
    "perfiles en cada corrida, no solo al que esté seleccionado arriba."
)
st.table(
    {
        "Perfil": ["Conservador", "Normal", "Agresivo"],
        "Delta objetivo (más bajo = más OTM)": [
            settings.strategy.target_short_delta.conservador,
            settings.strategy.target_short_delta.moderado,
            settings.strategy.target_short_delta.agresivo,
        ],
        "IV Rank mínimo para vender": [
            settings.strategy.iv_rank_high_threshold.conservador,
            settings.strategy.iv_rank_high_threshold.moderado,
            settings.strategy.iv_rank_high_threshold.agresivo,
        ],
        "Umbral de convicción": [
            settings.conviction_thresholds.conservador,
            settings.conviction_thresholds.moderado,
            settings.conviction_thresholds.agresivo,
        ],
    }
)

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
st.subheader("Símbolos monitoreados (config/symbols.yaml)")
st.write(", ".join(get_symbols()))
st.caption("Para agregar o quitar símbolos, editá config/symbols.yaml directamente — no hace falta tocar código.")

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
st.subheader("Calculadora de interés compuesto")
st.caption(
    "Proyecta cuánto crecería tu cuenta con aportes anuales a una tasa de rendimiento constante. "
    "El aporte de cada año se suma al FINAL de ese año (no crece ese mismo año)."
)

avg_annualized = repo.get_average_annualized_return_pct(conn)
default_rate = avg_annualized if avg_annualized is not None else 15.0

col_a, col_b = st.columns(2)
with col_a:
    ci_initial = st.number_input("Capital inicial", min_value=0.0, value=float(capital), step=1000.0, key="ci_initial")
    ci_rate = st.number_input(
        "Rendimiento anual esperado (%)",
        value=float(default_rate),
        step=0.5,
        key="ci_rate",
        help=(
            f"Prellenado con el {avg_annualized:.1f}% — promedio real del rendimiento anualizado de tus "
            "alertas más recientes (ver página Alertas)."
            if avg_annualized is not None
            else "Todavía no hay alertas con rendimiento anualizado calculado para prellenar esto — valor de referencia, editalo."
        ),
    )
with col_b:
    ci_contribution = st.number_input("Aporte anual adicional", min_value=0.0, value=0.0, step=1000.0, key="ci_contribution")
    ci_years = st.selectbox("Horizonte (años)", [1, 2, 3, 4, 5], index=2, key="ci_years")

ci_projection = project_compound_growth(ci_initial, ci_rate, ci_contribution, ci_years)
ci_final_value = ci_projection[-1]["value"]

st.metric(
    f"Valor final proyectado a {ci_years} año(s)",
    f"${ci_final_value:,.2f}",
    delta=f"{ci_final_value - ci_initial:,.2f} vs. capital inicial",
)

ci_df = pd.DataFrame(ci_projection).rename(columns={"year": "Año", "value": "Valor proyectado"})
st.dataframe(
    ci_df,
    use_container_width=True,
    hide_index=True,
    column_config={"Valor proyectado": st.column_config.NumberColumn(format="$%.2f")},
)
st.line_chart(ci_df.set_index("Año")["Valor proyectado"])

st.warning(
    "Esto es una **proyección** bajo supuestos constantes (misma tasa de rendimiento todos los "
    "años, aporte fijo) — no una garantía. El rendimiento real de vender opciones varía año a "
    "año según condiciones de mercado, y puede ser negativo en años puntuales.",
    icon="⚠️",
)

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
st.subheader("Simulador de inflación / depreciación del dinero")
st.caption(
    "Compara qué pasa con tu capital medido en poder adquisitivo REAL (ajustado por inflación) "
    "si lo dejás sin invertir, en un banco/plazo fijo, o en una inversión alternativa — no solo "
    "el valor nominal, que no refleja cuánto podés comprar con eso en el futuro."
)

MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

macro_snapshot = repo.get_latest_macro_snapshot(conn)
has_cpi = macro_snapshot is not None and macro_snapshot["cpi_yoy_pct"] is not None
default_inflation = macro_snapshot["cpi_yoy_pct"] if has_cpi else 3.0
cpi_date_str = macro_snapshot["cpi_yoy_date"] if has_cpi else None
cpi_date = date.fromisoformat(cpi_date_str) if cpi_date_str else None
cpi_date_label = f"{MESES_ES[cpi_date.month - 1]} {cpi_date.year}" if cpi_date else None

col_i1, col_i2 = st.columns(2)
with col_i1:
    inf_initial = st.number_input("Capital inicial", min_value=0.0, value=50_000.0, step=1000.0, key="inf_initial")
    inf_inflation = st.number_input(
        "Tasa de inflación anual (%)",
        value=float(default_inflation),
        step=0.1,
        key="inf_inflation",
        help=(
            f"Prellenado con el {default_inflation:.1f}% — CPI interanual real de FRED"
            + (f", dato de {cpi_date_label}" if cpi_date_label else "")
            + " (se actualiza solo cuando la Fed publica un dato nuevo, ver Contexto macro en la página General)."
            " Editable, sin edición manual del dato de origen."
            if has_cpi
            else "Todavía no hay dato de CPI de FRED para prellenar esto (corré el análisis para traerlo) — valor de referencia, editalo."
        ),
    )
    if cpi_date_label:
        st.caption(f"CPI interanual de FRED: {default_inflation:.1f}% (dato de {cpi_date_label})")
with col_i2:
    inf_bank_rate = st.number_input("Tasa banco / plazo fijo (%)", value=4.0, step=0.5, key="inf_bank_rate")
    inf_alt_rate = st.number_input("Tasa inversión alternativa (%)", value=10.0, step=0.5, key="inf_alt_rate")

INF_YEARS = 5
inf_scenario_rates = {
    "Sin invertir": 0.0,
    "Banco/plazo fijo": inf_bank_rate,
    "Inversión alternativa": inf_alt_rate,
}
inf_scenarios = project_inflation_scenarios(
    initial_capital=inf_initial, inflation_rate_pct=inf_inflation, scenario_rates=inf_scenario_rates, years=INF_YEARS
)

inf_cols = st.columns(3)
for inf_col, (scenario_name, scenario_rate) in zip(inf_cols, inf_scenario_rates.items()):
    rows = inf_scenarios[scenario_name]
    final_row = rows[-1]
    color = CRITICAL if scenario_rate < inf_inflation else GOOD
    with inf_col:
        st.markdown(
            f"<div style='font-weight:700; color:{color};'>{icon('trending-down' if color == CRITICAL else 'trending-up', size=15, color=color)} "
            f"{scenario_name} ({scenario_rate:.1f}%)</div>",
            unsafe_allow_html=True,
        )
        st.metric(
            f"Valor real a {INF_YEARS} años (hoy)",
            f"${final_row['real_value']:,.2f}",
            delta=f"{final_row['real_change_pct']:+.1f}% poder adquisitivo real",
        )
        year_rows = [r for r in rows if r["year"] > 0]  # comparación pedida: año 1 a 5, sin repetir el año 0
        scenario_df = pd.DataFrame(year_rows).rename(
            columns={"year": "Año", "nominal_value": "Nominal", "real_value": "Real", "real_change_pct": "% real"}
        )
        st.dataframe(
            scenario_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Nominal": st.column_config.NumberColumn(format="$%.2f"),
                "Real": st.column_config.NumberColumn(format="$%.2f"),
                "% real": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )

inf_chart_df = pd.DataFrame(
    {scenario_name: [r["real_value"] for r in inf_scenarios[scenario_name]] for scenario_name in inf_scenario_rates}
)
inf_chart_df.insert(0, "Año", range(0, INF_YEARS + 1))
st.line_chart(inf_chart_df.set_index("Año"))

st.warning(
    "Esto es una **proyección** con inflación y tasas constantes durante los 5 años — no una "
    "predicción real. La inflación real varía mes a mes, y ningún banco o inversión garantiza "
    "la misma tasa todos los años.",
    icon="⚠️",
)
