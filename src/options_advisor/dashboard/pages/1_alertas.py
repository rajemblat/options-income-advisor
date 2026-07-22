from __future__ import annotations

import json

import streamlit as st

from options_advisor.dashboard.components import get_connection, get_symbols, risk_badge
from options_advisor.storage import repository as repo

st.set_page_config(page_title="Alertas", page_icon="🔔", layout="wide")
st.title("🔔 Alertas — Ingreso a Largo Plazo")

conn = get_connection()
symbols = ["Todos"] + get_symbols()
selected = st.selectbox("Símbolo", symbols)

alerts = repo.get_alerts(conn, symbol=None if selected == "Todos" else selected, limit=200)

if not alerts:
    st.info("Todavía no hay alertas generadas. Andá a la página principal y corré el análisis.")
else:
    for alert in alerts:
        candidate = conn.execute(
            "SELECT * FROM candidate_contracts WHERE id = ?", (alert["candidate_contract_id"],)
        ).fetchone()
        strategy_label = candidate["strategy_type"] if candidate else "?"
        with st.container(border=True):
            c1, c2, c3 = st.columns([2, 3, 2])
            c1.markdown(f"### {alert['symbol']}")
            c1.caption(f"{alert['alert_date']} · perfil {alert['risk_profile']}")
            c2.markdown(f"**{strategy_label}**")
            if candidate:
                c2.code(json.dumps(json.loads(candidate["strikes_json"]), ensure_ascii=False), language="json")
            c3.markdown(f"### {risk_badge(alert['conviction_score'])}")
            c3.caption(f"umbral aplicado: {alert['threshold_applied']}")
            st.write(alert["narrative_text"])
            st.caption(f"fuente de la narración: {alert['narrative_source']}")
