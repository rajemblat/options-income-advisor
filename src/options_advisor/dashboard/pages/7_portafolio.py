from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from options_advisor.dashboard.components import ACCENT, get_broker, get_connection, get_settings, icon, inject_theme, render_header, render_notification_bell
from options_advisor.dashboard.portfolio_analysis import (
    effective_projected_pnl_at_date,
    effective_projected_pnl_at_own_expiration,
    find_matching_contract_iv,
    position_pct_return,
)

st.set_page_config(page_title="Portafolio real", page_icon="💼", layout="wide")
inject_theme()
render_header(
    icon("briefcase", size=24, color=ACCENT),
    "Portafolio real",
    "Posiciones reales de tus cuentas Schwab, con % de retorno y proyecciones si el precio no cambia",
)

conn = get_connection()
render_notification_bell(conn)
settings = get_settings()


def _underlying_of(position) -> str:
    return position.underlying_symbol if (position.asset_type == "OPTION" and position.underlying_symbol) else position.symbol


def _display_symbol(position) -> str:
    if position.asset_type != "OPTION" or position.strike is None or position.expiration is None or position.option_type is None:
        return position.symbol  # opción con símbolo OCC no reconocido: mostrar el símbolo crudo
    side = "P" if position.option_type == "put" else "C"
    return f"{position.underlying_symbol} {position.expiration} ${position.strike:.0f}{side}"


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

        # Una sola llamada batch para el precio actual de todos los subyacentes (acciones,
        # ETFs, y el subyacente de cada posición de opción) — no 1 llamada por posición.
        underlyings = sorted({_underlying_of(p) for p in filtered})
        quotes = broker.get_quotes(underlyings)

        rows = []
        for p in filtered:
            current_price = quotes[_underlying_of(p)].last_price if _underlying_of(p) in quotes else None
            rows.append(
                {
                    "Cuenta": p.account_number,
                    "Símbolo": _display_symbol(p),
                    "Descripción": p.description or "",
                    "Tipo": p.asset_type,
                    "Cantidad": p.quantity,
                    "Precio entrada": p.average_price,
                    "Valor actual": p.market_value,
                    "P&L": p.unrealized_pnl,
                    "% P&L": position_pct_return(p),
                    "Proy. a vencimiento": effective_projected_pnl_at_own_expiration(p, current_price),
                }
            )

        df = pd.DataFrame(rows).sort_values("Valor actual", ascending=False, key=abs)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Precio entrada": st.column_config.NumberColumn(format="$%.2f"),
                "Valor actual": st.column_config.NumberColumn(format="$%.2f"),
                "P&L": st.column_config.NumberColumn(format="$%.2f"),
                "% P&L": st.column_config.NumberColumn(format="%.1f%%"),
                "Proy. a vencimiento": st.column_config.NumberColumn(format="$%.2f"),
                "Cantidad": st.column_config.NumberColumn(format="%.0f"),
            },
        )
        st.caption(
            "% P&L: ganancia/pérdida sobre la base de costo. Proy. a vencimiento: resultado si el precio del "
            "subyacente NO cambia hasta el vencimiento propio de cada posición (para acciones/ETFs, que no "
            "vencen, es el P&L de hoy — no hay decaimiento de tiempo que proyectar)."
        )

        st.markdown("<hr class='oia-divider'>", unsafe_allow_html=True)
        st.subheader("Proyección a una fecha específica")
        st.caption(
            "\"Si nada cambia hasta esa fecha\": mantiene el precio actual y la IV vigente de cada opción "
            "constantes, y repricea con Black-Scholes las posiciones que todavía no vencieron para esa fecha."
        )

        target_date = st.date_input("Fecha", value=date.today() + timedelta(days=90), min_value=date.today())

        if st.button("Calcular proyección", type="primary"):
            option_positions_needing_chain = [
                p for p in filtered if p.asset_type == "OPTION" and p.expiration and p.expiration > target_date
            ]
            underlyings_needing_chain = sorted({p.underlying_symbol for p in option_positions_needing_chain if p.underlying_symbol})

            with st.spinner(f"Pidiendo cadenas de opciones en vivo para {len(underlyings_needing_chain)} subyacente(s)..."):
                chains = {}
                for u in underlyings_needing_chain:
                    max_dte = max(
                        (p.expiration - date.today()).days for p in option_positions_needing_chain if p.underlying_symbol == u
                    )
                    try:
                        chains[u] = broker.get_option_chain(u, expiration_range_days=(0, max_dte + 5))
                    except Exception:
                        chains[u] = None  # esta posición puntual queda sin proyección (N/D), el resto sigue

                proj_rows = []
                for p in filtered:
                    current_price = quotes[_underlying_of(p)].last_price if _underlying_of(p) in quotes else None
                    iv = None
                    if p.asset_type == "OPTION" and p.expiration and p.expiration > target_date:
                        chain = chains.get(p.underlying_symbol)
                        iv = find_matching_contract_iv(chain, p) if chain else None
                    proj_pnl = effective_projected_pnl_at_date(p, current_price, target_date, iv, settings.market.risk_free_rate)
                    proj_rows.append(
                        {
                            "Símbolo": _display_symbol(p),
                            "P&L hoy": p.unrealized_pnl,
                            f"P&L proyectado ({target_date})": proj_pnl,
                        }
                    )

            proj_df = pd.DataFrame(proj_rows)
            known = proj_df[f"P&L proyectado ({target_date})"].dropna()
            total_projected = known.sum()
            unknown_count = proj_df[f"P&L proyectado ({target_date})"].isna().sum()

            st.metric(f"P&L total proyectado al {target_date}", f"${total_projected:,.2f}", delta=f"{total_projected - total_pnl:,.2f} vs. hoy")
            if unknown_count:
                st.caption(f"⚠️ {unknown_count} posición(es) sin proyección (no se encontró el contrato en la cadena en vivo).")

            st.dataframe(
                proj_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "P&L hoy": st.column_config.NumberColumn(format="$%.2f"),
                    f"P&L proyectado ({target_date})": st.column_config.NumberColumn(format="$%.2f"),
                },
            )

        st.caption(
            "Entrega 2 (sin IA todavía): % de retorno, proyección a vencimiento propio y a fecha elegida. "
            "El análisis narrado por IA sobre la exposición total del portafolio queda para la Entrega 3."
        )
