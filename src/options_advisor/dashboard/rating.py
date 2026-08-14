"""Puntuación de operaciones (👍/😐/👎 + nota) compartida por el Simulador y el Real Market.

Vivía dentro de `pages/12_simulador.py`, pero el usuario pidió poder puntuar los Iron Condors
"tanto en real como simulador" (2026-08-14) y una página de Streamlit no se puede importar desde
otra: al importarla se ejecutaría toda su interfaz. Así que lo común se mudó acá.
"""

from __future__ import annotations

import json

import streamlit as st

from options_advisor.storage import repository as repo


_VOTE_OPTS = ["—", "👍", "😐", "👎"]
_VOTE_TO_FB = {"👍": "good", "😐": "normal", "👎": "bad", "—": None}
_FB_TO_IDX = {"good": 1, "normal": 2, "bad": 3}


@st.fragment
def _render_rating(conn, decision_row, key_suffix: str, question: str, param_rows=None) -> None:
    """Bloque de puntuación 👍/😐/👎 + nota. Es un `st.fragment` + `st.form`: al Guardar recarga SOLO
    este bloque, no toda la página — antes recargaba todo (y volvía a pedir datos a Schwab) y la
    pantalla quedaba oscura varios segundos (usuario 2026-08-06). Ahora el guardado es instantáneo;
    la operación desaparece de la lista de pendientes cuando refrescás. "Normal" = neutral (indeciso):
    el aprendizaje lo trata como señal neutra, ni suma ni resta.

    Si se pasa `param_rows` (lista de (param_key, etiqueta, valor)), se muestra además un voto
    👍/😐/👎 POR CADA casillero (usuario 2026-08-06: "votar cada parámetro"). Todo se guarda con el
    mismo botón. El aprendizaje cruza esos votos con los pesos del cerebro."""
    if decision_row is None:
        st.caption("No se encontró la decisión de apertura para puntuar esta operación.")
        return
    did = decision_row["id"]
    cur_idx = _FB_TO_IDX.get(decision_row["user_feedback"], 0)
    stored_params = repo.get_decision_param_feedback(conn, did) if param_rows else {}
    with st.form(key=f"ratingform_{key_suffix}_{did}"):
        rc1, rc2 = st.columns([1, 2])
        with rc1:
            choice = st.radio(question, ["Sin marcar", "👍 Bien", "😐 Normal", "👎 Mal"], index=cur_idx, key=f"fb_{key_suffix}_{did}")
        with rc2:
            note = st.text_area("📝 Tu nota (enseñale con tus palabras)", value=decision_row["user_note"] or "",
                                key=f"note_{key_suffix}_{did}", height=90,
                                placeholder="Ej: buena entrada, venía cayendo y con IV alta / se cerró tarde, debió esperar…")

        param_widgets = {}
        if param_rows:
            with st.expander("🗳️ Votar cada parámetro (opcional) — enseñale qué estuvo bien/mal casillero por casillero"):
                st.caption("Dejá en **—** los que no quieras opinar. Los que el robot usa para decidir "
                           "(delta, cobertura, anualizado, POP, IV rank, día, liquidez, theta) ajustan cómo elige; "
                           "el resto queda guardado como tu historial detallado.")
                for pkey, plabel, pval in param_rows:
                    pc1, pc2 = st.columns([3, 2])
                    with pc1:
                        st.markdown(f"**{plabel}:** {pval}")
                    with pc2:
                        idx = _FB_TO_IDX.get(stored_params.get(pkey), 0)
                        param_widgets[pkey] = st.radio(
                            plabel, _VOTE_OPTS, index=idx, horizontal=True,
                            key=f"pv_{key_suffix}_{did}_{pkey}", label_visibility="collapsed")

        if st.form_submit_button("💾 Guardar puntuación"):
            fb = {"👍 Bien": "good", "😐 Normal": "normal", "👎 Mal": "bad"}.get(choice)
            repo.set_decision_feedback(conn, did, fb)
            repo.set_decision_note(conn, did, note)
            if param_rows:
                votes = {k: _VOTE_TO_FB[v] for k, v in param_widgets.items() if _VOTE_TO_FB.get(v)}
                repo.set_decision_param_feedback(conn, did, votes)
            marca = {"good": "👍 Bien", "normal": "😐 Normal", "bad": "👎 Mal"}.get(fb, "sin marcar")
            extra = f" · {sum(1 for v in param_widgets.values() if _VOTE_TO_FB.get(v))} parámetro(s) votado(s)" if param_rows else ""
            st.success(f"Guardado ✅ ({marca}{extra}). Pasa a **Puntuadas** al refrescar la página.")


def condor_data_rows(ctx: dict, r) -> list[tuple[str, str, str]]:
    """Los datos de UN Iron Condor para puntuarlo, al mismo nivel de detalle que los puts (usuario
    2026-08-14: "para los iron necesito más datos para puntuar, los mismos que los put").

    Además de lo que ya se veía (strikes y crédito), responde las dos preguntas que el usuario pidió
    explícitamente y que definen el riesgo de un condor:
      · CUÁNTO puede moverse el SPX antes de tocar cada pata vendida (la cobertura de cada lado);
      · CUÁNTOS PUNTOS hay entre la pata vendida y la comprada de cada lado (el ancho del ala, que es
        lo que fija la pérdida máxima).
    Sirve igual para el condor de papel y para el real: las dos tablas tienen las mismas columnas."""
    def g(k):
        try:
            v = r[k]
        except (KeyError, IndexError):
            return None
        return v

    def d(k):
        v = ctx.get(k)
        return v if isinstance(v, (int, float)) else None

    def viejo(k: str) -> str:
        """Texto para un dato que NO se guardaba cuando se abrió esa operación. Distinto de un dato
        que falta: acá el robot nunca lo tuvo. Sin esta distinción parecía un bug (usuario 2026-08-14:
        "no tengo los delta de los cortos" — estaba mirando una operación del 12/08, de antes de que
        se guardaran)."""
        return "no se guardaba aún" if k not in ctx else "—"

    spot = g("entry_spot") or d("spot")
    sp, sc = g("short_put_strike"), g("short_call_strike")
    lp, lc = g("long_put_strike"), g("long_call_strike")
    credito = g("entry_net_credit") or 0.0
    riesgo = g("max_loss") or 0.0
    be_lo, be_hi = g("lower_breakeven"), g("upper_breakeven")

    cob_put = ((spot - sp) / spot) if (spot and sp) else None
    cob_call = ((sc - spot) / spot) if (spot and sc) else None
    ala_put = (sp - lp) if (sp and lp) else None
    ala_call = (lc - sc) if (lc and sc) else None
    rango_pts = (be_hi - be_lo) if (be_lo and be_hi) else None

    def money(v, dec=2):
        return f"${v:,.{dec}f}" if isinstance(v, (int, float)) else "—"

    def pct(v, dec=2):
        return f"{v * 100:.{dec}f}%" if isinstance(v, (int, float)) else "—"

    estado = g("status")
    motivo = g("close_reason")
    pnl = g("realized_pnl")
    resultado = (f"{motivo} · {money(pnl)}" if estado == "closed" and pnl is not None
                 else (motivo or "—") if estado == "closed" else "abierto")

    return [
        ("spot", "SPX al abrir", money(spot, 0)),
        ("put_vendido", "Put VENDIDO (strike)", f"{sp:,.0f}" if sp else "—"),
        ("cobertura_put", "Cobertura del put — cuánto puede CAER", pct(cob_put)),
        ("call_vendido", "Call VENDIDO (strike)", f"{sc:,.0f}" if sc else "—"),
        ("cobertura_call", "Cobertura del call — cuánto puede SUBIR", pct(cob_call)),
        ("ala_put", "Ancho del ala PUT (vendido → comprado)",
         f"{ala_put:,.0f} pts  ({sp:,.0f} → {lp:,.0f})" if ala_put else "—"),
        ("ala_call", "Ancho del ala CALL (vendido → comprado)",
         f"{ala_call:,.0f} pts  ({sc:,.0f} → {lc:,.0f})" if ala_call else "—"),
        ("delta_put", "Delta del put corto",
         f"{d('short_put_delta'):.3f}" if d("short_put_delta") else viejo("short_put_delta")),
        ("delta_call", "Delta del call corto",
         f"{d('short_call_delta'):.3f}" if d("short_call_delta") else viejo("short_call_delta")),
        ("credito", "Crédito cobrado", money(credito)),
        ("riesgo", "Riesgo máximo", money(riesgo)),
        ("credito_riesgo", "Crédito / riesgo", pct(credito / riesgo, 1) if riesgo else "—"),
        ("rango", "Rango de ganancia (breakevens)",
         f"{be_lo:,.0f} – {be_hi:,.0f}" + (f"  ({rango_pts:,.0f} pts = {rango_pts / spot * 100:.2f}%)"
                                           if rango_pts and spot else "") if (be_lo and be_hi) else "—"),
        ("dia_rango", "Rango del día al entrar", pct(d("day_range_pct"))),
        ("vix", "Movimiento del VIX ese día",
         f"{d('vix_change_pct'):+.2f}%" if d("vix_change_pct") is not None else viejo("vix_change_pct")),
        ("fecha_hora", "Abierta el", (f"{g('entry_date')} " if g("entry_date") else "")
         + (str(g("entry_ts") or "")[11:19] or "")),
        ("resultado", "Resultado", resultado),
    ]


def _condor_grid_md(rows: list[tuple[str, str, str]]) -> str:
    """Los mismos datos en una tabla de dos columnas, para leerlos de un vistazo antes de puntuar."""
    mitad = (len(rows) + 1) // 2
    izq, der = rows[:mitad], rows[mitad:]
    lineas = ["| Dato | Valor | Dato | Valor |", "|---|---|---|---|"]
    for i in range(mitad):
        a = izq[i]
        b = der[i] if i < len(der) else None
        # Ojo con la celda vacía: `**{''}**` deja un literal "****" a la vista.
        der_dato = b[1] if b else ""
        der_valor = f"**{b[2]}**" if b else ""
        lineas.append(f"| {a[1]} | **{a[2]}** | {der_dato} | {der_valor} |")
    return "\n".join(lineas)


def _render_intraday_ratings(conn, strategy: str, open_rows, closed_rows, label_fn, key_prefix: str,
                             data_rows_fn=None, book: str = "paper") -> None:
    """Puntuación 👍/👎 + nota para las operaciones intradía (iron butterfly / iron condor), una por
    posición dentro de un expander. Enlaza cada posición con su decisión de apertura por el
    position_id del contexto (usuario 2026-08-05: "en los iron poner para puntuar")."""
    st.markdown("#### ⭐ Puntuá estas operaciones")
    st.caption("Decile 👍/👎 con una nota — así el robot aprende tu criterio también en el iron. "
               "Al guardar, la operación se va a **Puntuadas ✅** y desaparece de acá cuando refrescás.")
    shown = 0
    had_any = False
    for r in list(open_rows) + list(closed_rows)[:15]:
        dec = repo.get_intraday_open_decision(conn, strategy, r["id"], book=book)
        if dec is None:
            continue
        had_any = True
        # Ya puntuada → se oculta de la lista de pendientes (igual que los puts, usuario 2026-08-06).
        if dec["user_feedback"]:
            continue
        with st.expander(f"· sin puntuar  {label_fn(r)}"):
            filas = None
            if data_rows_fn is not None:
                try:
                    ctx = json.loads(dec["context_json"]) if dec["context_json"] else {}
                except (ValueError, TypeError):
                    ctx = {}
                filas = data_rows_fn(ctx, r)
                st.markdown(_condor_grid_md(filas))
                st.caption("Los datos de arriba son los de ESTA operación, tal como estaban al abrirla.")
            _render_rating(conn, dec, key_prefix, "¿La IA operó BIEN esta operación (entrada y salida)?",
                           param_rows=filas)
        shown += 1
    if shown == 0:
        if had_any:
            st.success("¡Listo! No te queda ninguna operación del iron por puntuar (para el período elegido). "
                       "Las que puntuaste están en **Puntuadas ✅**.")
        else:
            st.caption("Todavía no hay operaciones para puntuar (aparecen acá cuando el iron abra alguna).")


