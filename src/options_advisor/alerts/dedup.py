from __future__ import annotations

from datetime import date


def build_dedup_key(symbol: str, strategy_type: str, expiration_date: date, strikes: dict, alert_date: date) -> str:
    """Misma estrategia/vencimiento/strikes el mismo día → mismo dedup_key, así el polling
    repetido cada 30 min no genera alertas duplicadas (Sección 6, Sección 6 del plan Fase 1).
    Si cambian los strikes o el score hace que se recalculen, el key cambia y cuenta como nueva."""
    strikes_part = "|".join(f"{k}={v}" for k, v in sorted(strikes.items()))
    return f"{symbol}|{strategy_type}|{expiration_date.isoformat()}|{strikes_part}|{alert_date.isoformat()}"
