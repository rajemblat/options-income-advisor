#!/bin/bash
set -e
cd ~/options-income-advisor
rm -f .git/index.lock
echo "══════ TESTS ══════"
.venv/bin/python -m pytest -q
echo "✅ Tests OK."
echo "══════ GUARDANDO ══════"
git add src/options_advisor/execution/live_condor_engine.py \
        tests/test_execution/test_no_abrir_sin_vision.py \
        scripts/guardar_chequeo_previo.sh

git commit -F - <<'MSG'
Condor real: no abre si el robot no esta en condiciones de ejecutar el stop

El stop de $100 NO es una orden puesta en Schwab: lo dispara el robot, mirando
el precio cada minuto. Si el robot no ve, el stop no existe -- y quedan hasta
$835 de riesgo sin proteccion, con el usuario creyendo que esta cubierto.

Esta semana el robot quedo ciego tres veces con el mercado abierto:
  27/08  35 min sin DNS justo en la ventana de entrada del condor
  28/08  2845 errores de conexion entre las 09 y las 11
  31/08  token vencido de 12:02 a 13:36

Ninguna de esas veces habia un condor abierto. Fue suerte, no diseno.

Regla del usuario (2026-08-31): "no puede abrir sin stop loss" / "debe verificar
que el stop funciona". `puede_cuidar_la_posicion()` chequea, ANTES de abrir:
conexion sana (no ciego, menos de 5 fallos seguidos, algun exito en los ultimos
5 minutos) y token con mas de 3 horas de vida -- un 0DTE hay que poder vigilarlo
hasta el cierre.

Solo condiciona ABRIR. Cerrar, marcar y gestionar lo ya abierto no pasa por aca:
si hay una posicion viva el robot tiene que seguir intentando cuidarla aunque la
red venga mal.

Bug atrapado escribiendo esto, con test que lo fija: conectividad.estado()
devuelve el TIMESTAMP del ultimo exito, no los segundos transcurridos. La primera
version comparaba ese timestamp contra 300 segundos -- como vale ~1.8 mil
millones, la condicion daba siempre verdadera y el condor no habria abierto nunca
mas. Un freno de seguridad que frena siempre no protege: rompe.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AayqVJ6rqcpTMXv9V2aBYz
MSG

echo "══════ SUBIENDO ══════"
git push
echo "✅ LISTO."
git log --oneline -3
