from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pytest  # noqa: E402

from options_advisor.broker.models import PriceBar  # noqa: E402


def make_price_bars(closes: list[float], start: date = date(2026, 1, 1)) -> list[PriceBar]:
    bars = []
    for i, close in enumerate(closes):
        bars.append(
            PriceBar(
                symbol="TEST",
                trade_date=start + timedelta(days=i),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1_000_000,
            )
        )
    return bars


@pytest.fixture
def price_bars_factory():
    return make_price_bars


def write_mock_fixtures(fixtures_dir: Path, symbol: str = "TST") -> None:
    """Fixture chica y determinista (sin aleatoriedad) para tests de integración:
    60 sesiones de precio con leve tendencia alcista e IV que termina alta (IV Rank alto
    al final de la serie), para forzar de forma predecible la rama de venta de prima."""
    import csv
    import json

    (fixtures_dir / "price_history").mkdir(parents=True, exist_ok=True)
    (fixtures_dir / "iv_history").mkdir(parents=True, exist_ok=True)
    (fixtures_dir / "option_chains").mkdir(parents=True, exist_ok=True)

    start = date(2026, 1, 1)
    n_days = 60
    with open(fixtures_dir / "price_history" / f"{symbol}.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        price = 100.0
        for i in range(n_days):
            price = 100.0 + i * 0.1
            writer.writerow(
                {
                    "date": (start + timedelta(days=i)).isoformat(),
                    "open": round(price, 2),
                    "high": round(price * 1.01, 2),
                    "low": round(price * 0.99, 2),
                    "close": round(price, 2),
                    "volume": 1_000_000,
                }
            )

    with open(fixtures_dir / "iv_history" / f"{symbol}.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "iv_atm"])
        writer.writeheader()
        for i in range(n_days):
            iv = 0.15 + 0.30 * (i / (n_days - 1))  # sube de 0.15 a 0.45: termina en el máximo -> IV Rank ~100
            writer.writerow({"date": (start + timedelta(days=i)).isoformat(), "iv_atm": round(iv, 4)})

    template = {
        "symbol": symbol,
        "bid_ask_spread_pct": 0.02,
        "contracts": [
            {"dte": dte, "strike_offset_pct": offset, "iv_skew": round(-offset * 0.1, 4), "option_type": option_type}
            for dte in (14, 30, 45, 60)
            for offset in (-0.10, -0.05, -0.025, 0.0, 0.025, 0.05, 0.10)
            for option_type in ("call", "put")
        ],
    }
    with open(fixtures_dir / "option_chains" / f"{symbol}.json", "w") as f:
        json.dump(template, f)


@pytest.fixture
def mock_fixtures_dir(tmp_path):
    write_mock_fixtures(tmp_path, symbol="TST")
    return tmp_path


# ---------------------------------------------------------------------------
# Ningún test manda avisos de verdad.
#
# El 23/08 el usuario recibió emails de cierre con datos inventados ("C put 125 a $1.50",
# "AAL put 11 a $1.55", un Iron Condor de $200 cerrado a mano) mezclados con los avisos reales de
# su robot. No los mandó el robot: los mandó `pytest`. La cadena era esta:
#
#   1. `dashboard/components.py` hace `load_dotenv(.env)` al importarse.
#   2. Basta que pytest recoja un test de tests/test_dashboard/ para que ese import corra.
#   3. A partir de ahí SMTP_HOST/USER/PASSWORD/EMAIL_TO reales viven en os.environ de TODO el
#      proceso de pytest.
#   4. tests/test_execution/test_live_engine.py llama a `close_real_positions()` de verdad, que
#      llama a `notifier.send_email()` de verdad → sale un correo real con datos de fixture.
#
# Este fixture corta el paso 3: cada test arranca sin credenciales de ningún canal. Los tests que
# necesitan credenciales (tests/test_alerts/test_notifier.py) las ponen ellos con
# monkeypatch.setenv dentro del test, que corre DESPUÉS de este fixture y por lo tanto gana.
_VARIABLES_DE_AVISO = (
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO", "EMAIL_FROM",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
)


@pytest.fixture(autouse=True)
def _sin_avisos_reales(monkeypatch):
    """Corre en TODOS los tests (autouse). Borra las credenciales de correo y Telegram del entorno
    para que ningún test pueda mandarle nada a la casilla del usuario, aunque el .env se haya
    colado por un import."""
    for nombre in _VARIABLES_DE_AVISO:
        monkeypatch.delenv(nombre, raising=False)
