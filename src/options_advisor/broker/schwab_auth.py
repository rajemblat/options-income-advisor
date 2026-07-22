from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from options_advisor.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
DEFAULT_TOKEN_STORE_PATH = PROJECT_ROOT / "data" / ".schwab_tokens.json"

# El access_token de Schwab expira a los 30 min; refrescamos un poco antes por margen de seguridad.
ACCESS_TOKEN_REFRESH_MARGIN_SECONDS = 120


class SchwabAuthError(RuntimeError):
    pass


class SchwabAuth:
    """OAuth2 Authorization Code para la Schwab Trader API. El refresh_token dura ~7 días y
    requiere volver a loguearse manualmente en el navegador (scripts/schwab_login.py) — es una
    particularidad conocida de esta API, documentada como riesgo en el plan de Fase 1."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, token_store_path: Path):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_store_path = token_store_path
        self._tokens: dict | None = None

    def authorization_url(self) -> str:
        params = {"client_id": self.client_id, "redirect_uri": self.redirect_uri}
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    @staticmethod
    def extract_code_from_redirect_url(redirect_url: str) -> str:
        query = parse_qs(urlparse(redirect_url).query)
        codes = query.get("code")
        if not codes:
            raise SchwabAuthError(f"No se encontró 'code' en la URL pegada: {redirect_url}")
        return codes[0]

    def _basic_auth_header(self) -> dict:
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}

    def exchange_code_for_tokens(self, authorization_code: str) -> None:
        response = httpx.post(
            TOKEN_URL,
            headers={**self._basic_auth_header(), "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "authorization_code", "code": authorization_code, "redirect_uri": self.redirect_uri},
        )
        response.raise_for_status()
        self._store_tokens(response.json())

    def _refresh(self) -> None:
        tokens = self._load_tokens()
        response = httpx.post(
            TOKEN_URL,
            headers={**self._basic_auth_header(), "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
        )
        response.raise_for_status()
        self._store_tokens(response.json())

    def _store_tokens(self, token_response: dict) -> None:
        token_response["obtained_at"] = time.time()
        self.token_store_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_store_path.write_text(json.dumps(token_response))
        self._tokens = token_response

    def _load_tokens(self) -> dict:
        if self._tokens is not None:
            return self._tokens
        if not self.token_store_path.exists():
            raise SchwabAuthError(
                "No hay tokens guardados. Corré scripts/schwab_login.py para autenticarte por primera vez."
            )
        self._tokens = json.loads(self.token_store_path.read_text())
        return self._tokens

    def get_valid_access_token(self) -> str:
        tokens = self._load_tokens()
        expires_at = tokens["obtained_at"] + tokens["expires_in"] - ACCESS_TOKEN_REFRESH_MARGIN_SECONDS
        if time.time() >= expires_at:
            logger.info("access_token de Schwab vencido o por vencer, refrescando...")
            self._refresh()
            tokens = self._tokens
        return tokens["access_token"]

    def is_authenticated(self) -> bool:
        try:
            self.get_valid_access_token()
            return True
        except Exception:
            return False
