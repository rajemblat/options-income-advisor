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

    candidatos: list[CandidatoRoll] = []
    por_vencimiento: dict[date, str] = {}
    for c in puts:
        if c.expiration <= posicion.expiration:
            continue
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
        por_vencimiento[c.expiration] = c.occ_symbol

    mejor, explicacion = elegir_roll(candidatos, cfg)
    if mejor is None:
        return Descarte(posicion, explicacion)

    return Propuesta(
        posicion=posicion,
        spot=spot,
        dte_viejo=dte_viejo,
        candidato=mejor,
        occ_viejo=viejo.occ_symbol,
        occ_nuevo=por_vencimiento[mejor.expiration],
        costo_recompra=round(costo_recompra, 4),
        prima_nueva=round(costo_recompra + mejor.credito_neto, 4),
        motivo=f"{motivo}. {explicacion}",
    )


def _rango_de_cadena(dte_viejo: int, cfg) -> tuple[int, int]:
    """Qué pedazo de la cadena pedir: desde el vencimiento que ya tenés hasta el tope de días.

    El extremo de abajo incluye el vencimiento viejo porque de ahí sale el ASK de recompra; el de
    arriba es `max_dte` y ni un día más, así no se traen vencimientos que las reglas van a descartar."""
    desde = max(0, int(dte_viejo) - 1)
    hasta = max(desde + 1, int(getattr(cfg, "max_dte", 40) or 40) + 1)
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
            now=now,
        )
        resumen["propuestas"].append(pid)
        logger.info("Roll propuesto #%s: %s $%.2f %s → %s (%s)", pid, posicion.symbol,
                    posicion.strike, posicion.expiration.isoformat(),
                    resultado.candidato.expiration.isoformat(), resultado.motivo)

    return resumen
