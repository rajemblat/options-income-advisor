#!/bin/bash
set -e
cd ~/options-income-advisor
rm -f .git/index.lock
echo "══════ TESTS ══════"
.venv/bin/python -m pytest -q
echo "✅ Tests OK."
echo "══════ GUARDANDO ══════"
git add src/options_advisor/alerts/notifier.py \
        src/options_advisor/broker/token_watch.py \
        tests/test_alerts/test_email_reintentos.py \
        scripts/guardar_avisos.sh

git commit -F - <<'MSG'
Los avisos ya no mueren por un parpadeo de DNS

El 30/08 12:07 el vigilante disparo "tu token de Schwab vence en 24 horas". El
envio fallo con socket.gaierror: nodename nor servname provided -- DNS, no
credenciales. El aviso se perdio. Lo mismo el 31/08 06:07 con el ultimo llamado.
El lunes el token vencio con el mercado abierto y el usuario se entero mirando
el dashboard. De todos los mails de la semana solo fallaron 4; dos de esos
cuatro eran justo estos.

Dos arreglos:

- notifier.send_email reintenta 3 veces, esperando 3 y 6 segundos. Un corte de
  DNS momentaneo se resuelve en el segundo intento. El log ahora dice el error
  concreto en vez de solo "fallo al enviar".

- token_watch marca la bandera anti-repeticion SOLO si el mail salio de verdad.
  Antes la marcaba siempre: con el envio fallado quedaba anotado "nivel 24h ya
  avisado" y el vigilante -- que corre cada hora, 24 veces antes del vencimiento
  -- no lo reintentaba nunca. Ahora, si no sale, no se marca y se reintenta a la
  hora siguiente.

Es el mismo patron que veniamos arreglando toda la semana en el condor: dar algo
por hecho sin verificar que paso de verdad.

Nota de diagnostico: el vigilante SI funciono y disparo los tres avisos (24h, 6h
y vencido) a la hora correcta. Lo que fallo fue el canal, no la deteccion.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AayqVJ6rqcpTMXv9V2aBYz
MSG

echo "══════ SUBIENDO ══════"
git push
echo "✅ LISTO."
git log --oneline -3
