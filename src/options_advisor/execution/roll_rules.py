"""Cuándo rolear un put corto y a qué vencimiento — decisión PURA, sin red ni base.

La pidió el usuario el 2026-09-09, mirando dos AAL de strike $13 que vencían en 9 días con la acción
en $12.92: dentro del dinero, camino a la asignación, y el robot sin nada que hacer al respecto.

Su regla, textual: "cuando está ITM en los 20 días a expirar, roll semanal si paga, si no mensual,
no más de 40 días; el que pague mejor porcentualmente". Y sobre los detalles: el strike SIEMPRE se
mantiene, la comparación es por crédito POR DÍA, y a débito NUNCA — si nada paga dentro de los 40
días, avisa y decide él.

Por qué crédito por día y no crédito a secas: un vencimiento mensual casi siempre paga más dólares
que uno semanal, pero te ata cuatro veces más tiempo. Comparar los totales elige el plazo largo
siempre, sin importar si rinde. Dividir por los días agregados los pone en la misma unidad.

Por qué el crédito se mide con precios EJECUTABLES y no al mid: es la misma lección del 02/09 con el
condor, que costó $295. Se recompra el corto viejo pagando el ASK y se vende el nuevo cobrando el
BID. Si con esos números el roll no paga, no paga — y "pagaba al mid" es un consuelo que no se puede
cobrar.

Este módulo NO manda órdenes ni lee la cadena: recibe números y devuelve una decisión. Todo lo que
toca la red vive en el motor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CandidatoRoll:
    """Un vencimiento al que se podría rolear, con la cuenta ya hecha."""

    expiration: date
    dte: int                    # días hasta ESE vencimiento
    credito_neto: float         # por acción: lo que se cobra menos lo que cuesta recomprar
    dias_agregados: int         # cuántos días de más se compran
    credito_por_dia: float      # la vara de comparación

    @property
    def credito_total(self) -> float:
        """En dólares por contrato (100 acciones)."""
        return round(self.credito_neto * 100.0, 2)


def esta_itm(spot: float | None, strike: float | None) -> bool:
    """¿El put corto está dentro del dinero? Un put lo está cuando la acción cayó por DEBAJO del
    strike — ahí es cuando te pueden asignar."""
    if spot is None or strike is None:
        return False
    return float(spot) < float(strike)


def toca_rolear(spot: float | None, strike: float | None, dte: int | None,
                rolls_hechos: int, cfg) -> tuple[bool, str]:
    """¿Corresponde evaluar un roll de esta posición? Devuelve (sí/no, motivo).

    El motivo se devuelve SIEMPRE, también cuando la respuesta es que no: es lo que después se
    escribe en el registro para que el usuario pueda ver por qué el robot no roleó algo que él
    esperaba que roleara. Un freno sin motivo es lo que lo obligó a preguntarme "¿por qué no abrió?"
    todo el 09/09."""
    if not getattr(cfg, "enabled", False):
        return False, "el roll automático está apagado"
    if dte is None:
        return False, "no se sabe cuánto falta para el vencimiento"
    tope = int(getattr(cfg, "dte_trigger", 20) or 20)
    if dte > tope:
        return False, f"faltan {dte} días para el vencimiento (se rolea desde {tope})"
    if getattr(cfg, "solo_itm", True) and not esta_itm(spot, strike):
        return False, (f"la acción (${spot:,.2f}) está por encima del strike (${strike:,.2f}): "
                       "el put vence sin valor y no hay nada que rolear")
    maximo = int(getattr(cfg, "max_rolls", 0) or 0)
    if maximo > 0 and rolls_hechos >= maximo:
        return False, (f"esta posición ya se roleó {rolls_hechos} vez/veces (tope {maximo}). "
                       "Rolear sin límite convierte una pérdida chica en una posición eterna: "
                       "de acá en adelante lo decidís vos.")
    return True, f"ITM y faltan {dte} días"


def evaluar_candidato(costo_recompra: float, prima_nueva: float, dte_actual: int,
                      expiration: date, dte_nuevo: int) -> CandidatoRoll | None:
    """La cuenta de UN vencimiento candidato.

    `costo_recompra` es el ASK del put que ya tenés (lo que cuesta sacártelo de encima) y
    `prima_nueva` el BID del mismo strike en el vencimiento nuevo (lo que te pagan por asumirlo otra
    vez). Los dos ejecutables, no al mid.

    None si el vencimiento no agrega días — rolear "hacia atrás" o al mismo día no es rolear."""
    dias = int(dte_nuevo) - int(dte_actual)
    if dias <= 0:
        return None
    credito = round(float(prima_nueva) - float(costo_recompra), 4)
    return CandidatoRoll(
        expiration=expiration, dte=int(dte_nuevo), credito_neto=credito,
        dias_agregados=dias, credito_por_dia=round(credito / dias, 6),
    )


def elegir_roll(candidatos: list[CandidatoRoll], cfg) -> tuple[CandidatoRoll | None, str]:
    """El mejor roll entre los candidatos, o None con el motivo.

    Reglas del usuario, en orden:
      1. NUNCA a débito. Un roll que cuesta plata es pagar por posponer un problema.
      2. Nada más allá de `max_dte` días.
      3. Entre los que quedan, el de mejor CRÉDITO POR DÍA.
    """
    if not candidatos:
        return None, "no hay vencimientos disponibles para ese strike"
    tope_dte = int(getattr(cfg, "max_dte", 40) or 40)
    dentro = [c for c in candidatos if c.dte <= tope_dte]
    if not dentro:
        return None, f"todos los vencimientos disponibles pasan los {tope_dte} días"
    pagan = [c for c in dentro if c.credito_neto > 0]
    if not pagan:
        mejor = max(dentro, key=lambda c: c.credito_neto)
        return None, (f"ninguno paga crédito dentro de los {tope_dte} días "
                      f"(el mejor, {mejor.expiration.isoformat()}, dejaría "
                      f"${mejor.credito_total:,.2f}). No se rolea a débito.")
    # Desempate estable: a igual crédito por día, el vencimiento más CORTO. Menos tiempo atado a la
    # misma apuesta, y más chances de volver a decidir pronto.
    mejor = min(pagan, key=lambda c: (-c.credito_por_dia, c.dte))
    return mejor, (f"{mejor.expiration.isoformat()} paga ${mejor.credito_total:,.2f} por "
                   f"{mejor.dias_agregados} días más (${mejor.credito_por_dia * 100:,.2f} por día)")
