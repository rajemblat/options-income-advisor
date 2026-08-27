#!/bin/bash
# Tests + guardado de los arreglos del CIERRE del condor real (incidente del 24/08).
# El mensaje del commit va por stdin (git commit -F -) y NO como argumento: lleva comillas
# dobles adentro y como argumento rompia el comando (error real 2026-08-26).
set -e
cd ~/options-income-advisor
rm -f .git/index.lock

echo "════════════════════════════════════════════"
echo "  1/3  TESTS"
echo "════════════════════════════════════════════"
.venv/bin/python -m pytest -q
echo
echo "✅ Todos los tests pasaron."
echo

echo "════════════════════════════════════════════"
echo "  2/3  GUARDANDO"
echo "════════════════════════════════════════════"
git add src/options_advisor/execution/real_condor_sender.py \
        src/options_advisor/execution/live_condor_engine.py \
        tests/test_execution/test_cierre_condor_carrera.py \
        scripts/guardar_cierre_condor.sh

git commit -F - <<'MSG'
Condor real: el cierre que llena mientras sale el cancel ya no se pierde

El 24/08, con dinero real, el motor quiso cerrar un condor en ganancia. La orden
no llenó dentro de la ventana del walk y el sender la canceló -- pero en Schwab
ya habia llenado, a $1.25. El walk devolvio CANCELED, el motor dejo la posicion
como abierta, y durante 40 MINUTOS mando recompras que Schwab rechazo una tras
otra (oversold/overbought position): pedia recomprar algo que ya no tenia. El
usuario lo vio en su broker antes que el robot, y no recibio ningun aviso porque
el email solo salia cuando el cierre entraba.

Tres arreglos:

- Despues de cancelar se vuelve a sondear la orden. Si habia llenado, gana el
  FILL y se registra el cierre. Cancelar no es una respuesta: hay que preguntar.
- Se guarda el statusDescription del broker (CondorSendResult.status_detalle).
  Antes el log solo decia REJECTED y el motivo se tiraba a la basura.
- A los 3 rechazos seguidos sale un mail con el motivo y el P&L abierto. Uno
  solo por posicion; el contador se limpia cuando el cierre entra.

Queda pendiente y NO se toca aca: dejar la orden de cierre WORKING en vez de
cancelar y reenviarla al mismo precio cada tick (pedido del usuario 24/08).
Necesita guardar el id de la orden viva entre ticks -- otro commit.

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
