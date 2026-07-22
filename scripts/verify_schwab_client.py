"""Script manual (no automatizado en CI, requiere credenciales reales) para verificar
SchwabBrokerClient contra la API en vivo apenas lleguen las credenciales — confirma si
Schwab expone griegos en la cadena de opciones o si el fallback (indicators/greeks.py)
tiene que activarse, y si el mapeo de campos en schwab_client.py es correcto.

Requiere haber corrido scripts/schwab_login.py antes.

Uso: python scripts/verify_schwab_client.py AAPL SPY
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from options_advisor.broker.schwab_client import SchwabBrokerClient  # noqa: E402


def main() -> None:
    symbols = sys.argv[1:] or ["AAPL", "SPY"]
    client = SchwabBrokerClient.from_env()

    print(f"Autenticado: {client.is_authenticated()}\n")

    for symbol in symbols:
        print(f"=== {symbol} ===")
        quote = client.get_quote(symbol)
        print(f"Quote: {quote}")

        history = client.get_price_history(symbol, lookback_days=5)
        print(f"Price history (últimas {len(history)} barras): {[(b.trade_date, b.close) for b in history]}")

        chain = client.get_option_chain(symbol, expiration_range_days=(20, 45))
        print(f"Cadena de opciones: {len(chain.contracts)} contratos")
        if chain.contracts:
            sample = chain.contracts[0]
            print(f"Contrato de ejemplo: {sample}")
            print(f"greeks_source detectado: {sample.greeks.source}")
        print()


if __name__ == "__main__":
    main()
