#!/bin/bash
# Corre TODOS los tests y, solo si pasan, guarda el trabajo del 24 al 26 de agosto en GitHub.
# Si un test falla, NO commitea nada.
set -e
cd ~/options-income-advisor

echo "════════════════════════════════════════════"
echo "  1/3  CORRIENDO LOS TESTS (puede tardar)"
echo "════════════════════════════════════════════"
.venv/bin/python -m pytest -q
echo
echo "✅ Todos los tests pasaron."
echo

echo "════════════════════════════════════════════"
echo "  2/3  GUARDANDO"
echo "════════════════════════════════════════════"

git add src/options_advisor/broker/schwab_auth.py tests/test_broker/test_token_recarga.py
git commit -m "El token de Schwab se relee del disco cuando lo renovas

SchwabAuth cacheaba los tokens en memoria en la primera lectura y no volvia a
mirar el archivo nunca mas. El 24/08 el usuario corrio schwab_login.py con el
mercado abierto; Schwab anula el refresh_token viejo al emitir uno nuevo, y el
robot -que seguia con el viejo en memoria- quedo 22 MINUTOS ciego tirando ~100
errores por minuto, hasta reiniciarlo a mano. Al servidor le paso lo mismo.

Ahora _load_tokens compara el mtime del archivo y relee si cambio. Si el stat
o el parseo fallan, se queda con lo que tenia: esto corre en el camino critico
de mandar ordenes y no puede reventar por un archivo a medio escribir.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AayqVJ6rqcpTMXv9V2aBYz"

git add config/settings.yaml
git commit -m "Condor: un solo stop-loss frena las aperturas del dia

stop_loss_streak_halt de 2 a 1 (usuario 2026-08-26: 'si pierde uno por stop
loss no vuelva a abrir ese dia'). El condor REAL y el de papel llevan
contadores separados. El butterfly queda en 2, sin cambios.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AayqVJ6rqcpTMXv9V2aBYz"

git add src/options_advisor/dashboard/pages/14_real_market.py \
        src/options_advisor/dashboard/pages/12_simulador.py \
        src/options_advisor/simulator/iron_condor.py
git commit -m "Dashboard: tabla de cerrados en el iron real y etiquetas que dicen la verdad

- Iron Condors reales: tabla 'Cerrados' con filtro por periodo, igual a la del
  Simulador (usuario 2026-08-24). El total NO suma las filas sin P&L conocido.
- La columna Motivo se arma desde la config viva. Decia 'objetivo 60%' fijo,
  un numero de principios de agosto: el objetivo real es 35% (20% temprano).
- Mismo arreglo en el docstring de should_close_condor, que decia 40/50%/20min.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AayqVJ6rqcpTMXv9V2aBYz"

git add scripts/huella_del_dia.py scripts/parar_condor_fantasma.py \
        scripts/reanudar_todo.py scripts/ver_lentitud.sh scripts/guardar_26ago.sh
git commit -m "Herramientas de operacion del dia a dia

- huella_del_dia.py: resumen comparable Mac vs servidor, para validar la mudanza
- parar_condor_fantasma.py: corta el martilleo cuando un condor real cerro en el
  broker y el robot no se entero (24/08), y completa el P&L cuando aparece
- reanudar_todo.py: saca el freno maestro desde la terminal
- ver_lentitud.sh: diagnostico de CPU/RAM/disco de la Mac

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AayqVJ6rqcpTMXv9V2aBYz"

echo
echo "════════════════════════════════════════════"
echo "  3/3  SUBIENDO A GITHUB"
echo "════════════════════════════════════════════"
git push
echo
echo "✅ LISTO. Todo guardado."
git log --oneline -5
