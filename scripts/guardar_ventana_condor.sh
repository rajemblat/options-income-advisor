#!/bin/bash
set -e
cd ~/options-income-advisor
rm -f .git/index.lock

echo "════════════════════════════════════════════"
echo "  1/3  TESTS"
echo "════════════════════════════════════════════"
.venv/bin/python -m pytest -q
echo
echo "✅ Tests OK."

echo "════════════════════════════════════════════"
echo "  2/3  GUARDANDO"
echo "════════════════════════════════════════════"
git add src/options_advisor/simulator/iron_condor.py \
        src/options_advisor/config.py \
        config/settings.yaml \
        tests/test_simulator/test_iron_condor.py \
        BACKLOG.md \
        scripts/guardar_ventana_condor.sh

git commit -F - <<'MSG'
Condor: la ventana de entrada pasa a hora de Nueva York, 09:30 a 14:00

Pedido del usuario (2026-08-27): "no quiero que solo opere la primera ventana,
quiero que opere cuando vea oportunidad, maximo hasta las 2 pm horario de
mercado, pero si ve que hay volatilidad y se dan las condiciones que opere".

evaluate_condor_signal comparaba la hora de la ultima barra TAL CUAL viene de
Schwab, que es UTC. Con entry_window 10:00-14:00 eso valia 06:00-10:00 ET y,
como el mercado abre 09:30, la ventana efectiva eran los primeros 30 minutos de
la rueda. El robot no podia abrir despues de las 10:00 ET aunque el dia siguiera
calmo -- hoy mismo perdio el condor porque un corte de DNS lo dejo sin barras
del SPX justo en esos 30 minutos.

Ahora la hora se convierte a America/New_York antes de comparar y la ventana es
09:30-14:00 hora de mercado. Una barra sin huso se toma como hora de mercado
(fixtures y tests).

Efecto colateral buscado: desactiva la bomba del 2026-11-02. Al salir EEUU del
horario de verano el mercado pasaba a abrir 14:30 UTC, despues de la vieja
ventana, y el condor habria dejado de abrir PARA SIEMPRE en silencio. Hay un
test que lo fija con fechas de noviembre.

El riesgo REAL no cambia: sigue 1 condor real por dia, stop de $100, y un solo
stop-loss frena el resto del dia. Lo que se abre mas es el papel, que no tiene
tope diario -- ahi vamos a ver si operar fuera de la primera media hora sirve.

OJO al leer el historial: los 16 condors ganadores del papel de agosto se
abrieron todos entre 09:31 y 09:54 ET porque era el unico horario en que PODIA
abrir. No es evidencia de que ese horario sea el mejor.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AayqVJ6rqcpTMXv9V2aBYz
MSG

echo
echo "════════════════════════════════════════════"
echo "  3/3  SUBIENDO"
echo "════════════════════════════════════════════"
git push
echo
echo "✅ LISTO."
git log --oneline -3
