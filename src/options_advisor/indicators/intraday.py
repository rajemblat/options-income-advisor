from __future__ import annotations

import pandas as pd

from options_advisor.broker.models import IntradayBar


def compute_vwap(bars: list[IntradayBar]) -> list[float | None]:
    """VWAP acumulado desde la primera barra de la lista (precio típico (H+L+C)/3, ponderado
    por volumen, acumulado) — una serie alineada 1:1 con `bars`, para dibujar como línea sobre
    el gráfico de velas. Llamar con las barras de UNA sola sesión por vez: el VWAP resetea cada
    sesión regular, no es una media móvil continua entre días."""
    if not bars:
        return []
    df = pd.DataFrame(
        {
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }
    )
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_volume = df["volume"].cumsum()
    cum_pv = (typical_price * df["volume"]).cumsum()
    vwap = cum_pv / cum_volume.replace(0, pd.NA)
    return [None if pd.isna(v) else round(float(v), 4) for v in vwap]
