"""Fechas de earnings HISTÓRICAS de un símbolo, combinando fuentes (usuario 2026-08-07: el modo
earnings del backtest necesita fechas de años atrás y el plan free de Finnhub no las da).

Orden: 1) Finnhub filtrado por símbolo; 2) Yahoo Finance (yfinance) como respaldo GRATIS con historial.
Nunca rompe el caller: [] si ninguna fuente responde. yfinance es opcional — si no está instalado,
simplemente se omite (instalar con: pip install yfinance)."""

from __future__ import annotations

import logging
from datetime import date

from options_advisor.market_context import finnhub_client

logger = logging.getLogger(__name__)


def _yahoo_earnings_dates_detail(symbol: str, start: date, end: date) -> tuple[list[date], str | None]:
    """Como `_yahoo_earnings_dates` pero devuelve (fechas, error). `error` es None si todo salió bien,
    o un texto corto explicando por qué Yahoo no aportó nada (yfinance no instalado, red bloqueada, sin
    datos, etc.) — para poder MOSTRARLE al usuario el motivo en vez de un silencio (usuario 2026-08-07:
    'hay 4 earnings al año, no puede ser 1')."""
    try:
        import yfinance as yf
    except Exception as e:
        logger.info("yfinance no está instalado; se omite Yahoo como fuente de earnings")
        return [], f"yfinance no está instalado ({e.__class__.__name__})"
    try:
        df = yf.Ticker(symbol).get_earnings_dates(limit=48)   # ~12 años de trimestres si Yahoo los tiene
        if df is None or getattr(df, "empty", True):
            return [], "Yahoo respondió sin fechas (posible límite de tasa; reintentá en unos minutos)"
        out: list[date] = []
        total = 0
        for ts in df.index:
            try:
                d = ts.date()
            except Exception:
                continue
            total += 1
            if start <= d <= end:
                out.append(d)
        result = sorted(set(out))
        if not result and total:
            return [], f"Yahoo trajo {total} fecha(s) pero NINGUNA cae en el rango {start}…{end}"
        return result, None
    except Exception as e:
        logger.warning("Yahoo/yfinance earnings no disponible para %s; se omite", symbol, exc_info=True)
        return [], f"Yahoo/yfinance falló: {e.__class__.__name__}: {e}"


def _yahoo_earnings_dates(symbol: str, start: date, end: date) -> list[date]:
    """Fechas de earnings de Yahoo Finance vía yfinance (gratis, sin API key). [] si yfinance no está
    instalado o falla la red."""
    return _yahoo_earnings_dates_detail(symbol, start, end)[0]


def historical_earnings_dates(symbol: str, start: date, end: date, finnhub_key: str | None) -> list[date]:
    """Fechas de earnings de `symbol` en [start, end], COMBINANDO Finnhub + Yahoo (unión). Finnhub free
    suele dar solo la PRÓXIMA (futura, no sirve para backtestear); Yahoo aporta las PASADAS. Juntando
    las dos, el backtest de earnings tiene con qué trabajar (usuario 2026-08-07)."""
    fh = set(finnhub_client.get_symbol_earnings_dates(symbol, start, end, finnhub_key))
    yh = set(_yahoo_earnings_dates(symbol, start, end))
    return sorted(fh | yh)


def historical_earnings_dates_detail(
    symbol: str, start: date, end: date, finnhub_key: str | None
) -> tuple[list[date], dict]:
    """Igual que `historical_earnings_dates` pero además devuelve un diccionario de diagnóstico:
    {"finnhub": n, "yahoo": n, "yahoo_error": str|None} — para que la UI le muestre al usuario
    exactamente cuántas fechas trajo cada fuente y por qué falló Yahoo si trajo cero
    (usuario 2026-08-07)."""
    fh = sorted(set(finnhub_client.get_symbol_earnings_dates(symbol, start, end, finnhub_key)))
    yh, yerr = _yahoo_earnings_dates_detail(symbol, start, end)
    dates = sorted(set(fh) | set(yh))
    diag = {"finnhub": len(fh), "yahoo": len(set(yh)), "yahoo_error": yerr}
    return dates, diag
