from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from options_advisor.dashboard.components import (
    ACCENT,
    cached_quotes,
    get_connection,
    get_settings,
    icon,
    inject_theme,
    render_header,
    render_notification_bell,
)
from options_advisor.simulator import rules
from options_advisor.storage import repository as repo

st.set_page_config(page_title="Lokshn · Prueba de estrés", page_icon="🧪", layout="wide", initial_sidebar_state="expanded")
inject_theme()
render_header(
    icon("alert-triangle", size=24, color=ACCENT),
    "Prueba de estrés",
    "Cuánto buying power te pediría Schwab si el mercado baja. Toma SOLO las operaciones que abrió el "
    "ROBOT (Real Market) — no tu portafolio manual — les baja el precio y recalcula el margen.",
)

conn = get_connection()
render_notification_bell(conn)
settings = get_settings()
today = date.today()

# --- SOLO las posiciones que abrió el robot (Real Market), de NUESTRO registro. No lee tu portafolio Schwab. ---
_robot = repo.get_open_real_put_positions(conn)
if not _robot:
    st.info("El robot no tiene puts reales abiertos para estresar. Cuando abra operaciones reales "
            "(automáticas o por el chat), aparecen acá solas.", icon="🧪")
    st.stop()

_syms = tuple(sorted({(r["symbol"] or "").strip().upper() for r in _robot}))
try:
    _quotes = cached_quotes(_syms)
except Exception:
    _quotes = {}

_c1, _c2 = st.columns([2, 2])
with _c1:
    _global = st.slider("📉 Cuánto baja el mercado (parejo, para todas)", min_value=-40, max_value=20,
                        value=-10, step=1, format="%d%%",
                        help="Aplica este % a TODAS. Abajo podés cambiar el % de cada posición por separado.")
with _c2:
    _bp_avail = st.number_input("💰 Tu buying power disponible (para la alerta)", min_value=0, value=180_000,
                                step=1_000, help="Para avisarte si en el escenario te quedarías sin BP.")

st.caption("Editá la columna **% baja** de cada fila para simular caídas distintas por acción "
           "(ej. NVDA −20% y el resto −5%). El resto de las columnas son de solo lectura.")

_base = []
for r in _robot:
    sym = (r["symbol"] or "").strip().upper()
    q = _quotes.get(sym)
    price = round(float(q.last_price), 2) if q is not None else None
    contracts = int(r["filled_contracts"] or r["final_contracts"] or 1)
    prem = float(r["fill_price"]) if r["fill_price"] is not None else None
    _base.append({
        "Símbolo": sym, "Strike": float(r["strike"]), "Cant": -abs(contracts),
        "Precio ahora": price, "% baja": int(_global),
        "_prem": prem, "_contracts": abs(contracts),
    })
_df = pd.DataFrame(_base)

_edited = st.data_editor(
    _df[["Símbolo", "Strike", "Cant", "Precio ahora", "% baja"]],
    hide_index=True, use_container_width=True, key="estres_editor",
    disabled=["Símbolo", "Strike", "Cant", "Precio ahora"],
    column_config={
        "Strike": st.column_config.NumberColumn(format="$%.2f"),
        "Precio ahora": st.column_config.NumberColumn(format="$%.2f"),
        "% baja": st.column_config.NumberColumn("% baja", min_value=-90, max_value=90, step=1, format="%d%%"),
    },
)


def _fnum(v):
    try:
        v = float(v)
        return v if v == v else None   # descarta NaN
    except (TypeError, ValueError):
        return None


# --- Margen naked por posición: max(0.20·subyacente − OTM + prima, 0.10·strike + prima) × 100 × contratos ---
_tot_now = 0.0
_tot_str = 0.0
_out = []
for i, r in _edited.iterrows():
    price = _fnum(_df.loc[i, "Precio ahora"])
    strike = _fnum(_df.loc[i, "Strike"])
    contracts = int(_df.loc[i, "_contracts"])
    prem = _fnum(_df.loc[i, "_prem"])
    if price is None or price <= 0 or strike is None:
        _out.append({"Símbolo": r["Símbolo"], "Strike": f"${strike:g}" if strike else "—", "Cant": int(_df.loc[i, "Cant"]),
                     "Precio ahora": "—", "% baja": f"{int(r['% baja'])}%", "Precio estresado": "—",
                     "BP ahora": "s/precio", "BP estresado": "s/precio", "Δ BP": "—"})
        continue
    move = float(r["% baja"]) / 100.0
    shocked = price * (1 + move)
    prem_now = prem if (prem is not None and prem > 0) else max(strike - price, 0.05)
    prem_str = max(strike - shocked, prem_now)   # al bajar, el put se mete ITM: prima ≥ intrínseco
    bp_now = rules.naked_put_margin(price, strike, prem_now) * contracts
    bp_str = rules.naked_put_margin(shocked, strike, prem_str) * contracts
    _tot_now += bp_now
    _tot_str += bp_str
    _out.append({
        "Símbolo": r["Símbolo"], "Strike": f"${strike:g}", "Cant": int(_df.loc[i, "Cant"]),
        "Precio ahora": f"${price:,.2f}", "% baja": f"{int(r['% baja'])}%",
        "Precio estresado": f"${shocked:,.2f}",
        "BP ahora": f"${bp_now:,.0f}", "BP estresado": f"${bp_str:,.0f}",
        "Δ BP": f"${bp_str - bp_now:+,.0f}",
    })

_m1, _m2, _m3 = st.columns(3)
_m1.metric("BP requerido AHORA", f"${_tot_now:,.0f}")
_m2.metric("BP requerido ESTRESADO", f"${_tot_str:,.0f}", delta=f"${_tot_str - _tot_now:+,.0f}")
_pct_uso = (_tot_str / _bp_avail * 100.0) if _bp_avail > 0 else None
_m3.metric("Uso de tu BP en el escenario", f"{_pct_uso:.0f}%" if _pct_uso is not None else "—")

if _bp_avail > 0 and _tot_str > _bp_avail:
    st.error(f"⚠️ En este escenario tu BP requerido (${_tot_str:,.0f}) SUPERA tu disponible "
             f"(${_bp_avail:,.0f}) — te faltarían ${_tot_str - _bp_avail:,.0f} (riesgo de margin call).", icon="🚨")
elif _bp_avail > 0 and _tot_str > 0.8 * _bp_avail:
    st.warning(f"Atención: usarías el {_pct_uso:.0f}% de tu BP. Te queda poco colchón.", icon="⚠️")
elif _bp_avail > 0:
    st.success(f"OK: en este escenario usarías el {_pct_uso:.0f}% de tu BP — te queda colchón.", icon="✅")

st.dataframe(pd.DataFrame(_out), use_container_width=True, hide_index=True)

st.caption("💡 **Estimación** con la fórmula de margen naked (naked-put margin de Reg-T): 20% del "
           "subyacente + la prima del put. Es un **techo conservador** — suele quedar POR ENCIMA del "
           '"BP Effect" que muestra el Explain Margin de Schwab (Schwab reporta el efecto marginal y usa '
           "portfolio / cross-margining). O sea: si acá aguantás el escenario, en Schwab también. El "
           "número exacto siempre es el de tu plataforma; esto es para planificar cuánto aguanta.")
