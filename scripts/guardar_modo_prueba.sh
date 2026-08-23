#!/bin/bash
# Guarda Y SUBE a GitHub el modo prueba para la mudanza (23/08/2026).
# Correlo UNA vez:  bash ~/options-income-advisor/scripts/guardar_modo_prueba.sh
#
# A diferencia de los anteriores, este hace el `git push` solo. Los dos scripts previos lo dejaban
# como paso aparte y las dos veces falló, porque al terminar el script la terminal vuelve a la
# carpeta personal y `git push` ahí no encuentra el repositorio.
set -euo pipefail
cd "$HOME/options-income-advisor"
[ -f .git/index.lock ] && { echo "Hay un .git/index.lock trabado. Borralo con:  rm .git/index.lock"; exit 1; }

echo "== Corriendo la suite completa (esperado: 1141 passed) =="
source .venv/bin/activate
pytest -q
echo

git add -A

git commit -q -m "Modo prueba: el servidor puede mirar sin operar mientras la Mac trabaja" -m \
"Pedido del usuario (23/08): 'no quiero que se borre de la Mac hasta comprobar que funciona bien en
otro lado'. Es la decision correcta, pero solo es segura si el robot nuevo NO puede mandar una
orden. Nunca puede haber dos robots en modo real a la vez: single_instance.py protege con un flock
sobre un archivo LOCAL, o sea que entre maquinas distintas no ve nada, y cada una lleva sus topes
diarios en su propia base.

deploy/modo.py conmuta entre 'prueba' (dry_run + kill_switch) y 'real', y lo VERIFICA leyendo la
configuracion ya parseada por pydantic. Confiar en acordarse de configurarlo a mano es exactamente
el tipo de cosa que sale mal una vez y cuesta plata.

Edita settings.yaml como TEXTO, cambiando solo dos lineas dentro del bloque live_trading. Es a
proposito: reescribir el YAML con una libreria se llevaria puestos los cientos de comentarios que
explican por que cada numero es el que es, y esos comentarios son media documentacion del proyecto.
Ocho tests cubren lo delicado: que no toque el dry_run del simulador ni el kill_switch de alerts
(nombres genericos que aparecen en otras secciones), que conserve todos los comentarios, que no
mueva las columnas, y que ida y vuelta deje el archivo byte a byte identico.

En modo prueba el camino real esta cortado en cuatro lugares del codigo: no manda ordenes, no
cierra posiciones, no re-precia y no manda emails de apertura ni de cierre." || echo "(sin cambios en este tema)"

git commit -q --allow-empty -m "LOKSHN_NO_NOTIFY tambien silencia Telegram" -m \
"El freno ya existia para email y notificaciones de macOS. Faltaba send_text.

Durante la validacion conviven dos robots mirando el mismo mercado, y solo el de la Mac -el que
opera de verdad- tiene permitido avisar. Sin esto cada alerta llegaria dos veces y no se sabria
cual vino de donde: exactamente la confusion que ya costo un susto el 23/08 con los emails que
mandaban los tests.

A diferencia de send_email y send_native, aca NO se mira PYTEST_CURRENT_TEST: los tests de Telegram
inyectan credenciales falsas y reemplazan httpx, asi que no sale nada a la red y necesitan poder
recorrer ese camino."

git commit -q --allow-empty -m "El robot dice en voz alta en que modo arranca" -m \
"Primera linea en la consola y en el log: 'MODO PRUEBA (mira pero NO opera)' o 'MODO REAL (opera
con plata de verdad)'.

Durante la mudanza conviven el robot de la Mac y el del servidor, y confundirlos es la unica forma
de que esto salga caro. Que haya que ir a leer settings.yaml para saber cual es cual no alcanza."

git commit -q --allow-empty -m "El instalador del servidor arranca en modo prueba por defecto" -m \
"Instalar en modo real es lo que hay que pedir explicitamente (--real), no al reves. El default
seguro es el que no puede perder plata.

Con --real el script para y exige que se escriba SI confirmando que el robot de la Mac ya esta
apagado, antes de tocar nada.

En modo prueba agrega ademas Environment=LOKSHN_NO_NOTIFY=1 a los servicios de systemd. Va en el
servicio y no en el .env por dos razones: se ve de un vistazo con `systemctl --user cat
lokshn-robot`, y desaparece solo al reinstalar en modo real.

MUDANZA.md pasa a describir la mudanza en dos etapas, con la advertencia de por que la base hay que
copiarla DE NUEVO el dia del cambio -la Mac siguio operando durante toda la validacion- y con que
mirar esos dias para decidir si confiar en el servidor."

echo
echo "== Subiendo a GitHub =="
git push
echo
echo "Listo: guardado y subido."
git --no-pager log --oneline -5
