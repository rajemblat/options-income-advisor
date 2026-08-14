from __future__ import annotations

from datetime import date

import streamlit as st

from options_advisor.advisor import advisor as advisor_mod
from options_advisor.dashboard.components import (
    ACCENT,
    TEXT_MUTED,
    get_anthropic_api_key,
    get_broker,
    get_connection,
    get_settings,
    icon,
    inject_theme,
    render_header,
    render_notification_bell,
)
from options_advisor.storage import repository as repo

st.set_page_config(page_title="Lokshn · Moshe", page_icon="🤖", layout="wide", initial_sidebar_state="expanded")
inject_theme()
render_header(
    icon("lightbulb", size=24, color=ACCENT),
    "Moshe 🤖",
    "Charlá con Moshe, tu analista técnico. Ve tus posiciones, el VIX, RSI/soportes y lo que cayó en vivo. "
    "Te recomienda, VOS aprobás con un botón, y va aprendiendo tus preferencias. Nada se abre sin tu OK.",
)

conn = get_connection()
render_notification_bell(conn)
settings = get_settings()
broker = get_broker()
api_key = get_anthropic_api_key()
today = date.today()

if not api_key:
    st.warning("Falta configurar `ANTHROPIC_API_KEY` en el `.env` para que la IA analice en vivo. "
               "Igual podés ver el contexto y tus preferencias más abajo.", icon="🔑")

# ------------------------- Estado del chat en la sesión -------------------------
if "asesor_history" not in st.session_state:
    st.session_state["asesor_history"] = []   # lista de {role, content}
if "asesor_pending" not in st.session_state:
    st.session_state["asesor_pending"] = None  # id de sugerencia pendiente de aprobar

col_chat, col_side = st.columns([2, 1])

with col_chat:
    # Historial
    for msg in st.session_state["asesor_history"]:
        with st.chat_message("assistant" if msg["role"] == "assistant" else "user"):
            st.markdown(msg["content"])

    # Tarjeta de sugerencia pendiente (aprobar / rechazar)
    _pending_id = st.session_state.get("asesor_pending")
    if _pending_id:
        s = repo.get_ai_suggestion(conn, _pending_id)
        if s and s["status"] == "pending":
            _is_close = (s["action"] if "action" in s.keys() else "open") == "close"
            _is_sim = ("target" in s.keys() and s["target"] == "simulador")
            _kind = (s["sim_kind"] if "sim_kind" in s.keys() else None) or "put"
            _kind_es = "iron condor" if _kind == "condor" else "put"
            with st.container(border=True):
                if _is_sim:
                    st.markdown(f"#### 🧪 Cerrar en el SIMULADOR (paper): **{_kind_es} {s['symbol']} {s['strike']:g}**")
                    st.caption("Es plata de PRÁCTICA (no real). Se cierra al instante a su valor actual.")
                elif _is_close:
                    st.markdown(f"#### 🔻 Cerrar (REAL): **recomprar {s['symbol']} put {s['strike']:g}** · vto {s['expiration']}")
                    st.caption(f"{s['contracts']} contrato(s) · cierre MANUAL (te llevás la prima aunque no toque la regla)")
                else:
                    st.markdown(f"#### 💡 Sugerencia (REAL): **vender {s['symbol']} put {s['strike']:g}** · vto {s['expiration']}")
                    _cred = f"~${s['target_credit']:.2f}" if s["target_credit"] is not None else "—"
                    _floor = s["min_price"] if "min_price" in s.keys() else None
                    _floor_txt = f" · piso duro ${_floor:.2f} (no vende por debajo)" if _floor else ""
                    st.caption(f"{s['contracts']} contrato(s) · prima estimada {_cred}{_floor_txt}")
                if s["rationale"]:
                    st.write(s["rationale"])
                if _is_sim:
                    st.caption("Si aprobás, se cierra **ahora mismo** en el simulador y ves el P&L en la página Simulador.")
                elif _is_close:
                    st.caption("Si aprobás, el robot **recompra para cerrar** en el próximo escaneo (mercado abierto), "
                               "caminando el precio. Te manda el email con precio de cierre y P&L.")
                else:
                    st.caption("Si aprobás, el robot la manda en el próximo escaneo **con lo que pediste** "
                               "(los contratos que elegiste, SIN los topes del robot automático). Necesita el "
                               "START del día; solo la frena el kill switch o el buying power de tu cuenta Schwab.")
                _b1, _b2, _b3 = st.columns([1, 1, 3])
                with _b1:
                    _lbl = ("✅ Aprobar y cerrar (simulador)" if _is_sim
                            else ("✅ Aprobar y cerrar" if _is_close else "✅ Aprobar y abrir"))
                    if st.button(_lbl, type="primary", use_container_width=True, key="approve_sug"):
                        if _is_sim:
                            # Cierre del simulador: INSTANTÁNEO acá mismo (paper, no pasa por el robot).
                            from options_advisor.simulator import manual_close
                            _ok, _pnl, _note = manual_close.close_simulator_position(
                                conn, broker, settings, today, _kind, symbol=s["symbol"], strike=float(s["strike"]))
                            repo.resolve_ai_suggestion(conn, _pending_id, status=("sent" if _ok else "error"),
                                                       result_note=_note)
                            _msg = (f"✅ {_note}" if _ok else f"⚠️ No pude cerrarla: {_note}")
                        else:
                            repo.approve_ai_suggestion(conn, _pending_id)
                            if _is_close:
                                _msg = (f"✅ Aprobaste **cerrar {s['symbol']} put {s['strike']:g}** (vto {s['expiration']}). "
                                        f"El robot lo recompra en el próximo escaneo con el mercado abierto. Lo ves en "
                                        f"**Real Market → Cerradas**.")
                            else:
                                _msg = (f"✅ Aprobaste vender **{s['symbol']} put {s['strike']:g}** (vto {s['expiration']}). "
                                        f"El robot la va a mandar en el próximo escaneo si el guardián la aprueba "
                                        f"(acordate del START de hoy). Seguí el fill en **Real Market**.")
                        st.session_state["asesor_pending"] = None
                        st.session_state["asesor_history"].append({"role": "assistant", "content": _msg})
                        st.rerun()
                with _b2:
                    if st.button("✕ Descartar", use_container_width=True, key="reject_sug"):
                        repo.reject_ai_suggestion(conn, _pending_id)
                        st.session_state["asesor_pending"] = None
                        st.rerun()

    # Entrada del chat — con STREAMING (usuario 2026-08-11: 'está un poco lento'): la respuesta aparece
    # en vivo mientras Claude la genera. Ocultamos del display el bloque ```json (la sugerencia/preferencias
    # técnicas) para que el usuario vea solo el análisis; el texto completo se usa después para parsear.
    _prompt = st.chat_input("Preguntale a Moshe (ej: ¿qué opinás de NVDA técnicamente?)")
    if _prompt:
        st.session_state["asesor_history"].append({"role": "user", "content": _prompt})
        with st.chat_message("user"):
            st.markdown(_prompt)
        _context = advisor_mod.build_live_context(conn, broker, settings, today)
        _full_chunks: list[str] = []

        def _visible_stream():
            hide, buf = False, ""
            for _chunk in advisor_mod.stream_reply(
                settings, api_key, _context, st.session_state["asesor_history"][:-1], _prompt
            ):
                _full_chunks.append(_chunk)
                if hide:
                    continue
                buf += _chunk
                _idx = buf.find("```json")
                if _idx != -1:
                    if _idx > 0:
                        yield buf[:_idx]
                    hide, buf = True, ""
                elif len(buf) > 7:          # dejar una cola por si "```json" cae entre chunks
                    yield buf[:-7]
                    buf = buf[-7:]
            if not hide and buf:
                yield buf

        with st.chat_message("assistant"):
            st.write_stream(_visible_stream)
        _full_text = "".join(_full_chunks)
        reply = advisor_mod._parse_reply(
            _full_text, _context.get("allowed_symbols", []), _context.get("open_positions", []),
            _context.get("simulador", []))
        st.session_state["asesor_history"].append({"role": "assistant", "content": reply.reply_text})
        # Guardar preferencias nuevas (aprendizaje auditable)
        _saved = []
        for p in reply.new_preferences:
            try:
                repo.add_ai_preference(conn, p, source="chat")
                _saved.append(p)
            except Exception:
                pass
        if _saved:
            st.session_state["asesor_history"].append(
                {"role": "assistant", "content": "📝 Aprendí y guardé: " + "; ".join(f"*{p}*" for p in _saved)
                 + ". Lo vas a ver en el panel de la derecha (podés borrarlo cuando quieras)."}
            )
        # Registrar la sugerencia (queda pendiente de tu aprobación)
        if reply.suggestion:
            sug = reply.suggestion
            # Piso duro de precio (usuario 2026-08-11): 3 capas — lo que puso Moshe en el JSON, o si no,
            # lo que se extrae del mensaje del usuario ("no bajes de 3.00"). Solo aplica al ABRIR real.
            _min_price = sug.get("min_price")
            if _min_price is None and sug.get("action", "open") == "open" and sug.get("target", "real") == "real":
                _min_price = advisor_mod._extract_price_floor(_prompt)
            try:
                sug_id = repo.add_ai_suggestion(
                    conn, symbol=sug["symbol"], strike=sug["strike"], expiration=sug["expiration"],
                    contracts=sug["contracts"], target_credit=sug.get("target_credit"),
                    rationale=sug.get("rationale"), action=sug.get("action", "open"),
                    target=sug.get("target", "real"), sim_kind=sug.get("sim_kind"),
                    position_id=sug.get("position_id"), min_price=_min_price,
                )
                st.session_state["asesor_pending"] = sug_id
            except Exception:
                pass
        st.rerun()

    if not st.session_state["asesor_history"]:
        st.info("Escribile abajo. Por ejemplo: *\"¿qué entrarías hoy?\"*, *\"¿qué opinás de NVDA acá?\"*, "
                "*\"no quiero operar COIN\"* (esto último lo aprende como preferencia).", icon="💬")

with col_side:
    st.markdown("#### 🧠 Lo que aprendí de vos")
    st.caption("Preferencias que Moshe tiene en cuenta al recomendarte. Auditable: borrá la que ya no quieras.")
    _prefs = repo.list_ai_preferences(conn, active_only=True)
    if not _prefs:
        st.caption("Todavía no aprendí ninguna preferencia. Decile cosas como *\"priorizá las caídas fuertes\"* "
                   "o *\"no me gusta COIN con VIX alto\"* y las guarda.")
    else:
        for p in _prefs:
            _pc1, _pc2 = st.columns([5, 1])
            _pc1.markdown(f"• {p['text']}")
            if _pc2.button("🗑", key=f"delpref_{p['id']}", help="Borrar esta preferencia"):
                repo.delete_ai_preference(conn, p["id"])
                st.rerun()

    with st.form("add_pref_form", clear_on_submit=True):
        _newp = st.text_input("Agregar una preferencia a mano", placeholder="ej: vencimientos de 30 días")
        if st.form_submit_button("Agregar") and _newp.strip():
            repo.add_ai_preference(conn, _newp.strip(), source="manual")
            st.rerun()

    st.divider()
    st.markdown("#### 📋 Últimas sugerencias")
    _sugs = repo.list_ai_suggestions(conn, limit=8)
    if not _sugs:
        st.caption("Cuando Moshe te proponga una operación, va a aparecer acá con su estado.")
    else:
        _estado_es = {"pending": "⏳ esperando tu OK", "approved": "✅ aprobada (va al robot)",
                      "sent": "📤 enviada al mercado", "rejected": "✕ descartada/frenada",
                      "expired": "⌛ vencida", "error": "⚠️ error"}
        for s in _sugs:
            _st = _estado_es.get(s["status"], s["status"])
            _act = "🔻 cerrar" if (s["action"] if "action" in s.keys() else "open") == "close" else "vender"
            st.markdown(f"**{_act} {s['symbol']} put {s['strike']:g}** · {s['expiration']} — {_st}")
            if s["result_note"]:
                st.caption(s["result_note"])

    if st.button("🔄 Actualizar estados", use_container_width=True):
        st.rerun()
