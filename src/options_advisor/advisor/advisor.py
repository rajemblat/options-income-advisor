"""Asesor AI (usuario 2026-08-10). Chat con contexto EN VIVO (posiciones reales, VIX, % del día de
la watchlist, oportunidades caídas) que RECOMIENDA operaciones de venta de puts y APRENDE preferencias
del usuario. No abre nada solo: propone UNA operación y el usuario la aprueba a mano; recién ahí el robot
la manda por el MISMO guardián real (START/kill/cupo/colateral). Todo fallo del LLM degrada a un texto
de respaldo — el chat nunca rompe la página.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)

_LLM_TIMEOUT_SECONDS = 40.0
_LLM_MAX_RETRIES = 2

SYSTEM_PROMPT = """Te llamás MOSHE. Sos un ANALISTA TÉCNICO EXPERTO y asesor de trading de opciones de un
usuario que VENDE puts cash-secured para cobrar prima (estrategia de ingreso). Hablás en español, claro y
directo, como un colega que sabe leer un gráfico y conoce su cuenta. Si te preguntan quién sos, sos Moshe,
su asesor. No hace falta que saludes con tu nombre en cada mensaje, solo cuando venga al caso.

SÉ BREVE Y DIRECTO: el usuario quiere respuestas cortas (2-4 frases) y sobre todo los BOTONES de
Aceptar/Rechazar. No te extiendas de más ni repitas.

REGLA CLAVE (usuario 2026-08-11): cuando el usuario te da una instrucción CONCRETA de abrir o cerrar una
operación (ej. "abrime un put en SPCX", "cerrá NVDA", "poné un put en UAL a 18%", "cerrá el iron del
simulador", "10 contratos en NU strike 12 a 45 días"), SIEMPRE tenés que TERMINAR con el bloque JSON de
`suggestion` para que le aparezcan los botones Aceptar/Rechazar. Nunca te quedes solo charlando sin
proponer la operación: si te dio una orden clara, tu respuesta corta + el bloque JSON es OBLIGATORIO. Si
de verdad falta un dato para armarla (ej. el strike), pedí ESE dato puntual en una frase, nada más.

NO RECHACES POR LOS LÍMITES DEL ROBOT (usuario 2026-08-11: "que el chat haga lo que le pido, que no mire
el límite"). El `max_contracts_per_order` y el cupo diario son SOLO para el robot automático, NO para lo
que el usuario te pide a mano en el chat. Si te pide 10 contratos, proponé 10 (poné "contracts": 10). Si
te pide varias del mismo símbolo, proponelas. Nunca digas "no puedo por el límite" — vos SIEMPRE proponés
lo que pidió y el usuario decide con el botón. (El único freno real es el kill switch y el buying power de
la cuenta, que los maneja el sistema al enviar, no vos.) Dominás: soportes y resistencias, tendencia
(medias móviles y sus cruces), momentum (RSI), volatilidad (ATR, IV rank), y cómo se traduce todo eso en
una buena venta de put (elegir strike apoyado en un soporte fuerte, con colchón, cuando la acción ya
corrigió).

Te paso un JSON de CONTEXTO EN VIVO con: sus posiciones reales abiertas (con P&L%), el VIX, y por cada
acción de su lista permitida su precio, el % del día y un bloque `tecnico` con RSI (rsi_14), medias
(sma_20/50/200), la señal de cruce (ma_cross), la tendencia leída de las medias, el ATR (atr_14), el IV
rank, y el soporte/resistencia más cercano con la distancia al soporte (dist_soporte_pct). Usá ESOS datos
para tu análisis — nada de generalidades. Si el usuario te pregunta por un símbolo, leé su `tecnico` y
respondé como un analista: dónde está parado respecto del soporte, si el RSI está sobrevendido (<30) o
sobrecomprado (>70), si la tendencia acompaña, si la volatilidad (IV rank) hace atractiva la venta de
prima, etc.

ALCANCE — MUY IMPORTANTE (no mezcles nunca REAL con SIMULADOR):
- CUENTA REAL (Real Market): venta de puts con plata de verdad, en `open_positions`. Podés proponer ABRIR
  o CERRAR posiciones reales. En el JSON, esas llevan "target": "real".
- SIMULADOR (paper, práctica): en `simulador` te paso sus posiciones abiertas, cada una con su `kind`
  ('put' o 'condor'). AHORA SÍ podés CERRARLAS a pedido del usuario (ej. "cerrá el iron condor del
  simulador porque ganó rápido") — el cierre es instantáneo y es plata de práctica, no real. NO podés
  ABRIR nuevas en el simulador desde el chat (eso lo hace el robot del simulador con sus reglas).
- Cuando propongas cerrar algo del SIMULADOR, en el JSON poné "target": "simulador" y "sim_kind" con el
  tipo ('put' o 'condor'), y `symbol`/`strike`/`expiration` que coincidan EXACTO con una posición de
  `simulador`. Para un condor, el `strike` es el del put corto (el que te paso en `simulador`).
- NUNCA confundas: si te piden cerrar algo real, target=real y matchea `open_positions`; si es del
  simulador, target=simulador y matchea `simulador`. No listes lo real como si fuera simulador ni al revés.

Reglas de la estrategia (respetalas al recomendar):
- Solo VENTA de puts (SELL_TO_OPEN), solo símbolos de la lista permitida (`allowed_symbols`).
- Preferí acciones que hoy estén al menos 1% ABAJO; entre varias, la MÁS caída, y que estén CERCA de un
  soporte fuerte (mirá `soporte_cercano` / `dist_soporte_pct`).
- Strike POR DEBAJO del precio actual (OTM), idealmente apoyado en/bajo el soporte fuerte; nunca un strike
  arriba del precio. Un RSI sobrevendido o IV rank alto refuerzan la idea.
- Vencimiento típico ~30-45 días.
- Respetá SIEMPRE las preferencias del usuario (`preferences`). Si una preferencia choca con una idea,
  no la recomiendes.
- Es plata REAL: si no hay una buena oportunidad, decilo y no fuerces ninguna.

PISO DURO DE PRECIO (usuario 2026-08-11: la orden llenó a 2.94 pidiendo 'no bajes de 3.00' — muy mal): si el
usuario te pone un mínimo al VENDER ("no bajes de 3.00", "mínimo 3", "piso 3.00", "que no baje de 3"), poné
ese número en el campo "min_price" de la sugerencia. Con eso la orden NUNCA se coloca ni se re-precia por
debajo de ese valor, aunque el mercado caiga (si el mercado no lo paga, no llena — es lo correcto). Si no te
dan un mínimo, dejá "min_price" en null.

Podés proponer COMO MÁXIMO UNA operación concreta por respuesta, y NUNCA se ejecuta sin que el usuario la
apruebe con un botón — aclarale eso cuando propongas algo.

Hay DOS tipos de operación que podés proponer:
- ABRIR ("action": "open"): vender un put nuevo.
- CERRAR ("action": "close"): recomprar/cerrar una posición que YA está abierta (mirá `open_positions`
  en el contexto, que trae el P&L% actual de cada una). Es un cierre MANUAL: el usuario puede pedirlo
  aunque NO se cumpla la regla automática (ej: "cerrá NVDA que está al 29%, presiento que mañana baja y
  me quiero llevar la prima"). Para un cierre, `symbol`, `strike` y `expiration` DEBEN coincidir EXACTO
  con una posición de `open_positions`. Cada posición trae un `id` ÚNICO: si el usuario te dice un ID (ej.
  "cerrá el #65" o "la 65"), poné ese número en `position_id` para cerrar EXACTAMENTE esa (sin confundir
  dos C con distinto vto/strike). Si hay varias iguales y no te dan el ID, pedile el ID.

APRENDIZAJE: si el usuario expresa una preferencia nueva (ej: "no me gusta COIN", "prefiero vencimientos
más cortos", "priorizá las caídas fuertes"), capturala.

FORMATO DE SALIDA — muy importante:
Primero escribí tu respuesta normal en español (análisis/recomendación conversacional). Si además querés
proponer una operación concreta o guardar preferencias nuevas, agregá AL FINAL un bloque JSON en una
sola valla de código ```json ... ``` con esta forma EXACTA:
```json
{"suggestion": {"action": "open", "symbol": "NVDA", "strike": 170, "expiration": "2026-09-19", "contracts": 1, "target_credit": 2.10, "min_price": 2.00, "rationale": "cayó 3.4% hoy, strike 4% OTM bajo soporte; el usuario pidió no bajar de 2.00"}, "new_preferences": ["no operar COIN con VIX alto"]}
```
(`min_price` es opcional: null si el usuario no puso un mínimo; el número si dijo "no bajes de X".)
Para un CIERRE REAL, por ejemplo (target "real"):
```json
{"suggestion": {"action": "close", "target": "real", "position_id": 65, "symbol": "NVDA", "strike": 170, "expiration": "2026-09-19", "contracts": 1, "rationale": "está al 29% de ganancia; el usuario quiere llevarse la prima"}, "new_preferences": []}
```
Para cerrar algo del SIMULADOR (target "simulador" + "sim_kind"), por ejemplo un iron condor:
```json
{"suggestion": {"action": "close", "target": "simulador", "sim_kind": "condor", "symbol": "$SPX", "strike": 7710, "expiration": "2026-08-11", "contracts": 1, "rationale": "ganó rápido en el simulador, el usuario quiere lockear"}, "new_preferences": []}
```
- `suggestion` puede ser null si no proponés nada concreto todavía; `action` es "open" o "close" (si falta, "open"); `target` es "real" (default) o "simulador".
- `new_preferences` es una lista (vacía si no hay ninguna nueva).
- No pongas NADA después del bloque JSON. Si no hay ni sugerencia ni preferencia, no pongas el bloque.
- Nunca inventes números que no puedas sostener con el contexto; si te falta un dato (ej. la prima
  exacta), estimá y aclaralo, no lo afirmes como cierto.
"""

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

# Respaldo determinístico para el piso de precio (usuario 2026-08-11: "no bajes de 3.00"). Captura frases
# como "no bajes de 3", "no baje de 3.00", "mínimo 3.00", "minimo 3", "piso 3.00", "no menos de 3".
_PRICE_FLOOR_RE = re.compile(
    r"(?:no\s+(?:baj\w+|menos)\s+(?:de\s+)?|m[íi]nimo\s+(?:de\s+)?|piso\s+(?:de\s+)?|no\s+menos\s+de\s+)"
    r"\$?\s*(\d+(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)


def _extract_price_floor(text: str) -> float | None:
    """Extrae un piso de precio del texto ('no bajes de 3.00' → 3.00). None si no encuentra uno válido."""
    if not text:
        return None
    m = _PRICE_FLOOR_RE.search(text)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", "."))
        return val if 0 < val < 10000 else None
    except (ValueError, TypeError):
        return None


@dataclass
class AdvisorReply:
    reply_text: str
    suggestion: dict | None = None
    new_preferences: list[str] = field(default_factory=list)
    source: str = "claude"


def _quotes_safe(broker, symbols):
    # Preferimos las quotes CACHEADAS del dashboard (ttl 60s) para no pegarle a Schwab en cada mensaje
    # del chat (era la causa de la lentitud, usuario 2026-08-11). Si no estamos en contexto Streamlit
    # (ej. tests), caemos al broker directo.
    try:
        from options_advisor.dashboard.components import cached_quotes
        return cached_quotes(tuple(symbols))
    except Exception:
        try:
            return broker.get_quotes(list(symbols))
        except Exception:
            logger.debug("Asesor: fallo al pedir quotes; se sigue sin ellas", exc_info=True)
            return {}


def _positions_safe(broker):
    """Posiciones reales, cacheadas 20s (mismo motivo de velocidad). Fallback al broker directo."""
    try:
        from options_advisor.dashboard.components import cached_all_positions
        return cached_all_positions()
    except Exception:
        try:
            return broker.get_all_positions()
        except Exception:
            return []


def _ta_of(snap, price) -> dict:
    """Resumen técnico COMPACTO de un símbolo, a partir de su último snapshot de indicadores (RSI, SMAs,
    ATR, IV rank, soportes/resistencias). Todo lo que falte queda en None. `price` es el precio EN VIVO."""
    if snap is None:
        return {}
    def g(k):
        try:
            return snap[k]
        except (KeyError, IndexError, TypeError):
            return None
    import json as _json
    sups, ress = [], []
    try:
        sups = sorted([float(x) for x in _json.loads(g("support_levels") or "[]")])
    except Exception:
        sups = []
    try:
        ress = sorted([float(x) for x in _json.loads(g("resistance_levels") or "[]")])
    except Exception:
        ress = []
    px = price if isinstance(price, (int, float)) else g("price")
    nearest_sup = max([s for s in sups if px is None or s <= px], default=None)
    nearest_res = min([r for r in ress if px is None or r >= px], default=None)
    sma20, sma50, sma200 = g("sma_20"), g("sma_50"), g("sma_200")
    # Tendencia simple leída de las medias (por encima = alcista).
    trend = None
    if isinstance(px, (int, float)) and isinstance(sma50, (int, float)):
        if isinstance(sma200, (int, float)):
            trend = "alcista" if px > sma50 > sma200 else ("bajista" if px < sma50 < sma200 else "lateral/mixta")
        else:
            trend = "alcista" if px > sma50 else "bajista"
    return {
        "rsi_14": round(g("rsi_14"), 1) if isinstance(g("rsi_14"), (int, float)) else None,
        "atr_14": round(g("atr_14"), 2) if isinstance(g("atr_14"), (int, float)) else None,
        "iv_rank": round(g("iv_rank"), 0) if isinstance(g("iv_rank"), (int, float)) else None,
        "sma_20": round(sma20, 2) if isinstance(sma20, (int, float)) else None,
        "sma_50": round(sma50, 2) if isinstance(sma50, (int, float)) else None,
        "sma_200": round(sma200, 2) if isinstance(sma200, (int, float)) else None,
        "ma_cross": g("ma_cross_signal"),
        "trend": trend,
        "soporte_cercano": round(nearest_sup, 2) if nearest_sup is not None else None,
        "resistencia_cercana": round(nearest_res, 2) if nearest_res is not None else None,
        "dist_soporte_pct": (round((px - nearest_sup) / px * 100.0, 1)
                             if (nearest_sup and isinstance(px, (int, float)) and px > 0) else None),
    }


def build_live_context(conn, broker, settings, as_of: date) -> dict:
    """Arma el contexto EN VIVO para el asesor: posiciones reales, VIX, % del día de la watchlist,
    candidatas caídas, cupo de hoy y preferencias. Nunca rompe: lo que falle queda vacío/None."""
    from options_advisor.storage import repository as repo

    lt = settings.live_trading
    allowed = list(lt.allowed_symbols or [])

    # VIX + quotes de la watchlist en un solo batch.
    quotes = _quotes_safe(broker, list(dict.fromkeys(allowed + ["$VIX"]))) if allowed else _quotes_safe(broker, ["$VIX"])
    vix_q = quotes.get("$VIX")
    vix = round(float(vix_q.last_price), 2) if vix_q is not None else None

    watchlist = []
    for sym in allowed:
        q = quotes.get(sym)
        if q is None:
            continue
        price = round(float(q.last_price), 2)
        # Análisis técnico del símbolo (RSI, SMAs, tendencia, soportes/resistencias) desde el último
        # snapshot guardado — sin llamada a la API, para que el asesor sea un experto técnico de verdad.
        try:
            snap = repo.get_latest_indicator_snapshot(conn, sym)
        except Exception:
            snap = None
        entry = {
            "symbol": sym,
            "price": price,
            "day_change_pct": round(float(q.net_change_pct), 2),
        }
        ta = _ta_of(snap, price)
        if ta:
            entry["tecnico"] = ta
        watchlist.append(entry)
    # Candidatas: las que cayeron al menos 1% hoy, más caída primero.
    down = sorted([w for w in watchlist if w["day_change_pct"] <= -1.0], key=lambda w: w["day_change_pct"])

    # Posiciones reales abiertas del robot, enriquecidas con el P&L% EN VIVO de Schwab (para que la IA
    # pueda decir "NVDA está al 29%" y proponer un cierre manual con dato real).
    sch = {}
    try:
        for p in _positions_safe(broker):
            if getattr(p, "option_type", None) == "put" and (p.quantity or 0) < 0 and p.strike and p.expiration:
                sch[(str(p.underlying_symbol or "").strip().upper(), round(float(p.strike), 2), p.expiration)] = p
    except Exception:
        sch = {}
    open_positions = []
    try:
        for r in repo.get_open_real_put_positions(conn):
            n = r["filled_contracts"] or r["final_contracts"] or 1
            credit = r["fill_price"]
            entry = {
                "id": r["id"],   # ID ÚNICO de la posición (usuario 2026-08-11): para cerrar la exacta sin confusión
                "symbol": r["symbol"], "strike": float(r["strike"]), "expiration": r["expiration"],
                "contracts": n, "credit": credit, "pnl_pct": None, "unrealized_pnl": None,
            }
            try:
                p = sch.get((str(r["symbol"]).strip().upper(), round(float(r["strike"]), 2),
                             date.fromisoformat(r["expiration"])))
                if p is not None and p.unrealized_pnl is not None:
                    premium = (credit or 0.0) * 100.0 * n
                    entry["unrealized_pnl"] = round(float(p.unrealized_pnl), 2)
                    if premium > 0:
                        entry["pnl_pct"] = round(float(p.unrealized_pnl) / premium * 100.0, 1)
            except Exception:
                pass
            open_positions.append(entry)
    except Exception:
        logger.debug("Asesor: fallo al leer posiciones abiertas", exc_info=True)

    # Cupo de hoy.
    try:
        used = repo.count_live_approved_opens_today(conn, as_of)
        cap = repo.get_max_live_orders_per_day(conn, lt.max_orders_per_day, as_of)
    except Exception:
        used, cap = None, None

    prefs = []
    try:
        prefs = [p["text"] for p in repo.list_ai_preferences(conn, active_only=True)]
    except Exception:
        logger.debug("Asesor: fallo al leer preferencias", exc_info=True)

    # Posiciones del SIMULADOR (paper). Moshe SÍ puede cerrarlas a pedido del usuario (cierre manual
    # instantáneo, usuario 2026-08-11: 'cerrá los iron del simulador'), pero NUNCA las mezcla con las
    # reales. Cada una trae su `kind` ('put'/'condor') para poder cerrarla.
    simulador = []
    try:
        for r in repo.get_open_simulated_positions(conn):
            simulador.append({
                "kind": "put",
                "symbol": r["symbol"],
                "estrategia": r["strategy_type"],
                "strike": float(r["strike"]) if r["strike"] is not None else None,
                "expiration": r["expiration_date"],
                "entry_premium": r["entry_premium"],
                "pnl_no_realizado": (round(float(r["last_unrealized_pnl"]), 2)
                                     if r["last_unrealized_pnl"] is not None else None),
            })
    except Exception:
        logger.debug("Asesor: fallo al leer puts del simulador", exc_info=True)
    try:
        for r in repo.get_open_condor_positions(conn):
            simulador.append({
                "kind": "condor",
                "symbol": r["underlying"],
                "estrategia": "iron_condor",
                "strike": float(r["short_put_strike"]),   # identificador (put corto)
                "expiration": r["expiration_date"],
                "entry_premium": r["entry_net_credit"],
                "pnl_no_realizado": (round(float(r["last_unrealized_pnl"]), 2)
                                     if r["last_unrealized_pnl"] is not None else None),
            })
    except Exception:
        logger.debug("Asesor: fallo al leer condors del simulador", exc_info=True)

    return {
        "today": as_of.isoformat(),
        "vix": vix,
        "allowed_symbols": allowed,
        "watchlist": watchlist,
        "candidates_down": down,
        "open_positions": open_positions,
        "simulador": simulador,
        "daily_cap": {"used": used, "cap": cap, "remaining": (None if cap is None or used is None else max(0, cap - used))},
        "preferences": prefs,
        "max_contracts_per_order": lt.max_contracts_per_order,
    }


def _parse_reply(text: str, allowed_symbols: list[str], open_positions: list[dict] | None = None,
                 sim_positions: list[dict] | None = None) -> AdvisorReply:
    """Separa el texto conversacional del bloque JSON (sugerencia + preferencias). Valida la sugerencia:
    ABRIR real → símbolo en la whitelist. CERRAR real → matchea `open_positions`. CERRAR simulador
    (target='simulador') → matchea `sim_positions` por kind+symbol+strike. Si algo no cierra, ignora la
    sugerencia pero conserva el texto — nunca propone una orden inválida."""
    suggestion = None
    new_prefs: list[str] = []
    display = text
    m = _JSON_BLOCK_RE.search(text)
    if m:
        display = (text[: m.start()] + text[m.end():]).strip()
        try:
            data = json.loads(m.group(1))
        except (ValueError, TypeError):
            data = {}
        raw = data.get("suggestion")
        if isinstance(raw, dict):
            try:
                action = str(raw.get("action", "open")).strip().lower() or "open"
                if action not in ("open", "close"):
                    action = "open"
                target = str(raw.get("target", "real")).strip().lower() or "real"
                if target not in ("real", "simulador"):
                    target = "real"
                sim_kind = str(raw.get("sim_kind", "")).strip().lower() or None
                try:
                    position_id = int(raw.get("position_id")) if raw.get("position_id") not in (None, "") else None
                except (ValueError, TypeError):
                    position_id = None
                sym = str(raw.get("symbol", "")).strip().upper()
                strike = float(raw.get("strike"))
                exp = str(raw.get("expiration", "")).strip()
                date.fromisoformat(exp)  # valida formato YYYY-MM-DD
                contracts = int(raw.get("contracts") or 1)
                ok = bool(sym) and strike > 0 and contracts >= 1
                if target == "simulador":
                    # Cierre del simulador: solo 'close', y tiene que matchear una posición del simulador.
                    action = "close"
                    match = next((p for p in (sim_positions or [])
                                  if str(p.get("symbol", "")).upper() == sym
                                  and abs(float(p.get("strike") or 0) - strike) < 1e-6), None)
                    ok = ok and match is not None
                    if match is not None and not sim_kind:
                        sim_kind = match.get("kind")
                elif action == "open":
                    allowed_up = {s.upper() for s in (allowed_symbols or [])}
                    ok = ok and (not allowed_up or sym in allowed_up)
                else:  # close real: por ID exacto si lo dieron, o por símbolo+strike+vto
                    if position_id is not None:
                        ok = ok and any(int(p.get("id", -1)) == position_id for p in (open_positions or []))
                    else:
                        ok = ok and any(
                            (p.get("symbol", "").upper() == sym and abs(float(p.get("strike", 0)) - strike) < 1e-6
                             and str(p.get("expiration")) == exp)
                            for p in (open_positions or [])
                        )
                if ok:
                    tc = raw.get("target_credit")
                    mp = raw.get("min_price")
                    min_price = float(mp) if isinstance(mp, (int, float)) and float(mp) > 0 else None
                    # Respaldo determinístico: si Moshe no completó min_price pero el texto pide un piso
                    # ("no bajes de 3.00", "mínimo 3", "piso 3.00"), lo extraemos igual (solo aperturas).
                    if min_price is None and action == "open" and target == "real":
                        min_price = _extract_price_floor(text)
                    suggestion = {
                        "action": action, "target": target, "sim_kind": sim_kind, "position_id": position_id,
                        "symbol": sym, "strike": strike, "expiration": exp, "contracts": contracts,
                        "target_credit": float(tc) if isinstance(tc, (int, float)) else None,
                        "min_price": min_price,
                        "rationale": str(raw.get("rationale") or "").strip() or None,
                    }
            except (ValueError, TypeError, KeyError):
                suggestion = None
        prefs = data.get("new_preferences")
        if isinstance(prefs, list):
            new_prefs = [str(p).strip() for p in prefs if str(p).strip()]
    return AdvisorReply(reply_text=display or text, suggestion=suggestion, new_preferences=new_prefs)


def _build_messages(context: dict, history: list[dict], user_message: str) -> list[dict]:
    messages = []
    for h in history[-12:]:  # ventana acotada de contexto conversacional
        role = "assistant" if h.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": str(h.get("content", ""))})
    messages.append({
        "role": "user",
        "content": "CONTEXTO EN VIVO (JSON):\n" + json.dumps(context, ensure_ascii=False, default=str)
                   + "\n\nMensaje del usuario:\n" + user_message,
    })
    return messages


def chat(conn, broker, settings, api_key: str | None, history: list[dict], user_message: str,
         as_of: date) -> AdvisorReply:
    """Una vuelta de conversación (NO streaming). `history` = lista de {role, content} previos (sin el
    mensaje nuevo). Degrada a un texto de respaldo si no hay api_key o el LLM falla."""
    context = build_live_context(conn, broker, settings, as_of)
    if not api_key:
        return AdvisorReply(
            reply_text=("No tengo la clave de la IA configurada (ANTHROPIC_API_KEY), así que no puedo "
                        "analizar en vivo ahora. Igual te dejo el contexto: " + _context_summary(context)),
            source="fallback",
        )
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, timeout=_LLM_TIMEOUT_SECONDS, max_retries=_LLM_MAX_RETRIES)
        response = client.messages.create(
            model=settings.llm.model,
            max_tokens=max(settings.llm.max_tokens, 1500),
            system=SYSTEM_PROMPT,
            messages=_build_messages(context, history, user_message),
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise ValueError("Respuesta vacía de Claude")
        return _parse_reply(text, context.get("allowed_symbols", []), context.get("open_positions", []),
                            context.get("simulador", []))
    except Exception:
        logger.exception("Asesor: fallo al conversar con Claude; devuelvo respaldo")
        return AdvisorReply(
            reply_text=("Se me complicó conectar con la IA en este momento — probá de nuevo en unos "
                        "segundos. Mientras, el contexto en vivo es: " + _context_summary(context)),
            source="fallback",
        )


def stream_reply(settings, api_key: str | None, context: dict, history: list[dict], user_message: str):
    """Generador de chunks de texto para mostrar la respuesta EN STREAMING (st.write_stream) — así el
    usuario ve la respuesta aparecer en vivo en vez de esperar el bloque completo (usuario 2026-08-11:
    'está un poco lento'). Si no hay api_key o el LLM falla, hace `yield` de un texto de respaldo. El
    parseo de la sugerencia se hace después, sobre el texto completo, con `_parse_reply`."""
    if not api_key:
        yield ("No tengo la clave de la IA configurada (ANTHROPIC_API_KEY), así que no puedo analizar en "
               "vivo ahora. Igual te dejo el contexto: " + _context_summary(context))
        return
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, timeout=_LLM_TIMEOUT_SECONDS, max_retries=_LLM_MAX_RETRIES)
        with client.messages.stream(
            model=settings.llm.model,
            max_tokens=max(settings.llm.max_tokens, 1500),
            system=SYSTEM_PROMPT,
            messages=_build_messages(context, history, user_message),
        ) as stream:
            for chunk in stream.text_stream:
                yield chunk
    except Exception:
        logger.exception("Asesor: fallo el streaming con Claude; devuelvo respaldo")
        yield ("Se me complicó conectar con la IA en este momento — probá de nuevo en unos segundos. "
               "Mientras, el contexto en vivo es: " + _context_summary(context))


def _context_summary(context: dict) -> str:
    """Resumen corto del contexto, para el fallback sin LLM."""
    vix = context.get("vix")
    down = context.get("candidates_down") or []
    cap = context.get("daily_cap") or {}
    parts = []
    if vix is not None:
        parts.append(f"VIX {vix}")
    if down:
        top = ", ".join(f"{d['symbol']} {d['day_change_pct']:+.1f}%" for d in down[:5])
        parts.append(f"más caídas hoy: {top}")
    else:
        parts.append("hoy no hay acciones de tu lista al menos 1% abajo")
    if cap.get("cap") is not None:
        parts.append(f"cupo hoy {cap.get('used')}/{cap.get('cap')}")
    return " · ".join(parts) + "."
