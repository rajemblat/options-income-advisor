from __future__ import annotations

import json

from options_advisor.alerts.formatting import strategy_label
from options_advisor.strategy.payoff import probability_otm

# Sección "Vista tabla en Escaneo" (pedido 2026-07-27): transforma filas de
# `repo.get_recent_single_leg_candidates` (candidatos de una sola pata + IV Rank ya unido) en
# filas planas listas para una tabla ordenable — lógica pura, sin Streamlit, para poder
# testearla sin un runtime de UI (mismo patrón que dashboard/portfolio_analysis.py).


def _pct_distance(option_type: str, reference: float, underlying_price: float) -> float | None:
    """% que el subyacente necesita moverse desde el precio actual hasta `reference` (strike o
    breakeven) — mismo sentido/signo que `compute_coverage`: positivo cuando `reference` está
    del lado OTM (más seguro), sirve tanto para Moneyness (contra el strike) como %BE (contra
    el breakeven)."""
    if not underlying_price:
        return None
    if option_type == "put":
        return (underlying_price - reference) / underlying_price * 100
    return (reference - underlying_price) / underlying_price * 100


def build_scanner_rows(
    candidates: list[dict],
    risk_free_rate: float | None = None,
    instrument_types: dict[str, str | None] | None = None,
) -> list[dict]:
    """Une legs_json/breakevens_json (JSON crudo tal como vienen de sqlite3.Row) en una fila
    plana por candidato. Candidatos sin datos suficientes (sin legs, sin underlying_price) se
    omiten — no tiene sentido una fila de screener sin precio ni pata para calcular nada.

    `risk_free_rate`/`instrument_types` son opcionales (Sección 'Pestaña Screener', pedido
    2026-07-27) — sin ellos, "Probabilidad OTM (%)"/"Instrumento" quedan en None; la Vista
    tabla de Escaneo (que no los necesita) sigue llamando esta función sin pasarlos."""
    rows: list[dict] = []
    for c in candidates:
        legs = json.loads(c["legs_json"]) if c["legs_json"] else []
        breakevens = json.loads(c["breakevens_json"]) if c["breakevens_json"] else []
        underlying_price = c["underlying_price"]
        if not legs or not underlying_price:
            continue
        leg = legs[0]
        strike = leg["strike"]
        option_type = leg["option_type"]
        breakeven = breakevens[0] if breakevens else None
        max_loss = c["max_loss"]
        net_premium = c["net_premium"]
        dte = c["dte"]
        sigma = leg.get("implied_volatility")

        prob_otm = None
        if risk_free_rate is not None and sigma is not None and dte is not None:
            prob_otm = round(probability_otm(option_type, underlying_price, strike, dte, risk_free_rate, sigma) * 100, 1)

        rows.append(
            {
                "Symbol": c["symbol"],
                "Instrumento": (instrument_types or {}).get(c["symbol"]),
                "Estrategia": strategy_label(c["strategy_type"]),
                "Price": underlying_price,
                "Exp Date": c["expiration_date"],
                "Strike": strike,
                "Moneyness (%)": round(_pct_distance(option_type, strike, underlying_price), 2),
                "Bid": leg.get("bid"),
                "Breakeven": breakeven,
                "%BE": round(_pct_distance(option_type, breakeven, underlying_price), 2) if breakeven is not None else None,
                "Volume": leg.get("volume"),
                "Open Interest": leg.get("open_interest"),
                "IV Rank": c["iv_rank"],
                "Delta": c["delta"],
                # Return = retorno del período (no anualizado) sobre el capital en riesgo — distinto
                # de "Rendimiento Anualizado" (annualized_return_pct), que ya proyecta a 365 días.
                "Return (%)": round(net_premium / max_loss * 100, 2) if net_premium is not None and max_loss else None,
                "Rendimiento Anualizado (%)": c["annualized_return_pct"],
                "POP (%)": round(c["probability_of_profit"] * 100, 1) if c["probability_of_profit"] is not None else None,
                "Probabilidad OTM (%)": prob_otm,
                "DTE": dte,
            }
        )
    return rows
