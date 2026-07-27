from __future__ import annotations

from options_advisor.alerts.engine import process_symbol_alerts
from options_advisor.config import load_settings
from options_advisor.storage import db


class _FakeAnalysis:
    """Solo lo que process_symbol_alerts toca ANTES del corte por block_new_candidates=True —
    no hace falta un IndicatorSnapshot/OptionChain reales para probar el bloqueo temprano."""

    def __init__(self, symbol: str):
        self.snapshot = type("S", (), {"symbol": symbol})()


def test_process_symbol_alerts_returns_empty_when_blocked():
    conn = db.connect(":memory:")
    result = process_symbol_alerts(conn, _FakeAnalysis("AAPL"), load_settings(), block_new_candidates=True)
    assert result == []


def test_process_symbol_alerts_block_flag_defaults_to_false():
    """Sección Fed/FRED (pedido 2026-07-26): sin pasar el flag explícito, el comportamiento no
    cambia — sigue de largo hasta snap.iv_rank (None acá, corta ahí con lista vacía por una
    razón DISTINTA a block_new_candidates, confirmando que el default no bloquea de entrada)."""
    conn = db.connect(":memory:")

    class _AnalysisWithSnapshot:
        def __init__(self):
            self.snapshot = type("S", (), {"symbol": "AAPL", "iv_rank": None})()

    result = process_symbol_alerts(conn, _AnalysisWithSnapshot(), load_settings())
    assert result == []  # corta por iv_rank None, no por bloqueo — confirma que no bloquea por default
