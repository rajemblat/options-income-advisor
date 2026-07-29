from __future__ import annotations

import itertools
from datetime import date

import streamlit as st

from options_advisor.dashboard.components import (
    ACCENT,
    DATE_RANGE_OPTIONS,
    GOOD,
    TEXT_MUTED,
    filter_by_date_range,
    get_connection,
    get_symbols,
    icon,
    inject_theme,
    render_alert_card,
    render_header,
    render_notification_bell,
)
from options_advisor.storage import repository as repo

RISK_LEVEL_LABELS = {"conservador": "Conservador", "moderado": "Normal", "agresivo": "Agresivo"}
RISK_LEVEL_KEYS = ["conservador", "moderado", "agresivo"]

# Agrupa los strategy_type técnicos (cash_secured_put y short_put_naked son la misma
# categoría "Naked Put" para el usuario, ver STRATEGY_LABELS en alerts/formatting.py) en las
# 4 categorías del MVP (settings.strategy.enabled) para el filtro de estrategia.
STRATEGY_FILTER_GROUPS = {
    "naked_put": {"cash_secured_put", "short_put_naked"},
    "covered_call": {"covered_call"},
    "collar": {"collar"},
    "iron_condor": {"iron_condor"},
}
STRATEGY_FILTER_LABELS = {"naked_put": "Naked Put", "covered_call": "Covered Call", "collar": "Collar", "iron_condor": "Iron Condor"}

st.set_page_config(page_title="Alertas", page_icon="🔔", layout="wide")
inject_theme()
render_header(icon("bell", size=24, color=ACCENT), "Alertas — Ingreso a Largo Plazo")

conn = get_connection()
render_notification_bell(conn)

col_symbol, col_risk, col_strategy, col_range = st.columns(4)
with col_symbol:
    symbols = ["Todos"] + get_symbols()
    selected_symbol = st.selectbox("Símbolo", symbols)
with col_risk:
    risk_options = ["Todos"] + RISK_LEVEL_KEYS
    selected_risk = st.selectbox("Perfil de riesgo", risk_options, format_func=lambda k: RISK_LEVEL_LABELS.get(k, k))
with col_strategy:
    strategy_options = ["Todas"] + list(STRATEGY_FILTER_GROUPS)
    selected_strategy = st.selectbox("Estrategia", strategy_options, format_func=lambda k: STRATEGY_FILTER_LABELS.get(k, k))
with col_range:
    # Default "Hoy" (pedido 2026-07-29): la vista al entrar a la página muestra solo las
    # alertas de hoy, sin borrar nada del historial — el resto sigue accesible cambiando el
    # rango, mismo filtro ya usado en Operaciones (`DATE_RANGE_OPTIONS`/`filter_by_date_range`).
    range_labels = list(DATE_RANGE_OPTIONS.keys())
    selected_range = st.selectbox("Rango de fechas", range_labels, index=range_labels.index("Hoy"))

alerts = repo.get_alerts(conn, symbol=None if selected_symbol == "Todos" else selected_symbol, limit=200)
if selected_risk != "Todos":
    alerts = [a for a in alerts if a["risk_profile"] == selected_risk]
alerts_before_date_filter = alerts
alerts = filter_by_date_range(alerts, selected_range, date.today(), date_field="alert_date")

macro = repo.get_latest_macro_snapshot(conn)
fed_meeting_date = macro["fed_meeting_date"] if macro else None
investor_profile = repo.get_investor_profile(conn)
capital_available = investor_profile.capital_available if investor_profile else None

rows = []
for alert in alerts:
    candidate = conn.execute("SELECT * FROM candidate_contracts WHERE id = ?", (alert["candidate_contract_id"],)).fetchone()
    if selected_strategy != "Todas" and (candidate is None or candidate["strategy_type"] not in STRATEGY_FILTER_GROUPS[selected_strategy]):
        continue
    rows.append((alert, candidate))

if not rows:
    if alerts_before_date_filter:
        st.info("No hay alertas que coincidan con el rango de fechas y/o los demás filtros seleccionados.", icon="🔍")
    else:
        st.info("Todavía no hay alertas generadas para esta combinación de filtros. Corré el análisis desde la página principal.")
else:
    # Agrupadas por fecha (pedido 2026-07-29: mismo agrupamiento visual que ya existe en
    # Operaciones, "⚡ Hoy" separado de lo viejo) — `rows` ya viene ordenado DESC por alert_ts
    # desde el repo, así que fechas iguales quedan contiguas (agrupable sin resortear).
    today = date.today()
    for alert_date_str, group_iter in itertools.groupby(rows, key=lambda r: r[0]["alert_date"]):
        group = list(group_iter)
        alert_date = date.fromisoformat(alert_date_str)
        if alert_date == today:
            label_html = f"{icon('zap', size=15, color=GOOD)} Hoy"
        else:
            label_html = f"{icon('clock', size=15, color=TEXT_MUTED)} {alert_date.strftime('%d/%m/%Y')}"
        st.markdown(
            f"<div style='font-size:1.05rem; font-weight:700; color:{TEXT_MUTED}; margin:1.4rem 0 0.6rem;'>"
            f"{label_html} · {len(group)} alerta{'s' if len(group) != 1 else ''}</div>",
            unsafe_allow_html=True,
        )
        for alert, candidate in group:
            snapshot = repo.get_indicator_snapshot(conn, alert["symbol"], date.fromisoformat(alert["alert_date"]))
            next_earnings_date = snapshot["next_earnings_date"] if snapshot else None
            next_ex_dividend_date = snapshot["next_ex_dividend_date"] if snapshot else None
            render_alert_card(
                alert,
                candidate,
                next_earnings_date=next_earnings_date,
                fed_meeting_date=fed_meeting_date,
                next_ex_dividend_date=next_ex_dividend_date,
                capital_available=capital_available,
            )
