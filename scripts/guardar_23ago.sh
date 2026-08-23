#!/bin/bash
# Corre la suite y guarda en git los arreglos del 23/08/2026.
# Correlo UNA vez:  bash ~/options-income-advisor/scripts/guardar_23ago.sh
set -euo pipefail
cd "$HOME/options-income-advisor"
[ -f .git/index.lock ] && { echo "Hay un .git/index.lock trabado. Borralo con:  rm .git/index.lock"; exit 1; }

echo "== Corriendo la suite completa (esperado: 1121 passed) =="
source .venv/bin/activate
pytest -q
echo

git add -A

git commit -q -m "Los tests ya no le mandan emails de verdad al usuario" -m \
"El 23/08 llegaron a la casilla tres avisos de cierre con datos que no existen en ninguna base:
'C put 125 vendido a \$1.50', 'AAL put 11 a \$1.55' y un Iron Condor de \$200 cerrado a mano.
Llegaron dos veces, mezclados con los avisos reales del robot. No los mando el robot: los mando
pytest.

La cadena: dashboard/components.py hace load_dotenv(.env) al importarse. Con que la suite recoja
UN test de tests/test_dashboard/ ese import corre, y SMTP_HOST/USER/PASSWORD/EMAIL_TO reales
quedan en os.environ de TODO el proceso. tests/test_execution/test_live_engine.py llama a
close_real_positions() de verdad, que llama a notifier.send_email() de verdad. Cada corrida de la
suite era un envio real con datos de fixture.

Dos frenos, a proposito, porque el correo es irreversible: un fixture autouse en tests/conftest.py
que borra las credenciales de todos los canales en cada test, y un chequeo dentro del notifier que
corta si detecta PYTEST_CURRENT_TEST o LOKSHN_NO_NOTIFY=1. Verificado empiricamente que sin el
freno se abria la conexion SMTP y con el freno no se abre ninguna." || echo "(sin cambios en este tema)"

git commit -q --allow-empty -m "Los tres canales de aviso quedan separados y firmados" -m \
"Pedido del usuario: 'es importante que no se mezcle'. Los canales son:
  1. OPERACIONES (espejo de su cuenta de Schwab, alerts/real_trades.py) — pestaña Operaciones y
     Telegram. Son las que el copia y reenvia. NO manda email y no se toco una linea.
  2. ROBOT REAL (live_engine.py, live_condor_engine.py) — el unico canal que manda email.
  3. SIMULADOR — no avisa hacia afuera.

En produccion nunca se mezclaron: el unico emisor de email era el canal 2. La confusion la
generaron los correos de fixture de la suite. Aun asi, ahora todo email del canal 2 sale firmado
al pie con 'Lokshn - ROBOT operando con DINERO REAL', y hay un test de fuente que falla si alguien
agrega un aviso del robot llamando a send_email() pelado, mas otro que verifica que el canal
Operaciones sigue sin mandar correo."

git commit -q --allow-empty -m "El robot avisa cuando se queda ciego por falta de red" -m \
"Viernes 21/08: la Mac perdio la resolucion de DNS. Toda llamada a Schwab murio con
'httpx.ConnectError: nodename nor servname provided'. El robot siguio 'corriendo' —309 escaneos
ese dia— pero 288 terminaron en menos de un segundo porque cada simbolo reventaba al instante.
Nadie se entero hasta el domingo. Ese mismo apagon produjo la lectura vacia de posiciones que casi
cierra 5 operaciones abiertas por error.

Un robot ciego que no avisa es peor que un robot apagado: parece que esta trabajando.

Nuevo modulo broker/conectividad.py: cuenta fallos de RED seguidos (httpx.TransportError, que es
'no llegamos a Schwab') distinguiendolos de 4xx/5xx (que prueban que SI hay conexion). Con 20
fallos seguidos Y mas de 3 minutos sin una sola respuesta buena, manda UN aviso por apagon,
con el mismo patron anti-repeticion que el vigilante del token. El texto dice explicitamente que
NO es el token vencido, porque durante un apagon el dashboard muestra 'no autenticado' y la
reaccion natural —salir a re-loguearse a mano— no arregla nada.

El enganche va en el transporte de httpx y no en cada metodo: hay una docena de llamadas
distintas y una nueva se olvidaria de reportar. El chequeo cuelga de job_live_position_maintenance,
que ya late cada minuto en horario de mercado."

git commit -q --allow-empty -m "Un refresh de token que falla por red deja de martillar" -m \
"Consecuencia del mismo apagon: _refresh() revienta ANTES de guardar nada, asi que obtained_at
nunca se actualiza y la condicion 'vencido o por vencer' queda pegada en True para siempre. Cada
una de las ~100 llamadas por escaneo re-entraba a la rama de refresh, escribia 'refrescando...' en
el log e intentaba un POST que volvia a reventar, volcando un stack trace de 90 lineas.

Medido en el log del 21/08: 31.073 lineas de 'refrescando...' para 15 refrescos de verdad.

Ahora, tras un fallo de RED, los siguientes 30 segundos cortan de una sin tocar la red, con un
mensaje que aclara que no es el token. Un 400/401 de Schwab —el token vencido de verdad— sigue
dando su mensaje de siempre con las instrucciones de re-login: el freno nuevo no lo tapa."

git commit -q --allow-empty -m "El log del robot rota y no vuelve a comerse el disco" -m \
"data/logs/scheduler.err.log habia llegado a 560 MB. Lo escribe launchd redirigiendo stderr y NO
rota: con el disco lleno y el robot operando plata real se pierde la rueda. Se trunco (guardando
el ultimo tramo en scheduler_ultimo_tramo.log) y se cambio la configuracion:

  - handler 'archivo': todo el detalle INFO en data/logs/robot.log, rotado 20 MB x 5 = 120 MB de
    techo absoluto. configure_logging() ahora convierte la ruta a absoluta y crea la carpeta, para
    que no dependa del directorio desde el que se arranque el proceso.
  - handler 'console' (lo que captura launchd): solo WARNING para arriba.
  - httpx a WARNING: logueaba una linea por request y un escaneo son cientos.
  - los fallos de RED por simbolo pasan a UNA linea en vez de un stack trace de 90.

TRAMPA que casi entra en produccion: scripts/healthcheck_scheduler.py decide si el robot esta
colgado mirando el mtime de un archivo de log, y apuntaba a scheduler.err.log. Con la consola en
WARNING un robot SANO no escribe nada ahi, su mtime se congela, y el healthcheck lo habria
reiniciado en bucle con el mercado abierto. Se repunta a robot.log y se agrega un test que ata las
dos rutas. Por lo mismo, apscheduler se DEJA en INFO aunque sea ruidoso: sus lineas cada 15
segundos son el latido que mantiene vivo ese chequeo."

echo
echo "Listo. Commits creados."
git --no-pager log --oneline -6
echo
echo "Subilos con:   cd ~/options-income-advisor && git push"
