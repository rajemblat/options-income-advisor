from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from options_advisor.broker import conectividad
from options_advisor.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
DEFAULT_TOKEN_STORE_PATH = PROJECT_ROOT / "data" / ".schwab_tokens.json"

# El access_token de Schwab expira a los 30 min; refrescamos un poco antes por margen de seguridad.
ACCESS_TOKEN_REFRESH_MARGIN_SECONDS = 120

# El refresh_token dura ~7 días contados DESDE EL LOGIN MANUAL (scripts/schwab_login.py). Refrescar
# el access_token cada 30 min NO reinicia ese reloj: Schwab devuelve el mismo refresh_token con su
# vencimiento original. Por eso `obtained_at` (que se pisa en cada refresh) no sirve para saber
# cuánto falta, y guardamos aparte `refresh_token_obtained_at`, sellado solo en el login.
REFRESH_TOKEN_LIFETIME_SECONDS = 7 * 24 * 3600

# Cuánto esperar antes de volver a intentar un refresh que falló por RED (no por token vencido).
# Por qué existe (viernes 2026-08-21, encontrado el 23/08): la Mac se quedó sin DNS y CADA llamada
# a la API entraba a la rama de refresh, escribía "refrescando..." en el log, intentaba un POST que
# reventaba, y volcaba un stack trace de 90 líneas. Resultado: 31.000 líneas de "refrescando..." en
# un día para ~15 refrescos de verdad, y el log del scheduler creció a 560 MB. Como `_refresh()`
# revienta antes de guardar nada, `obtained_at` nunca se actualiza y la condición de vencimiento
# queda pegada en True para siempre: el robot se martillaba a sí mismo.
REFRESH_FAIL_BACKOFF_SECONDS = 30.0


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
        # (instante del fallo, texto) del último refresh que falló por red. Ver REFRESH_FAIL_BACKOFF_SECONDS.
        self._ultimo_fallo_de_refresh: tuple[float, str] | None = None

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
            timeout=15.0,  # nunca colgar el arranque esperando a Schwab (blindaje 2026-08-05)
        )
        response.raise_for_status()
        # from_login=True: ESTE es el único momento en que arranca el reloj de los 7 días.
        self._store_tokens(response.json(), from_login=True)

    def _refresh(self) -> None:
        tokens = self._load_tokens()
        try:
            self._refresh_http(tokens)
        except httpx.TransportError as exc:
            # No llegamos ni a Schwab (DNS/Wi-Fi/TLS). NO es el token: no lo tratamos como vencido,
            # anotamos el fallo para frenar el martilleo y avisamos al vigilante de conexión.
            self._ultimo_fallo_de_refresh = (time.time(), str(exc))
            conectividad.registrar_fallo_de_red(exc)
            raise
        self._ultimo_fallo_de_refresh = None
        conectividad.registrar_exito()

    def _refresh_http(self, tokens: dict) -> None:
        response = httpx.post(
            TOKEN_URL,
            headers={**self._basic_auth_header(), "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
            timeout=15.0,  # nunca colgar por un refresh de token lento (blindaje 2026-08-05)
        )
        # El refresh_token de Schwab dura ~7 días; cuando vence, Schwab responde 400/401 al refrescar
        # (usuario 2026-08-10, punto 1). Antes eso tiraba un httpx.HTTPStatusError crudo que reventaba
        # el dashboard en rojo. Ahora lo convertimos en SchwabAuthError con instrucciones claras para
        # que la UI muestre "Reconectá Schwab" en vez de un stacktrace.
        if response.status_code in (400, 401):
            raise SchwabAuthError(
                "El token de Schwab venció (dura ~7 días). Reconectá corriendo: "
                "python scripts/schwab_login.py — después reiniciá el robot y refrescá el dashboard."
            )
        response.raise_for_status()
        self._store_tokens(response.json())

    def _store_tokens(self, token_response: dict, *, from_login: bool = False) -> None:
        now = time.time()
        token_response["obtained_at"] = now
        # Reloj de los 7 días (usuario 2026-08-18, tras perder media rueda con el token vencido):
        # se sella SOLO en el login manual. Un refresh arrastra el sello viejo, así el contador no
        # se reinicia solo cada 30 min y podemos avisar 24 h antes de que caduque de verdad.
        if from_login:
            token_response["refresh_token_obtained_at"] = now
        else:
            previous = self._stored_refresh_token_obtained_at()
            # Sin sello previo (primer arranque tras este cambio, o archivo de una versión vieja)
            # asumimos "recién emitido". El error es como mucho de unos minutos y siempre hacia el
            # lado seguro: preferimos avisar de más y no de menos.
            token_response["refresh_token_obtained_at"] = previous if previous is not None else now
        self.token_store_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_store_path.write_text(json.dumps(token_response))
        self._tokens = token_response

    def _stored_refresh_token_obtained_at(self) -> float | None:
        """Sello de emisión que ya está guardado. None si todavía no hay ninguno.

        Mira PRIMERO el disco y solo cae a memoria si el archivo no se puede leer. El orden importa:
        el archivo es lo que comparten el robot, el dashboard y `scripts/schwab_login.py`, así que si
        difiere de la memoria es porque alguien MÁS lo escribió — típicamente vos reconectándote
        desde otra terminal. Ese dato es más nuevo que el que este proceso tiene cacheado, y leer la
        memoria primero hacía que el refresh siguiente lo pisara con el sello viejo.

        Nunca lanza: se usa dentro de `_store_tokens`, en el camino crítico del trading."""
        try:
            value = json.loads(self.token_store_path.read_text()).get("refresh_token_obtained_at")
            if isinstance(value, (int, float)):
                return float(value)
        except Exception:
            pass
        value = (self._tokens or {}).get("refresh_token_obtained_at")
        return float(value) if isinstance(value, (int, float)) else None

    def refresh_token_seconds_left(self) -> float | None:
        """Segundos hasta que caduque el refresh_token de este store. Lee del DISCO a propósito
        (ver `read_refresh_token_seconds_left`)."""
        return read_refresh_token_seconds_left(self.token_store_path)

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
            self._frenar_si_el_refresh_viene_fallando()
            logger.info("access_token de Schwab vencido o por vencer, refrescando...")
            self._refresh()
            tokens = self._tokens
        return tokens["access_token"]

    def _frenar_si_el_refresh_viene_fallando(self) -> None:
        """Si el refresh acaba de fallar por red, corta de una en vez de reintentar el POST.

        Sin esto, durante un apagón de red cada una de las ~100 llamadas por escaneo repetía el
        intento y su stack trace. El mensaje dice explícitamente que NO es el token vencido, para
        que nadie salga a re-loguearse a mano por un problema de Wi-Fi."""
        if self._ultimo_fallo_de_refresh is None:
            return
        cuando, detalle = self._ultimo_fallo_de_refresh
        transcurrido = time.time() - cuando
        if transcurrido >= REFRESH_FAIL_BACKOFF_SECONDS:
            return  # ya pasó el enfriamiento: se vuelve a intentar de verdad
        raise SchwabAuthError(
            f"Sin conexión con Schwab: el refresh del token falló hace {transcurrido:.0f}s ({detalle}). "
            "NO es el token vencido — es la red de la máquina. Se reintenta solo en cuanto vuelva."
        )

    def is_authenticated(self) -> bool:
        try:
            self.get_valid_access_token()
            return True
        except Exception:
            return False


def read_refresh_token_seconds_left(token_store_path: Path = DEFAULT_TOKEN_STORE_PATH) -> float | None:
    """Segundos que faltan para que caduque el refresh_token (el que obliga a re-loguearse a mano).
    None si no hay tokens guardados o el archivo está ilegible; negativo si ya venció.

    Lee SIEMPRE del disco, nunca de `SchwabAuth._tokens`. Es a propósito: el robot cachea los tokens
    en memoria al arrancar, así que un proceso viejo seguiría creyendo que faltan horas después de
    que vos te reconectaste desde otra terminal (fue exactamente lo que pasó el 18/08). El archivo es
    la única fuente de verdad compartida entre el robot, el dashboard y el script de login.
    Nunca lanza."""
    try:
        tokens = json.loads(Path(token_store_path).read_text())
    except Exception:
        return None
    issued_at = tokens.get("refresh_token_obtained_at") or tokens.get("obtained_at")
    if not isinstance(issued_at, (int, float)):
        return None
    return float(issued_at) + REFRESH_TOKEN_LIFETIME_SECONDS - time.time()
