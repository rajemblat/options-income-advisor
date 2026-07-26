from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from options_advisor.dashboard.components import ACCENT, get_connection, get_settings, get_symbols, icon, inject_theme, render_header, render_notification_bell
from options_advisor.dashboard.compound_interest import project_compound_growth
from options_advisor.storage import repository as repo
from options_advisor.storage.models import InvestorProfile

st.set_page_config(page_title="Configuración", page_icon="⚙️", layout="wide")
inject_theme()
render_header(icon("settings", size=24, color=ACCENT), "Configuración")

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
