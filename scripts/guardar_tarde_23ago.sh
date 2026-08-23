#!/bin/bash
# Guarda Y SUBE lo trabajado la tarde del 23/08/2026 (despues del commit de las 13:34).
# Correlo UNA vez:  bash ~/options-income-advisor/scripts/guardar_tarde_23ago.sh
set -euo pipefail
cd "$HOME/options-income-advisor"
[ -f .git/index.lock ] && { echo "Hay un .git/index.lock trabado. Borralo con:  rm .git/index.lock"; exit 1; }

echo "== Corriendo la suite completa (esperado: 1151 passed) =="
source .venv/bin/activate
pytest -q
echo

git add -A

git commit -q -m "El healthcheck ya no se suicida cuando no puede revivir al robot" -m \
"Encontrado leyendo los logs del 20/08. Ese dia el LaunchAgent del robot estaba descargado (lo
habiamos bajado para reparar la base). El healthcheck lo detecto BIEN y quiso revivirlo:

    launchctl kickstart ... -> exit 113
    Could not find service '...scheduler' in domain for user gui: 501

Pero _restart_scheduler usaba subprocess.run(..., check=True), asi que ese exit 113 se convirtio en
una excepcion que mato el proceso entero. El healthcheck dejo de correr y no volvio hasta que
recargamos el agente del robot ese mismo dia.

O sea: el componente que existe para ser la ultima linea de defensa se suicido exactamente en el
escenario que tenia que reportar, y en silencio.

Ahora el fallo NO lanza: se registra, y se manda un email con el comando exacto para arreglarlo en
la plataforma donde este corriendo. 'Tu robot esta muerto y no lo puedo revivir' es el mensaje mas
importante que este sistema puede mandar; perderlo por un check=True no es aceptable.

Verificado reproduciendo el exit 113 exacto contra el codigo nuevo: sobrevive y avisa. Tres tests
de regresion, incluido uno que falla si vuelve a aparecer check=True." || echo "(sin cambios en este tema)"

git commit -q --allow-empty -m "Ritmo de escaneo: el robot que mira no le come el cupo al que opera" -m \
"Durante la validacion conviven dos robots sobre la MISMA cuenta de Schwab. Medido: cada simbolo son
3 llamadas a la API (get_quote + get_price_history + get_option_chain) y el universo son ~101
simbolos, o sea ~300 llamadas por escaneo, disparado cada minuto.

Por que importa, y esto es lo verificado en el codigo: SchwabBrokerClient.place_order() NO tiene
reintentos. Un 429 justo cuando sale una orden la pierde. Y esta BIEN que no reintente -- reintentar
el POST de una orden puede terminar en orden duplicada -- asi que la solucion no puede ser tocar el
camino de las ordenes.

deploy/ritmo.py baja el escaneo a cada 5 minutos en la maquina que solo valida. Cada 5 min alcanza
de sobra: se valida QUE decide, no cuantas veces por hora lo decide. La Mac queda intacta en 1 min.

Conserva el comentario original de la linea en settings.yaml (explica por que el valor de produccion
es 1, con fecha y pedido del usuario) y solo le agrega una marca, que se limpia al volver a normal.
Siete tests, incluido que ida y vuelta deja el archivo byte a byte identico."

git commit -q --allow-empty -m "Reporte diario para comparar las dos maquinas" -m \
"No hay forma de comparar dos robots mirando dos dashboards: son cientos de simbolos y cada vista se
refresca en momentos distintos. scripts/reporte_dia.py imprime un bloque corto y determinista con lo
que la maquina vio y decidio ese dia: simbolos analizados, precios de 8 simbolos FIJOS (elegidos de
antemano justamente para que sean comparables), conteo de decisiones por accion, motivos mas
frecuentes, candidatos, y cuantas decisiones fallaron por RED.

Solo biblioteca estandar, a proposito: corre con el python3 del sistema sin activar el entorno del
proyecto, que es una cosa menos que explicar por SSH.

Ya dio su primer resultado util: el reporte del viernes 21/08 muestra 107 decisiones falladas por
red. El apagon de DNS de ese dia, contado."

git commit -q --allow-empty -m "Swap de 2 GB y verificacion de integridad de la base en el instalador" -m \
"Medido en el servidor con el robot y el dashboard corriendo y el mercado cerrado: 720 MB usados,
1.2 GB disponibles, y Swap: 0B. Sin swap, un pico de memoria durante el escaneo del universo hace
que el kernel mate el proceso mas grande en vez de paginar: el robot, en horario de mercado, sin
aviso. 2 GB de archivo de swap con swappiness=10, asi en operacion normal no se toca.

Ademas, el instalador ahora corre PRAGMA integrity_check sobre la base recien copiada. La copia
desde la Mac se hace con el robot ANDANDO (a proposito, para no cortar el trading durante la
validacion), y un archivo de 42 MB copiado mientras se escribe puede llegar cortado. Mejor
descubrirlo en un comando que el lunes con el mercado abierto. Si falla, imprime los comandos
exactos para rehacer la copia con el robot detenido."

echo
echo "== Subiendo a GitHub =="
git push
echo
echo "Listo: guardado y subido."
git --no-pager log --oneline -5
