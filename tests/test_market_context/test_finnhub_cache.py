"""Finnhub: cachear la fecha de earnings y frenar ante un 429.

El robot escanea ~23 simbolos por minuto y para cada uno preguntaba la fecha de earnings: unas
1.400 llamadas por hora contra un plan gratis que no las aguanta. El 28/08 hubo 121 respuestas 429
en un dia, y cada una deja al analisis sin el dato de earnings de ese simbolo.

La fecha de earnings de una empresa cambia cuatro veces al ano. Preguntarla cada minuto no aporta
nada.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from options_advisor.market_context import finnhub_client as fc

HOY = date(2026, 8, 31)


# La limpieza de la cache entre tests la hace una fixture autouse en tests/conftest.py, para que
# valga en TODOS los tests y no solo en este archivo.


class _Respuesta:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"earningsCalendar": [{"date": "2026-10-28"}]}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


def test_no_vuelve_a_preguntar_lo_mismo(monkeypatch):
    """EL arreglo: 23 simbolos x 390 minutos de rueda pasan a ser 23 llamadas por dia."""
    llamadas = {"n": 0}

    def falsa(*a, **k):
        llamadas["n"] += 1
        return _Respuesta()

    monkeypatch.setattr(fc.httpx, "get", falsa)

    for _ in range(50):
        assert fc.get_next_earnings_date("AAPL", HOY, "KEY") == date(2026, 10, 28)
    assert llamadas["n"] == 1, f"Pregunto {llamadas['n']} veces lo mismo"


def test_cachea_tambien_el_no_hay_earnings(monkeypatch):
    """"Esta empresa no tiene earnings a la vista" es una respuesta tan valida como una fecha:
    si no se cachea, esos simbolos siguen preguntando cada minuto."""
    llamadas = {"n": 0}

    def falsa(*a, **k):
        llamadas["n"] += 1
        return _Respuesta(payload={"earningsCalendar": []})

    monkeypatch.setattr(fc.httpx, "get", falsa)

    for _ in range(10):
        assert fc.get_next_earnings_date("XYZ", HOY, "KEY") is None
    assert llamadas["n"] == 1


def test_cada_simbolo_tiene_su_propia_entrada(monkeypatch):
    llamadas = {"n": 0}

    def falsa(*a, **k):
        llamadas["n"] += 1
        return _Respuesta()

    monkeypatch.setattr(fc.httpx, "get", falsa)

    fc.get_next_earnings_date("AAPL", HOY, "KEY")
    fc.get_next_earnings_date("MSFT", HOY, "KEY")
    fc.get_next_earnings_date("AAPL", HOY, "KEY")
    assert llamadas["n"] == 2, "Mezclo simbolos distintos en la misma entrada"


def test_un_429_frena_las_consultas_siguientes(monkeypatch):
    """Insistir contra un limite de tasa solo alarga el bloqueo."""
    llamadas = {"n": 0}

    def falsa(*a, **k):
        llamadas["n"] += 1
        return _Respuesta(status_code=429)

    monkeypatch.setattr(fc.httpx, "get", falsa)

    assert fc.get_next_earnings_date("AAPL", HOY, "KEY") is None
    for s in ["MSFT", "NVDA", "AMZN", "GOOGL"]:
        assert fc.get_next_earnings_date(s, HOY, "KEY") is None
    assert llamadas["n"] == 1, f"Siguio golpeando despues del 429 ({llamadas['n']} llamadas)"


def test_sin_api_key_no_llama_a_nadie(monkeypatch):
    def explota(*a, **k):
        raise AssertionError("no deberia llamar sin api key")

    monkeypatch.setattr(fc.httpx, "get", explota)
    assert fc.get_next_earnings_date("AAPL", HOY, None) is None


def test_un_error_de_red_no_rompe_ni_envenena_la_cache(monkeypatch):
    """Si falla por red devolvemos None pero NO lo cacheamos: al rato hay que volver a intentar."""
    estado = {"falla": True, "n": 0}

    def falsa(*a, **k):
        estado["n"] += 1
        if estado["falla"]:
            raise httpx.ConnectError("sin red")
        return _Respuesta()

    monkeypatch.setattr(fc.httpx, "get", falsa)

    assert fc.get_next_earnings_date("AAPL", HOY, "KEY") is None
    estado["falla"] = False
    assert fc.get_next_earnings_date("AAPL", HOY, "KEY") == date(2026, 10, 28)
    assert estado["n"] == 2, "Cacheo un fallo de red como si fuera una respuesta"
