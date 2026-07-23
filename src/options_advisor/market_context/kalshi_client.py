from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

logger = logging.getLogger(__name__)

# Datos de mercado de Kalshi (mercado de predicción regulado por la CFTC) son públicos — no
# requieren autenticación ni API key, solo las endpoints de trading/cuenta la necesitan.
# https://docs.kalshi.com/getting_started/quick_start_market_data
BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
FED_SERIES_TICKER = "KXFED"
_TIMEOUT = 10.0


@dataclass
class FedRateProbabilities:
    meeting_date: date
    event_ticker: str
    current_upper_rate: float  # tasa objetivo (límite superior) vigente, referencia para "sube/baja/mantiene"
    hike_probability: float
    hold_probability: float
    cut_probability: float
    most_likely_upper_bound: float  # el límite superior de la franja con más probabilidad


def _mid_probability(market: dict) -> float | None:
    bid, ask = market.get("yes_bid_dollars"), market.get("yes_ask_dollars")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2


def _nearest_open_event(client: httpx.Client) -> dict | None:
    response = client.get(f"{BASE_URL}/events", params={"series_ticker": FED_SERIES_TICKER, "status": "open"})
    response.raise_for_status()
    events = response.json().get("events", [])
    if not events:
        return None
    return min(events, key=lambda e: e["strike_date"])


def get_fed_decision_probabilities(current_upper_rate: float) -> FedRateProbabilities | None:
    """Probabilidad de que la Fed suba, mantenga o baje la tasa en la próxima reunión, a partir
    de precios reales de mercado (contratos Kalshi sobre la tasa de fondos federales tras cada
    reunión) — nunca una estimación del narrador de IA. `current_upper_rate` es el límite
    superior del rango objetivo vigente (ver market_context.fred_client), usado solo para
    clasificar cada franja de strikes como suba/mantiene/baja relativo a hoy.

    None si Kalshi no responde o no hay datos suficientes — el pipeline sigue sin este dato,
    igual que el resto de fuentes externas."""
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            event = _nearest_open_event(client)
            if event is None:
                return None
            response = client.get(f"{BASE_URL}/markets", params={"event_ticker": event["event_ticker"], "status": "open"})
            response.raise_for_status()
            markets = response.json().get("markets", [])

        rungs = sorted(
            ((m["floor_strike"], _mid_probability(m)) for m in markets if _mid_probability(m) is not None),
            key=lambda pair: pair[0],
        )
        if not rungs:
            return None

        # Cada escalón del mercado es "¿la tasa terminará por encima de floor_strike?" — la
        # probabilidad de la franja (floor_i, floor_i+1] es la diferencia entre escalones
        # consecutivos (misma técnica que usa CME FedWatch para leer el strip de futuros).
        buckets: list[tuple[float, float, float]] = []  # (lower, upper, probability)
        prev_floor, prev_prob = 0.0, 1.0
        for floor, prob_above in rungs:
            buckets.append((prev_floor, floor, prev_prob - prob_above))
            prev_floor, prev_prob = floor, prob_above
        buckets.append((prev_floor, float("inf"), prev_prob))

        # Cada franja es (lower, upper]: si upper coincide con la tasa vigente, esa franja ES
        # "sin cambios" (la Fed se mueve en escalones de 0.25pp, así que la tasa actual
        # siempre coincide con un escalón exacto del mercado). Por encima = suba, por
        # debajo = baja.
        hike = hold = cut = 0.0
        for lower, upper, prob in buckets:
            prob = max(0.0, prob)
            if upper == current_upper_rate:
                hold += prob
            elif upper < current_upper_rate:
                cut += prob
            else:
                hike += prob

        most_likely = max(buckets, key=lambda b: b[2])

        strike_dt = datetime.fromisoformat(event["strike_date"].replace("Z", "+00:00")).astimezone(timezone.utc)
        return FedRateProbabilities(
            meeting_date=strike_dt.date(),
            event_ticker=event["event_ticker"],
            current_upper_rate=current_upper_rate,
            hike_probability=round(hike, 4),
            hold_probability=round(hold, 4),
            cut_probability=round(cut, 4),
            most_likely_upper_bound=most_likely[1] if most_likely[1] != float("inf") else most_likely[0],
        )
    except Exception:
        logger.warning("Kalshi (probabilidad de decisión de la Fed) no disponible; se omite este dato", exc_info=True)
        return None
