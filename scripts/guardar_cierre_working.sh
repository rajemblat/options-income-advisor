#!/bin/bash
set -e
cd ~/options-income-advisor
rm -f .git/index.lock
echo "══════ TESTS ══════"
.venv/bin/python -m pytest -q
echo "✅ Tests OK."
echo "══════ GUARDANDO ══════"
git add src/options_advisor/execution/live_condor_engine.py \
        src/options_advisor/storage/db.py \
        src/options_advisor/storage/repository.py \
        tests/test_execution/test_cierre_condor_queda_puesto.py \
        scripts/guardar_cierre_working.sh

git commit -F - <<'MSG'
Condor real: la recompra queda PUESTA en vez de reponerse cada minuto

Usuario 2026-08-24, mirando su historial de ordenes: "envio el cierre en 1.25,
despues lo sigue enviando mas veces en 1.25 en vez de dejarlo working que lo tome
cuando lo vaya a tomar. Si entiendo que lo envie otra vez si modifica el precio
de cierre, pero el mismo precio no es necesario, lo deja en working".

Tenia razon por dos motivos. El obvio: reponer la misma orden al mismo precio es
ruido. El grave: ese ciclo cancelar/reponer abrio la carrera de ese mismo dia --
la orden llenO a $1.25 justo mientras salia el cancel, el robot la dio por no
llenada, y paso 40 minutos mandando recompras que Schwab rechazaba sobre una
posicion que ya no existia.

Ahora execute_condor_walk se llama con leave_resting_at_mid=True tambien para
cerrar, y la orden viva se guarda en la fila (close_working_order_id y
close_working_price, columnas nuevas). En cada tick _gestionar_cierre_puesto la
sondea ANTES de decidir nada:

  FILLED   -> se registra el cierre con ese precio y se limpia
  muerta   -> se limpia y se reintenta desde cero
  viva     -> se la deja trabajar; solo se REEMPLAZA si el precio objetivo se
              movio mas de $0.05. Menos que eso es ruido del mid.

Ante un error de red al sondear NO se cancela nada: una orden viva en el broker
es mas segura que una cancelada a ciegas. Es la leccion del 24/08.

Error propio corregido escribiendo los tests, y anotado en el codigo: la primera
version escribia el precio objetivo a mano ($1.25) cuando la cadena de prueba
daba $0.80. El test comprobaba mi aritmetica en vez del comportamiento. Ahora lo
calcula con las mismas funciones del motor.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AayqVJ6rqcpTMXv9V2aBYz
MSG

echo "══════ SUBIENDO ══════"
git push
echo "✅ LISTO."
git log --oneline -3
