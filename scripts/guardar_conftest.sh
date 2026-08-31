#!/bin/bash
set -e
cd ~/options-income-advisor
rm -f .git/index.lock
echo "══════ TESTS ══════"
.venv/bin/python -m pytest -q
echo "✅ Tests OK."
echo "══════ GUARDANDO ══════"
git add tests/conftest.py scripts/guardar_conftest.sh
git commit -F - <<'MSG'
Aislar la cache de Finnhub entre tests

Quedo afuera del commit anterior (40063c5): el script listaba los archivos por
nombre y este no estaba. Sin esta fixture el suite falla, asi que el commit
anterior por si solo deja el repo en rojo.

Una cache a nivel de modulo es estado global. En produccion eso es lo que la
hace util -- un solo proceso guardando respuestas todo el dia -- y en los tests
es exactamente lo que hay que aislar: el resultado de un test pasa a depender de
cuales corrieron antes.

Paso de verdad al agregar la cache: test_get_next_earnings_date_returns_none_on
_http_error simulaba un fallo de red y esperaba None, pero recibio una fecha,
porque un test anterior ya habia cacheado AAPL. Ese test existia desde antes y
atrapo un efecto colateral que no estaba previsto.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AayqVJ6rqcpTMXv9V2aBYz
MSG
echo "══════ SUBIENDO ══════"
git push
echo "✅ LISTO."
git log --oneline -2
