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
git add src/options_advisor/execution/live_condor_engine.py \
        tests/test_execution/test_apertura_condor_rechazada.py \
        scripts/adoptar_condor_28ago.py \
        scripts/guardar_apertura_condor.sh

git commit -F - <<'MSG'
Condor real: una apertura no se descarta sin preguntarle a Schwab si llenO

El 28/08, con dinero real. El robot dejo la orden de apertura puesta a las
09:38:58, leyo REJECTED 34 segundos despues y tiro la fila como
apertura_no_lleno. La orden lleno igual: 09:41:06, credito $1.95. Como la fila
quedo en 'closed', dejo de aparecer en la lista que el motor recorre cada minuto
(status IN 'working','open') y la posicion quedo VIVA dos horas -- sin stop de
$100, sin objetivo de ganancia, sin nadie mirandola -- hasta que el usuario la
vio en su broker y la cerro a mano. $807.50 de riesgo maximo que el robot creia
que no existia.

Contexto del dia: 2845 errores de conexion entre las 09 y las 11 y el detector
de operaciones reales caido hasta las 11:03. La lectura de estado bien pudo ser
basura; ademas, al reemplazar una orden el id viejo puede quedar muerto mientras
el reemplazo sigue vivo.

Es el reflejo del bug del 24/08: alli creyo que una posicion seguia abierta
cuando ya estaba cerrada. Misma causa de fondo -- decidir el estado de una orden
con UNA sola lectura y no volver a preguntar.

Arreglo: antes de descartar una apertura REJECTED/CANCELED/EXPIRED se llama a
_buscar_orden_en_schwab (la MISMA verificacion que ya se usaba para las filas en
'sending'; lo que faltaba era usarla tambien aca). Si aparece una orden LLENADA
con las 4 patas exactas, se ADOPTA con su credito real y queda bajo gestion, con
mail avisando. Si de verdad no lleno, se descarta como antes pero ahora sale un
mail con el statusDescription del broker: el 28/08 el usuario no se entero de
nada.

3 tests nuevos, uno reproduce el incidente.

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
