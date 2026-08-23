#!/bin/bash
# Guarda en git el trabajo del 18 al 22 de agosto de 2026, en commits separados por tema.
# Correlo UNA vez desde la Terminal:  bash ~/options-income-advisor/scripts/guardar_cambios.sh
set -euo pipefail
cd "$HOME/options-income-advisor"

[ -f .git/index.lock ] && { echo "Hay un .git/index.lock trabado. Borralo con:"; echo "  rm .git/index.lock"; exit 1; }

git add -A

git commit -q -m "Aviso 24h antes de que venza el token de Schwab" -m \
"El refresh_token dura ~7 dias y al vencer el robot queda ciego: no puede pedir ni una
cotizacion. El 18/08 vencio de madrugada y no se noto hasta las 11:46 — media rueda perdida
con el mercado cayendo fuerte.

El reloj real no se podia medir: obtained_at se pisaba en cada refresh del access_token (cada
30 min), asi que el archivo siempre parecia recien emitido. Ahora se guarda aparte
refresh_token_obtained_at, sellado SOLO en el login manual y arrastrado en los refrescos.

- broker/token_watch.py: vigilante horario, avisa a las 24h y a las 6h por email y notificacion
  de macOS, una sola vez por nivel; al reconectar el ciclo arranca solo.
- broker/schwab_auth.py: sello de emision + lectura desde disco (un proceso viejo ya no pisa una
  reconexion hecha desde otra terminal).
- dashboard: cartel de aviso en todas las paginas.
- 15 tests nuevos." || echo "(nada que commitear en este tema)"

git commit -q --allow-empty -m "Una lectura fallida al broker ya no cierra posiciones reales" -m \
"El 21/08 a las 12:26 la API de Schwab fallo dos veces seguidas. get_all_positions() es
tolerante: loguea el error y devuelve lo que pudo, que ese dia fue una lista vacia. La
reconciliacion la tomo como verdad y marco closed_in_broker 5 posiciones REALES que seguian
abiertas (AAL x3, NVDA, AMZN). Ademas de descuadrar el registro, el robot dejo de vigilarlas:
sin cierre por ganancia y sin contar para los topes.

La solucion no es desconfiar de toda lista vacia — eso dejaria la reconciliacion muerta el dia
que cierres todo a mano. Es preguntar bien:

- schwab_client.get_all_positions_strict(): LANZA si alguna cuenta no respondio, asi que su
  lista vacia si es de fiar. _iter_raw_positions ahora anota los fallos.
- live_engine: la reconciliacion usa la version estricta; ante una lectura incompleta no cierra
  nada. Estaba duplicada en dos lugares, quedo en una sola funcion.
- 2 tests de regresion: lectura rota -> no cierra; lectura confiable y sin la posicion -> si."

git commit -q --allow-empty -m "Naked put sin stop-loss; el iron conserva el suyo" -m \
"Pedido del usuario el 20/08 tras el cierre de NCLH a -68 dolares (la opcion llego a 3.06x la
prima). simulator.stop_loss_multiple pasa a 0.0 = desactivado, y como es una regla compartida
cubre simulador y real de una sola vez.

Los stops del iron NO se tocan: son parametros aparte (intraday_condor.stop_loss_dollars = 100,
intraday_butterfly.stop_loss = 70).

Sin stop, un naked put que se va en contra corre hasta el vencimiento o la asignacion; el
control de riesgo pasa a ser la seleccion de entrada y los topes de colateral."

git commit -q --allow-empty -m "Diversificacion: el tamano se achica al repetir simbolo" -m \
"El robot no miraba lo que ya tenia. Cada vez que AAL caia 1% y pasaba los filtros abria el
tamano completo: quedaron 9 contratos en 3 entradas (17, 18 y 20 de agosto), todos expuestos al
mismo movimiento.

Escalera a la mitad: 1ra entrada el tamano completo, 2da la mitad, 3ra uno solo, 4ta ninguna.
Con AAL habria sido 4 -> 2 -> 1 -> nada = 7 contratos.

NO reemplaza la regla de tamano por precio de la empresa (07/08 y 17/08): esa sigue fijando el
tamano BASE de la primera entrada. Cuando frena del todo queda registrado como orden frenada,
con el motivo visible en el dashboard."

git commit -q --allow-empty -m "Cierres por ganancia en su propio hilo, cada minuto" -m \
"El chequeo de objetivo de ganancia colgaba del final de _run_robot_scan, despues del bucle que
analiza el universo entero. Ese bucle tarda varios minutos y APScheduler saltea corridas
('skipped: maximum number of running instances reached'), asi que el cierre esperaba. El 19/08
AAPL cruzo su objetivo y se cerro recien 15:56; el email de apertura de DLO llego 24h tarde.

job_live_position_maintenance: re-precio, cierre real y emails pendientes, cada minuto en hilo y
conexion propios. Mismo patron que ya se uso para la deteccion de operaciones reales y el
butterfly. El escaneo sigue llamando a las mismas funciones como red de seguridad; son
idempotentes y el lock de live_engine evita que corran dos a la vez."

git commit -q --allow-empty -m "Dashboard: utilidad por periodo, P&L en vivo del condor y columnas correctas" -m \
"- Filtro Hoy/Semana/Mes/Ano/Todo en naked y en iron condor, arriba del panel y mandando sobre el
  numero grande. El titular pasa a ser plata COBRADA, sin el flotante.
- El panel de naked colgaba de un else que solo corria con posiciones abiertas: al cerrarse las 5
  el 21/08 desaparecio hasta el historico. Ahora se muestra siempre.
- Columna Cant: mostraba la cantidad AGREGADA del broker en cada fila (AAL 13P aparecia -5 en dos
  filas distintas, como si fueran 10 contratos). Ahora muestra los de cada orden, y el margen se
  prorratea.
- Columna Abierta: fecha y hora de cada posicion, tomada de log_ts (sent_ts se pisa en cada
  reemplazo del precio caminado).
- P&L del condor abierto calculado en vivo desde el valor de mercado de las 4 patas, con la misma
  llamada que ya hace la tabla de naked (sin consultas extra).
- Moshe informaba 0% de ganancia: leia unrealized_pnl de Schwab, que llega en 0 y se queda en 0.
  Ahora calcula (credito - mark), igual que Real Market.
- El email de apertura dice cuando se abrio la operacion."

git commit -q --allow-empty -m "Respaldo diario de la base y suite de tests en verde" -m \
"data/app.db esta excluida de git a proposito, asi que GitHub no la protege: ahi viven ~46.000
decisiones, 153 ordenes reales y todo el historial de P&L. Y ya se corrompio dos veces (quedan
data/_corrupt/, data/_corrupt2/ y un app_reparada.db).

scripts/backup_db.py usa la API .backup de SQLite y no un cp: con la base en WAL, copiar el
archivo puede agarrar un estado a medio escribir. Verifica con integrity_check antes de
comprimir y rota a 30 dias.

Tests: la suite tenia 7 fallas de antes, todas de tests que quedaron viejos cuando se cambiaron
reglas a proposito (escalones de ganancia por antiguedad del 12/08, agrupado de patas del 12/08,
custom_multileg que es etiqueta de deteccion y no se construye). Actualizados con el motivo
escrito. 1081 tests en verde."

echo
echo "Commits creados. Ahora subilos a GitHub con:"
echo "    cd ~/options-income-advisor && git push"
git --no-pager log --oneline -7
