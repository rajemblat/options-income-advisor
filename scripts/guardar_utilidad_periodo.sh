#!/bin/bash
set -e
cd ~/options-income-advisor
rm -f .git/index.lock
echo "══════ TESTS ══════"
.venv/bin/python -m pytest -q
echo "✅ Tests OK."
echo "══════ GUARDANDO ══════"
git add src/options_advisor/execution/live_condor_engine.py \
        tests/test_execution/test_apertura_condor_rechazada.py \
        scripts/adoptar_condor_28ago.py \
        scripts/guardar_apertura_condor.sh \
        scripts/guardar_utilidad_periodo.sh \
        src/options_advisor/dashboard/components.py \
        src/options_advisor/dashboard/pages/12_simulador.py \
        src/options_advisor/dashboard/pages/14_real_market.py

git commit -F - <<'MSG'
Condor real: una apertura no se descarta sin preguntarle a Schwab si llenO

El 28/08, con dinero real. El robot dejo la orden de apertura puesta a las
09:38:58, leyo REJECTED 34 segundos despues y tiro la fila como
apertura_no_lleno. La orden lleno igual: 09:41:06, credito $1.95. Como la fila
quedo en 'closed' dejo de aparecer en la lista que el motor recorre cada minuto
(status IN 'working','open') y la posicion quedo VIVA dos horas -- sin stop de
$100, sin objetivo, sin nadie mirandola -- hasta que el usuario la vio en su
broker y la cerro a mano. $807.50 de riesgo que el robot creia inexistente.

Se verifico que la Mac NO se durmio: no hubo un solo minuto sin lineas de log
entre 09:30 y 11:40. La causa fue la logica, no el equipo.

Arreglo: antes de descartar una apertura REJECTED/CANCELED/EXPIRED se llama a
_buscar_orden_en_schwab (la misma verificacion que ya se usaba para las filas en
'sending'). Si aparece una orden LLENADA con las 4 patas exactas se ADOPTA con
su credito real y queda bajo gestion, avisando por mail. Si de verdad no lleno,
se descarta como antes pero sale un mail con el motivo del broker.

Ademas, en el dashboard: la utilidad acumulada va en verde/rojo y dice cuanto
tiempo abarca (usuario: "cuando pongo utilidad en todo quiero que salga en
numeros verde y que diga cuantos meses va esa utilidad"). Nuevo helper
components.utilidad_con_periodo, aplicado en naked del simulador, iron condor
del simulador y del real. El naked real ya lo tenia desde el 13/08.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AayqVJ6rqcpTMXv9V2aBYz
MSG

echo "══════ SUBIENDO ══════"
git push
echo "✅ LISTO."
git log --oneline -3
