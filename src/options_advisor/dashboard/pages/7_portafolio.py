from __future__ import annotations

import pandas as pd
import streamlit as st

from options_advisor.dashboard.components import ACCENT, get_broker, get_connection, get_settings, icon, inject_theme, render_header, render_notification_bell

st.set_page_config(page_title="Portafolio real", page_icon="💼", layout="wide")
inject_theme()
render_header(
    icon("briefcase", size=24, color=ACCENT),
    "Portafolio real",
    "Posiciones reales de tus cuentas Schwab — símbolo, cantidad, precio de entrada, valor actual y P&L",
)

conn = get_connection()
render_notification_bell(conn)
settings = get_settings()

if settings.broker.mode != "schwab":
    st.info(
        "Esta página necesita `broker.mode: schwab` en `config/settings.yaml` — en modo mock no hay "
        "cuentas reales de las que traer posiciones.",
        icon="💼",
    )
else:
    broker = get_broker()
    positions = broker.get_all_positions()

    if not positions:
        st.info(
            "No se encontraron posiciones (o falló la conexión a Schwab — revisá los logs). "
            "Si tenés posiciones reales y no aparecen, confirmá que la cuenta esté vinculada a esta app.",
            icon="💼",
        )
    else:
        accounts = sorted({p.account_number for p in positions})
        selected_account = st.selectbox("Cuenta", ["Todas"] + accounts)
        filtered = positions if selected_account == "Todas" else [p for p in positions if p.account_number == selected_account]

        total_value = sum(p.market_value for p in filtered)
        total_pnl = sum(p.unrealized_pnl for p in filtered)
        col1, col2, col3 = st.columns(3)
        col1.metric("Posiciones", len(filtered))
        col2.metric("Valor total", f"${total_value:,.2f}")
        col3.metric("P&L no realizado", f"${total_pnl:,.2f}", delta=f"{total_pnl:,.2f}")

        st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)

        df = pd.DataFrame(
            [
                {
                    "Cuenta": p.account_number,
                    "Símbolo": p.symbol,
                    "Descripción": p.description or "",
                    "Tipo": p.asset_type,
                    "Cantidad": p.quantity,
                    "Precio entrada": p.average_price,
                    "Valor actual": p.market_value,
                    "P&L": p.unrealized_pnl,
                }
                for p in filtered
            ]
        )
        df = df.sort_values("Valor actual", ascending=False, key=abs)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Precio entrada": st.column_config.NumberColumn(format="$%.2f"),
                "Valor actual": st.column_config.NumberColumn(format="$%.2f"),
                "P&L": st.column_config.NumberColumn(format="$%.2f"),
                "Cantidad": st.column_config.NumberColumn(format="%.0f"),
            },
        )
        st.caption(
            "Entrega 1: solo posiciones (símbolo, cantidad, precio de entrada, valor actual, P&L). "
            "Griegos/DTE/caveats de earnings-FOMC por posición de opciones y el análisis con IA "
            "quedan para la Entrega 2."
        )
