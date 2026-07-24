from __future__ import annotations

from datetime import datetime

import streamlit as st

from options_advisor.dashboard.components import ACCENT, get_connection, get_settings, get_symbols, icon, inject_theme, render_header, render_notification_bell
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
    risk_level = st.selectbox(
        "Perfil de riesgo",
        ["conservador", "moderado", "agresivo"],
        index=["conservador", "moderado", "agresivo"].index(current.risk_level if current else defaults.risk_level),
    )
    threshold_override = st.number_input(
        "Umbral de convicción manual (vacío = usar el default del perfil)",
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

st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
st.subheader("Umbrales de convicción por defecto (config/settings.yaml)")
st.table(
    {
        "Perfil": ["conservador", "moderado", "agresivo"],
        "Umbral": [
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
