"""Login inicial (o re-login cada ~7 días cuando vence el refresh_token) contra la Schwab
Trader API. Flujo manual simplificado: abre la URL de autorización en tu navegador, iniciá
sesión en Schwab, y pegá acá la URL completa a la que te redirige al final (va a fallar
con "no se puede acceder a este sitio" porque no hay nada corriendo en el redirect_uri —
eso es esperado, lo único que importa es copiar la URL de la barra de direcciones).

Uso: python scripts/schwab_login.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from options_advisor.broker.schwab_auth import DEFAULT_TOKEN_STORE_PATH, SchwabAuth  # noqa: E402


def main() -> None:
    client_id = os.environ["SCHWAB_CLIENT_ID"]
    client_secret = os.environ["SCHWAB_CLIENT_SECRET"]
    redirect_uri = os.environ.get("SCHWAB_REDIRECT_URI", "https://127.0.0.1:8182/callback")

    auth = SchwabAuth(client_id, client_secret, redirect_uri, DEFAULT_TOKEN_STORE_PATH)

    print("1. Abrí esta URL en tu navegador e iniciá sesión en Schwab:\n")
    print(f"   {auth.authorization_url()}\n")
    print("2. Después de aprobar el acceso, el navegador va a intentar redirigirte y va a")
    print("   fallar (no hay servidor corriendo ahí) — está bien, copiá la URL completa de")
    print("   la barra de direcciones en ese momento.\n")

    redirect_url = input("3. Pegá acá esa URL completa: ").strip()
    code = SchwabAuth.extract_code_from_redirect_url(redirect_url)
    auth.exchange_code_for_tokens(code)
    print(f"\nListo. Tokens guardados en {DEFAULT_TOKEN_STORE_PATH}.")
    print("El access_token se refresca solo; el refresh_token dura ~7 días y vas a tener que")
    print("correr este script de nuevo cuando venza (el dashboard te va a avisar).")


if __name__ == "__main__":
    main()
