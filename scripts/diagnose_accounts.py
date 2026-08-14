"""Diagnóstico de cuentas Schwab vinculadas (usuario 2026-08-10: una orden real de 1 contrato la rebotó
Schwab por 'buying power' teniendo $180k → sospecha de que el robot le pegó a la cuenta equivocada).

Lista TODAS las cuentas que ve el login, su tipo (CASH/MARGIN), su option buying power y cuál elige el
robot hoy (la primera, porque account_number está vacío). Con esto sabemos a qué cuenta apuntar.

Correr desde la raíz del proyecto, en la misma terminal donde corre el robot (necesita las mismas
variables de entorno SCHWAB_*):  python scripts/diagnose_accounts.py
No manda ninguna orden — solo lee saldos."""

from __future__ import annotations

from options_advisor.broker import get_broker_client
from options_advisor.config import load_settings


def main() -> None:
    settings = load_settings()
    broker = get_broker_client(settings)
    if not hasattr(broker, "list_account_hashes"):
        print("El broker no es Schwab (mode:", settings.broker.mode, ") — nada que diagnosticar.")
        return

    accounts = broker.list_account_hashes()
    print(f"Cuentas vinculadas a este login: {len(accounts)}\n")

    picked_hash = broker.resolve_account_hash(settings.live_trading.account_number or None)

    for i, acc in enumerate(accounts):
        num = str(acc.get("accountNumber"))
        h = acc.get("hashValue")
        try:
            resp = broker._trader_client.get(
                f"/accounts/{h}", params={"fields": "positions"}, headers=broker._trader_headers()
            )
            resp.raise_for_status()
            sa = resp.json().get("securitiesAccount", {})
            typ = sa.get("type", "?")
            bal = sa.get("currentBalances", {}) or {}
            opt_bp = bal.get("optionBuyingPower")
            bp = bal.get("buyingPower")
            cash = bal.get("cashAvailableForTrading", bal.get("cashBalance"))
            n_pos = len(sa.get("positions", []) or [])
        except Exception as e:  # noqa: BLE001
            typ, opt_bp, bp, cash, n_pos = f"ERROR: {e}", None, None, None, "?"

        marca = "  <<< ES LA QUE USA EL ROBOT HOY" if h == picked_hash else ""
        print(f"[{i}] cuenta {num}  tipo={typ}{marca}")
        print(f"      option_buying_power = {opt_bp}")
        print(f"      buying_power        = {bp}")
        print(f"      cash_available      = {cash}")
        print(f"      posiciones abiertas = {n_pos}\n")

    print("-> Para operar una cuenta específica, poné su número en config/settings.yaml:")
    print('   live_trading.account_number: "EL_NUMERO_DE_LA_CUENTA_CON_180K"')


if __name__ == "__main__":
    main()
