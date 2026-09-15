"""El detector de rolls: mira las posiciones abiertas y PROPONE — nunca manda (usuario 2026-09-14).

El reparto de tareas, y por qué es así:

    detector (acá)  →  propuesta en la base  →  el usuario aprueba  →  el scheduler manda

El dashboard nunca toca el broker: Streamlit re-ejecuta la página entera con cada clic, y una orden
real no puede depender de cuántas veces se redibujó una pantalla. Y el detector no manda nada porque
esta es la primera función del robot que CIERRA y ABRE posiciones reales por su cuenta: las primeras
las mira el usuario antes de que salgan.

Este módulo sí toca la red (pide la cadena de opciones). La decisión pura vive en `roll_rules`, que
no sabe nada de Schwab ni de SQLite. La separación es a propósito: las reglas del usuario se pueden
probar con números inventados, sin mercado abierto y sin inventar un broker falso.

Dos cosas que se cuidan especialmente:

  · **Los símbolos OCC salen de la cadena, nunca se arman a mano.** Entre que se propone y se
    aprueba pueden pasar horas. Un símbolo construido con reglas propias sobre el instrumento
    equivocado es el error más caro que puede cometer este sistema — y ya pasó una vez, con el
    condor de agosto, donde el root del semanal del SPX (SPXW) no coincide con el del subyacente.
  · **Los precios son EJECUTABLES, no el mid.** Se recompra el viejo pagando el ASK y se vende el
    nuevo cobrando el BID. Es la lección del 02/09, que costó $295: "pagaba al mid" es un consuelo
    que no se puede cobrar.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime

from options_advisor.broker.models import OptionChain
from options_advisor.execution.roll_rules import CandidatoRoll, elegir_roll, evaluar_candidato, toca_rolear
from options_advisor.storage import repository as repo

logger = logging.getLogger(__name__)

# Tolerancia al comparar strikes. Vienen de dos lados (la base y la cadena del broker) y son floats:
# 13.0 y 12.999999999 son el mismo strike y no puede fallar por eso.
_EPS_STRIKE = 0.001


@dataclass(frozen=True)
class Posicion:
    """Lo único que el detector necesita saber de una posición abierta."""

    open_order_id: int
    symbol: str
    strike: float
    expiration: date
    contracts: int


@dataclass(frozen=True)
class Propuesta:
    """Un roll listo para que el usuario lo mire."""

    posicion: Posicion
    spot: float
    dte_viejo: int
    candidato: CandidatoRoll
    occ_viejo: str
    occ_nuevo: str
    costo_recompra: float
    prima_nueva: float
    motivo: str
    # TODOS los vencimientos que pagan crédito, de mejor a peor por crédito POR DÍA. El usuario elige
    # (2026-09-15: "que me muestre un cartel y me diga que elija uno con todo el menú de opciones de
    # una semana a 90 días"). `candidato` es solo el recomendado, no una decisión tomada.
    menu: list[dict]


@dataclass(frozen=True)
class Descarte:
    """Esta posición NO se rolea, y por qué.

    El motivo se guarda siempre, también cuando es obvio. Un freno sin motivo es lo que obligó al
    usuario a preguntar "¿por qué no abrió?" todo el 09/09."""

    posicion: Posicion
    motivo: str


def posicion_desde_fila(fila) -> Posicion | None:
    """Traduce una fila de `live_order_log` a lo que necesita el detector.

    None si le falta algo esencial. Una fila sin strike o sin vencimiento no se puede rolear, y
    adivinarlos sería peor que saltearla."""
    try:
        exp = fila["expiration"]
        strike = fila["strike"]
        if not exp or strike is None:
            return None
        return Posicion(
            open_order_id=int(fila["id"]),
            symbol=fila["symbol"],
            strike=float(strike),
            expiration=date.fromisoformat(str(exp)[:10]),
            contracts=int(fila["filled_contracts"] or fila["final_contracts"] or 1),
        )
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _rolada_hoy(fila, hoy: date) -> bool:
    """¿Esta posición nació HOY de un roll? Tolerante a filas viejas sin la columna."""
    try:
        return bool(fila["roll_of"]) and str(fila["log_date"])[:10] == hoy.isoformat()
    except (KeyError, IndexError, TypeError):
        return False


def _mismo_strike(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) < _EPS_STRIKE


def evaluar_posicion(
    posicion: Posicion, chain: OptionChain, *, hoy: date, rolls_hechos: int, cfg
) -> Propuesta | Descarte:
    """La decisión completa para UNA posición, con la cadena ya en la mano. Sin red, sin base."""
    dte_viejo = (posicion.expiration - hoy).days
    spot = float(chain.underlying_price)

    corresponde, motivo = toca_rolear(spot, posicion.strike, dte_viejo, rolls_hechos, cfg)
    if not corresponde:
        return Descarte(posicion, motivo)

    puts = [c for c in chain.contracts
            if c.option_type == "put" and _mismo_strike(c.strike, posicion.strike)]

    viejo = next((c for c in puts if c.expiration == posicion.expiration), None)
    if viejo is None:
        return Descarte(posicion, f"el contrato que ya tenés (${posicion.strike:,.2f} al "
                                  f"{posicion.expiration.isoformat()}) no aparece en la cadena")
    if not viejo.occ_symbol:
        # Sin el símbolo exacto no se propone. Armarlo a mano es el error más caro posible.
        return Descarte(posicion, "el broker no devolvió el símbolo OCC del contrato que tenés")
    costo_recompra = float(viejo.ask or 0.0)
    if costo_recompra <= 0:
        return Descarte(posicion, "no hay ask para recomprar el put que tenés (sin liquidez ahora)")

    # Los vencimientos que nacen DENTRO de la ventana de disparo no se ofrecen (usuario
    # 2026-09-15). Rolear a 17 días cuando el disparador es a 20 significa que la posición nueva ya
    # cumple la condición para otro roll en el instante en que se abre — y eso fue exactamente lo
    # que pasó ese día: AAL se roleó a 2 OCT a las 12:12 y a las 12:12:14, cinco segundos después,
    # el mismo tick propuso rolearla de nuevo. Dos saltos cobraron $0.17 + $0.19 donde ir directo
    # pagaba $0.38: el spread se pagó dos veces por nada.
    piso_dte = int(getattr(cfg, "dte_trigger", 20) or 20)

    candidatos: list[CandidatoRoll] = []
    datos: dict[date, tuple[str, float]] = {}       # vencimiento → (símbolo OCC, prima)
    for c in puts:
        if c.expiration <= posicion.expiration:
            continue
        if (c.expiration - hoy).days <= piso_dte:
            continue                       # nacería ya pidiendo otro roll
        if not c.occ_symbol:
            continue
        prima = float(c.bid or 0.0)
        if prima <= 0:
            continue                       # nadie lo compra: no hay crédito que cobrar
        cand = evaluar_candidato(costo_recompra, prima, dte_viejo, c.expiration,
                                 (c.expiration - hoy).days)
        if cand is None:
            continue
        candidatos.append(cand)
        datos[c.expiration] = (c.occ_symbol, prima)

    mejor, explicacion = elegir_roll(candidatos, cfg)
    if mejor is None:
        if not candidatos:
            explicacion += (f" (solo se ofrecen vencimientos a más de {piso_dte} días: uno más "
                            "corto nacería pidiendo otro roll)")
        return Descarte(posicion, explicacion)

    # EL MENÚ. Todo lo que paga crédito dentro del tope de días, ordenado por crédito POR DÍA —
    # que es la vara con la que se comparan plazos distintos: un mensual casi siempre paga más
    # dólares que un semanal, pero te ata cuatro veces más tiempo.
    #
    # Los que darían débito quedan afuera: "si es débito ni me pregunta". Y los números que van acá
    # son los MISMOS que se van a mandar — no se recalculan al dibujar la pantalla.
    tope_dte = int(getattr(cfg, "max_dte", 90) or 90)
    menu = [
        {
            "expiration": c.expiration.isoformat(),
            "dte": c.dte,
            "dias_agregados": c.dias_agregados,
            "credito_neto": c.credito_neto,
            "credito_total": c.credito_total,
            "credito_por_dia": c.credito_por_dia,
            "prima_nueva": round(datos[c.expiration][1], 4),
            "occ": datos[c.expiration][0],
            "recomendado": c.expiration == mejor.expiration,
        }
        for c in sorted(candidatos, key=lambda x: (-x.credito_por_dia, x.dte))
        if c.credito_neto > 0 and c.dte <= tope_dte
    ]

    return Propuesta(
        posicion=posicion,
        spot=spot,
        dte_viejo=dte_viejo,
        candidato=mejor,
        occ_viejo=viejo.occ_symbol,
        occ_nuevo=datos[mejor.expiration][0],
        costo_recompra=round(costo_recompra, 4),
        prima_nueva=round(datos[mejor.expiration][1], 4),
        motivo=f"{motivo}. {explicacion}",
        menu=menu,
    )


def _rango_de_cadena(dte_viejo: int, cfg) -> tuple[int, int]:
    """Qué pedazo de la cadena pedir: desde el vencimiento que ya tenés hasta el tope de días.

    El extremo de abajo incluye el vencimiento viejo porque de ahí sale el ASK de recompra; el de
    arriba es `max_dte` y ni un día más, así no se traen vencimientos que las reglas van a descartar."""
    desde = max(0, int(dte_viejo) - 1)
    hasta = max(desde + 1, int(getattr(cfg, "max_dte", 90) or 90) + 1)
    return desde, hasta


def detectar_rolls(conn, broker, cfg, *, hoy: date | None = None,
                   now: datetime | None = None) -> dict:
    """Pasada completa: vence lo viejo, mira cada posición abierta y guarda las propuestas.

    Devuelve un resumen con las propuestas nuevas y los descartes CON SU MOTIVO, para que el
    dashboard y el log puedan explicar por qué el robot no propuso algo que el usuario esperaba.

    Nunca manda una orden. Nunca aprueba nada."""
    hoy = hoy or date.today()
    now = now or datetime.now()

    vencidas = repo.caducar_rolls_viejos(conn, hoy, now=now)

    resumen = {"vencidas": vencidas, "propuestas": [], "descartes": [], "errores": []}
    if not getattr(cfg, "enabled", False):
        # Se corta ANTES de pedir cadenas: con el roll apagado no tiene sentido gastar llamadas al
        # broker, y menos con el circuit breaker cuidando la cuota.
        resumen["descartes"].append(("—", "el roll automático está apagado"))
        return resumen

    cadenas: dict[str, OptionChain] = {}
    for fila in repo.get_open_real_put_positions(conn):
        posicion = posicion_desde_fila(fila)
        if posicion is None:
            continue
        if repo.hay_roll_pendiente_para(conn, posicion.open_order_id):
            continue          # ya hay una esperando: no se amontonan tarjetas iguales
        if _rolada_hoy(fila, hoy):
            # Rolear dos veces el mismo día es pagar el spread dos veces. El 15/09 AAL se roleó a
            # las 12:12 y de nuevo a las 12:18 — el segundo salto cobró $0.19 donde ir directo
            # pagaba $0.38. Si mañana sigue haciendo falta, mañana se propone.
            resumen["descartes"].append(
                (posicion.symbol, "ya se roleó hoy; si sigue correspondiendo, mañana se propone"))
            continue

        dte_viejo = (posicion.expiration - hoy).days
        # El filtro barato PRIMERO: si ni siquiera está en ventana, no se pide la cadena.
        if dte_viejo > int(getattr(cfg, "dte_trigger", 20) or 20):
            continue

        chain = cadenas.get(posicion.symbol)
        if chain is None:
            try:
                chain = broker.get_option_chain(
                    posicion.symbol, expiration_range_days=_rango_de_cadena(dte_viejo, cfg)
                )
            except Exception as exc:                       # noqa: BLE001 — un símbolo no tumba la pasada
                logger.warning("Roll: no pude traer la cadena de %s: %s", posicion.symbol, exc)
                resumen["errores"].append((posicion.symbol, str(exc)))
                continue
            cadenas[posicion.symbol] = chain

        resultado = evaluar_posicion(
            posicion, chain, hoy=hoy, cfg=cfg,
            rolls_hechos=repo.contar_rolls_de(conn, posicion.open_order_id),
        )
        if isinstance(resultado, Descarte):
            resumen["descartes"].append((posicion.symbol, resultado.motivo))
            logger.info("Roll: %s $%.2f no se rolea — %s",
                        posicion.symbol, posicion.strike, resultado.motivo)
            continue

        pid = repo.insert_roll_proposal(
            conn,
            open_order_id=resultado.posicion.open_order_id,
            symbol=resultado.posicion.symbol,
            strike=resultado.posicion.strike,
            contracts=resultado.posicion.contracts,
            expiration_vieja=resultado.posicion.expiration,
            expiration_nueva=resultado.candidato.expiration,
            dte_viejo=resultado.dte_viejo,
            dte_nuevo=resultado.candidato.dte,
            dias_agregados=resultado.candidato.dias_agregados,
            occ_viejo=resultado.occ_viejo,
            occ_nuevo=resultado.occ_nuevo,
            costo_recompra=resultado.costo_recompra,
            prima_nueva=resultado.prima_nueva,
            credito_neto=resultado.candidato.credito_neto,
            credito_por_dia=resultado.candidato.credito_por_dia,
            spot=resultado.spot,
            motivo=resultado.motivo,
            candidatos=resultado.menu,
            now=now,
        )
        resumen["propuestas"].append(pid)
        logger.info("Roll propuesto #%s: %s $%.2f %s → %s (%s)", pid, posicion.symbol,
                    posicion.strike, posicion.expiration.isoformat(),
                    resultado.candidato.expiration.isoformat(), resultado.motivo)

    return resumen


# ═══════════════ EJECUTAR LO QUE EL USUARIO APROBÓ ═══════════════
#
# El dashboard solo pone la propuesta en 'aprobada'. Mandar la orden pasa acá, en el scheduler.
#
# Por qué el precio NO se camina. En una apertura común el robot arranca pidiendo caro y va bajando
# hacia el mid hasta que llena. Acá no: el usuario aprobó UN crédito, con ese número a la vista, y
# caminar el precio significaría ejecutar algo distinto de lo que aprobó. Si no llena a ese precio,
# no llena — la propuesta queda como 'error' con el motivo y en la próxima pasada se vuelve a
# proponer con los precios nuevos, que es exactamente lo que corresponde.
#
# Y la orden es UNA sola, combinada (NET_CREDIT, dos patas). Nunca se manda la recompra y después la
# venta por separado: entre una y otra habría un instante sin cobertura, y si la segunda fallara la
# posición quedaría deshecha sin que nadie lo decidiera.

_ESTADOS_MUERTOS = {"CANCELED", "REJECTED", "EXPIRED"}


def precio_neto_del_fill(order_info: dict) -> float | None:
    """El crédito NETO por acción al que Schwab ejecutó DE VERDAD el roll: vendida − comprada.

    Se lee de las ejecuciones (`orderActivityCollection[].executionLegs[]`), no del límite. El 03/09
    el condor se mandó a $1.65 y Schwab llenó a $1.75: anotar el límite habría dejado $10 por
    contrato fuera del registro. Acá importa por lo mismo — y porque el crédito de entrada es la
    base sobre la que después se calcula todo.

    None —y el que llama se queda con el límite, diciendo que es un respaldo— si el dato no es
    confiable: todavía no hay ejecuciones publicadas, no llegaron las dos patas, o las cantidades no
    coinciden (un roll a medio armar: ese neto no significa nada). Nunca inventa."""
    patas: dict[object, str] = {}
    for leg in order_info.get("orderLegCollection") or []:
        leg_id = leg.get("legId")
        instr = str(leg.get("instruction") or "").upper()
        if leg_id is not None and instr:
            patas[leg_id] = instr
    if len(patas) != 2:
        return None

    ejecutado: dict[object, dict] = {}
    for actividad in order_info.get("orderActivityCollection") or []:
        for e in actividad.get("executionLegs") or []:
            leg_id = e.get("legId")
            precio, cantidad = e.get("price"), e.get("quantity")
            if leg_id not in patas or precio is None or cantidad is None:
                continue
            acc = ejecutado.setdefault(leg_id, {"monto": 0.0, "cantidad": 0.0})
            acc["monto"] += float(precio) * float(cantidad)
            acc["cantidad"] += float(cantidad)
    if len(ejecutado) != 2:
        return None
    cantidades = {round(d["cantidad"], 6) for d in ejecutado.values()}
    if len(cantidades) != 1 or 0.0 in cantidades:
        return None

    neto = 0.0
    for leg_id, d in ejecutado.items():
        promedio = d["monto"] / d["cantidad"]
        neto += promedio if patas[leg_id].startswith("SELL") else -promedio
    neto = round(neto, 4)
    return neto if neto > 0 else None


def escalera_de_credito(credito: float, pct: float, max_peldanos: int = 6) -> list[float]:
    """Los precios que se van a ir probando, del aprobado al piso (usuario 2026-09-15).

    El piso es `crédito × (1 − pct)`, redondeado a centavos y SIEMPRE mayor que cero: un roll a
    débito está prohibido, así que la escalera no puede cruzar el cero ni tocarlo.

    Se acotan los peldaños porque el tiempo no es gratis: el tick del roll corre cada 3 minutos y
    esta orden vive dentro de un tick. Con 6 peldaños de 20 segundos son 2 minutos como máximo. Si
    el rango da para más de 6 centavos, los pasos se agrandan en vez de multiplicarse.

    Siempre devuelve al menos el precio aprobado: aunque no haya margen para negociar, la orden se
    manda igual."""
    credito = round(float(credito), 2)
    if credito <= 0:
        raise ValueError("el crédito aprobado tiene que ser > 0")
    piso = max(0.01, round(credito * (1.0 - max(0.0, float(pct))), 2))
    if piso >= credito:
        return [credito]
    centavos = int(round((credito - piso) * 100))
    pasos = min(max(1, int(max_peldanos) - 1), centavos)
    precios = [credito]
    for i in range(1, pasos + 1):
        precio = round(credito - (credito - piso) * i / pasos, 2)
        if precio < precios[-1] and precio >= piso:
            precios.append(precio)
    if precios[-1] != piso:
        precios.append(piso)
    return precios


def _freno_para_ejecutar(conn, broker, settings) -> str | None:
    """None si se puede mandar; si no, el motivo en palabras.

    Se chequea TODO acá aunque parte ya esté chequeado al proponer: entre proponer y aprobar pueden
    pasar horas, y en el medio el usuario puede haber apretado el kill switch."""
    from options_advisor.scheduler.maquina_real import es_la_maquina_real
    from options_advisor.scheduler.market_calendar import market_session

    lt = settings.live_trading
    if not getattr(settings.roll, "enabled", False):
        return "el roll automático está apagado"
    if not getattr(lt, "enabled", False):
        return "el trading real está apagado"
    if repo.is_live_kill_switch(conn):
        return "el kill switch está activo"
    if not es_la_maquina_real(getattr(lt, "real_machine_hostname", None)):
        return "esta máquina no es la designada para operar en real"
    if broker is None or not hasattr(broker, "place_order"):
        return "el broker no puede mandar órdenes"
    if market_session() != "abierto":
        return "el mercado está cerrado"
    return None


def _anotar_el_roll(conn, fila, credito_real: float, schwab_order_id: str, now: datetime) -> int | None:
    """Deja el roll registrado en el libro: cierra la posición vieja y abre la nueva.

    Sin esto el roll sería invisible para el resto del robot — el cierre por objetivo, la exposición
    y el P&L seguirían mirando una posición que ya no existe, y la nueva no la cuidaría nadie.

    Los precios por pata: la orden es combinada, así que Schwab informa un NETO, no dos precios. Se
    usa el costo de recompra que se propuso y se despeja la prima nueva del neto REAL. El total —que
    es lo que mueve el P&L— queda exacto; el reparto entre las dos patas queda marcado como
    estimado, que es la verdad."""
    contratos = int(fila["contracts"])
    costo = float(fila["costo_recompra"])
    prima_nueva = round(costo + float(credito_real), 4)

    vieja = conn.execute(
        "SELECT fill_price, collateral FROM live_order_log WHERE id = ?", (fila["open_order_id"],)
    ).fetchone()
    entrada = float(vieja["fill_price"] or 0.0) if vieja else 0.0
    colateral = float(vieja["collateral"] or 0.0) if vieja else 0.0

    repo.mark_real_position_closed(
        conn, int(fila["open_order_id"]), close_ts=now, close_fill_price=costo,
        close_reason="roll", realized_pnl=round((entrada - costo) * 100.0 * contratos, 2),
        close_schwab_order_id=str(schwab_order_id), pnl_is_estimate=True,
    )

    nuevo_id = repo.insert_live_order_log(
        conn, log_date=now.date(), log_ts=now, symbol=fila["symbol"], action="SELL_TO_OPEN",
        strike=float(fila["strike"]), expiration=str(fila["expiration_nueva"]), approved=True,
        final_contracts=contratos, start_limit_price=prima_nueva, collateral=colateral,
        dry_run=False, sent=False,
        reasons=(f"roll de la posición #{fila['open_order_id']}: {fila['expiration_vieja']} → "
                 f"{fila['expiration_nueva']}, crédito neto ${float(credito_real) * 100.0:,.2f} "
                 f"por contrato"),
        payload_json=None, ladder_json=None, roll_of=int(fila["open_order_id"]),
    )
    repo.mark_live_order_sent(
        conn, nuevo_id, schwab_order_id=str(schwab_order_id), order_status="FILLED",
        fill_price=prima_nueva, filled_contracts=contratos, final_limit_price=prima_nueva,
        replacements=0, sent_ts=now,
    )
    return nuevo_id


def ejecutar_rolls_aprobados(conn, broker, settings, *, now: datetime | None = None,
                             sleep=time.sleep, clock=time.monotonic) -> dict:
    """Manda las propuestas que el usuario aprobó, una por una, y anota el resultado.

    Devuelve un resumen. Cada propuesta va en su propio try: que una falle no puede impedir la
    siguiente, y una propuesta nunca queda en 'aprobada' para siempre — o pasa a 'enviada' o pasa a
    'error' con el motivo."""
    now = now or datetime.now()
    resumen = {"enviadas": [], "fallidas": [], "freno": None}

    aprobadas = repo.get_roll_proposals(conn, status="aprobada")
    if not aprobadas:
        return resumen

    freno = _freno_para_ejecutar(conn, broker, settings)
    if freno:
        # No se marcan como error: el usuario las aprobó y siguen válidas hasta que venzan a fin de
        # día. Si el mercado está cerrado, mañana la caducidad las limpia sola.
        resumen["freno"] = freno
        logger.info("Roll: hay %d propuesta(s) aprobada(s) sin ejecutar — %s", len(aprobadas), freno)
        return resumen

    try:
        account_hash = broker.resolve_account_hash(
            getattr(settings.live_trading, "account_number", "") or None)
    except Exception as exc:  # noqa: BLE001
        resumen["freno"] = f"no se pudo resolver la cuenta de Schwab: {exc}"
        logger.exception("Roll: no se pudo resolver la cuenta")
        return resumen
    if not account_hash:
        resumen["freno"] = "no se pudo resolver la cuenta de Schwab"
        return resumen

    for fila in aprobadas:
        try:
            _ejecutar_una(conn, broker, account_hash, fila, now=now, cfg=settings.roll,
                          sleep=sleep, clock=clock, resumen=resumen)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Roll: falló la ejecución de la propuesta #%s", fila["id"])
            resumen["fallidas"].append((fila["id"], str(exc)))
            repo.cerrar_roll(conn, int(fila["id"]), status="error",
                             nota=f"error inesperado al ejecutar: {exc}", now=now)
    return resumen


def _ejecutar_una(conn, broker, account_hash, fila, *, now, cfg, sleep, clock, resumen) -> None:
    """Manda el roll y NEGOCIA: arranca en el crédito aprobado y baja de a poco hasta el piso.

    Por qué se negocia, si antes decía que caminar el precio era ejecutar algo distinto de lo
    aprobado: porque el usuario lo pidió explícitamente (2026-09-15, "si el roll no lo toma puede
    negociar y bajar un poco, un umbral") y porque el límite lo pone él — 15% del crédito. Dentro de
    ese margen sigue siendo la operación que aprobó; fuera, no, y por eso ahí se rinde y pregunta de
    nuevo en vez de seguir bajando.

    Y si agota el margen: CANCELA. Dejar la orden viva al piso significaría que, dos horas después y
    con la acción en otro lado, se ejecute un precio que el usuario miró una sola vez."""
    from options_advisor.execution import schwab_orders as so

    aprobado = float(fila["credito_neto"])
    escalera = escalera_de_credito(aprobado, getattr(cfg, "negociar_pct", 0.15),
                                   getattr(cfg, "negociar_max_peldanos", 6))
    por_peldano = max(1, int(getattr(cfg, "negociar_segundos_por_peldano", 20) or 20))
    contratos = int(fila["contracts"])

    def _payload(precio: float) -> dict:
        return so.build_roll_put(fila["occ_viejo"], fila["occ_nuevo"], contratos,
                                 net_credit_limit=precio)

    precio = escalera[0]
    oid = broker.place_order(account_hash, _payload(precio))
    logger.warning("Roll #%s ENVIADO: %s $%.2f %s → %s, crédito $%.2f (piso $%.2f, orden %s)",
                   fila["id"], fila["symbol"], float(fila["strike"]), fila["expiration_vieja"],
                   fila["expiration_nueva"], precio, escalera[-1], oid)

    estado, info, reemplazos = "", {}, 0

    def _sondear() -> str:
        nonlocal info
        try:
            info = broker.get_order(account_hash, oid) or {}
        except Exception:  # noqa: BLE001
            logger.debug("Roll: sondeo de estado falló (se reintenta)", exc_info=True)
            return ""
        return str(info.get("status") or "").upper()

    for peldano, siguiente in enumerate(escalera[1:] + [None]):
        inicio = clock()
        while clock() - inicio < por_peldano:
            sleep(2.0)
            estado = _sondear() or estado
            if estado == "FILLED" or estado in _ESTADOS_MUERTOS:
                break
        if estado == "FILLED" or estado in _ESTADOS_MUERTOS:
            break
        if siguiente is None:
            break
        try:
            oid = broker.replace_order(account_hash, oid, _payload(siguiente))
        except Exception:  # noqa: BLE001
            logger.exception("Roll #%s: no se pudo bajar el precio a $%.2f; se deja en $%.2f",
                             fila["id"], siguiente, precio)
            break
        precio, reemplazos = siguiente, reemplazos + 1
        logger.info("Roll #%s: no llenó a $%.2f, se baja a $%.2f (peldaño %d de %d)",
                    fila["id"], escalera[peldano], siguiente, peldano + 1, len(escalera) - 1)

    if estado == "FILLED":
        real = precio_neto_del_fill(info)
        origen = "schwab"
        if real is None:
            real, origen = precio, "limite"
            logger.warning("Roll #%s LLENÓ pero Schwab todavía no publicó las ejecuciones — se "
                           "registra el límite $%.2f como crédito", fila["id"], precio)
        nuevo_id = _anotar_el_roll(conn, fila, real, oid, now)
        regateo = "" if reemplazos == 0 else (
            f" tras bajar de ${aprobado:.2f} a ${precio:.2f} en {reemplazos} paso(s)")
        repo.cerrar_roll(conn, int(fila["id"]), status="enviada",
                         nota=(f"llenó a ${real:.2f} de crédito neto (origen: {origen}){regateo}; "
                               f"posición nueva #{nuevo_id}"),
                         schwab_order_id=str(oid), credito_real=real, now=now)
        resumen["enviadas"].append(fila["id"])
        return

    if estado in _ESTADOS_MUERTOS:
        detalle = str(info.get("statusDescription") or "")[:300]
        repo.cerrar_roll(conn, int(fila["id"]), status="error",
                         nota=f"Schwab devolvió {estado}. {detalle}".strip(),
                         schwab_order_id=str(oid), now=now)
        resumen["fallidas"].append((fila["id"], estado))
        return

    try:
        broker.cancel_order(account_hash, oid)
        cola = "se canceló"
    except Exception as exc:  # noqa: BLE001
        cola = f"NO se pudo cancelar ({exc}) — revisala en Schwab"
        logger.exception("Roll #%s: no se pudo cancelar la orden %s", fila["id"], oid)
    repo.cerrar_roll(conn, int(fila["id"]), status="error",
                     nota=(f"nadie lo tomó: bajé de ${aprobado:.2f} a ${escalera[-1]:.2f} (el piso "
                           f"del {getattr(cfg, 'negociar_pct', 0.15):.0%}) y no llenó; {cola}. "
                           "En la próxima pasada te lo propongo de nuevo con precios frescos, por "
                           "si querés otro vencimiento."),
                     schwab_order_id=str(oid), now=now)
    resumen["fallidas"].append((fila["id"], "no llenó ni al piso"))
