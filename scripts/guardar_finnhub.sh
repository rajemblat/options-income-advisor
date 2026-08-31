#!/bin/bash
set -e
cd ~/options-income-advisor
rm -f .git/index.lock
echo "══════ TESTS ══════"
.venv/bin/python -m pytest -q
echo "✅ Tests OK."
echo "══════ GUARDANDO ══════"
git add src/options_advisor/market_context/finnhub_client.py \
        tests/test_market_context/test_finnhub_cache.py \
        scripts/guardar_finnhub.sh
git commit -F - <<'MSG'
Finnhub: cachear la fecha de earnings y frenar ante un 429

El robot escanea ~23 simbolos por minuto y para cada uno preguntaba la fecha de
earnings: unas 1.400 llamadas por hora contra un plan gratis que no las aguanta.
El 28/08 hubo 121 respuestas 429 en un dia, y cada una deja al analisis sin el
dato de earnings de ese simbolo.

La fecha de earnings de una empresa cambia cuatro veces al ano. Se cachea 12
horas: pasan a ser ~23 llamadas por dia. Se cachea tambien el None ("no hay
earnings a la vista"), que si no seguia preguntando cada minuto.

Y ante un 429 se deja de consultar por 15 minutos en vez de seguir golpeando:
insistir contra un limite de tasa solo alarga el bloqueo.

Un fallo de RED no se cachea, a proposito: es transitorio y hay que reintentar.
Solo se cachea una respuesta buena.

No afecta el trading -- los earnings son contexto para el analisis, no una
condicion de entrada.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AayqVJ6rqcpTMXv9V2aBYz
MSG
echo "══════ SUBIENDO ══════"
git push
echo "✅ LISTO."
git log --oneline -2
