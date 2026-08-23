#!/bin/bash
# Guarda en git los arreglos de la auditoria del 22/08/2026.
# Correlo UNA vez:  bash ~/options-income-advisor/scripts/guardar_auditoria.sh
set -euo pipefail
cd "$HOME/options-income-advisor"
[ -f .git/index.lock ] && { echo "Hay un .git/index.lock trabado. Borralo con:  rm .git/index.lock"; exit 1; }

git add -A

git commit -q -m "Un IntegrityError ya no deja la base trabada para todo el robot" -m \
"El unico manejo de errores de SQLite del proyecto -- el except IntegrityError de
insert_real_trade_alert -- hacia 'return None' sin rollback. Python emite BEGIN antes del INSERT,
el INSERT toma el lock de escritor del WAL, el UNIQUE falla, el statement se aborta pero la
TRANSACCION no. Verificado: conn.in_transaction queda en True y cualquier otra conexion recibe
'database is locked'.

Por que era grave: ese IntegrityError es ESPERABLE. El indice unico existe justamente para
resolver la carrera entre el dashboard y el scheduler detectando la misma operacion (incidente
del 29/07), asi que se dispara cuando hay dos procesos trabajando. Y la conexion del dashboard
vive cacheada por Streamlit. A partir de ese momento el robot 'seguia andando', logueaba, y no
persistia ni una decision ni un cierre ni un P&L. En silencio.

En todo el proyecto no habia un solo rollback(). Ahora hay uno, con un test que verifica que otra
conexion puede escribir despues del choque." || echo "(sin cambios en este tema)"

git commit -q --allow-empty -m "Llenados parciales: se dejan de mandar contratos de mas" -m \
"Schwab no tiene estado PARTIALLY_FILLED: una orden de 4 con 2 llenados sigue reportando WORKING
con filledQuantity=2. Y replace_order CANCELA el remanente y crea una orden NUEVA por la cantidad
que se le pase. Como el payload usaba siempre la cantidad completa, con 4 puts de AAL aprobados el
peldaño 1 llenaba 2, el reemplazo volvia a pedir 4 y llenaba: 6 puts cortos. Un 50% mas de lo que
aprobo el guardian, con colateral que ningun tope conto.

El assert de live_guard protege la DECISION; esto protege la EJECUCION.

Ademas, una orden que moria (EXPIRED/CANCELED) con parte llenada se registraba con
filled_contracts=0. Como get_open_real_put_positions exige order_status='FILLED', esos contratos
REALES quedaban invisibles: sin objetivo de ganancia, sin stop, y sin contar para ningun tope.

Deliberadamente NO se usa filledQuantity para declarar una orden llenada -- solo el status decide
eso. Se usa unicamente para dimensionar el reemplazo, donde equivocarse hacia abajo es inofensivo
(se manda de menos y el proximo escaneo reintenta) y hacia arriba compra lo que nadie aprobo.

Los stubs de los tests devolvian filledQuantity=1 incluso en WORKING, que es imposible en Schwab
y tapaba el bug. Corregidos, mas 2 tests nuevos."

git commit -q --allow-empty -m "La apertura automatica de ordenes reales tambien toma el lock" -m \
"maybe_log_live_order era la UNICA funcion que mandaba ordenes reales sin @_serialized, mientras
sus hermanas (send_pending_open_emails, reprice_resting_orders, close_real_positions,
process_approved_ai_orders) si lo tomaban. Los comentarios del repo afirmaban que el lock cubria
'TODAS las operaciones que tocan ordenes REALES'; no era cierto.

El agujero: el anti-duplicado has_live_committed_order_for_symbol_today exige sent=1, y esa marca
se escribe recien DESPUES del price walk, que dura hasta 3 minutos. Si el chat aprobaba vender NU
a las 10:00 y entraba al walk, el escaneo podia llegar a NU a las 10:01, no ver la fila del chat
(todavia con sent=0) y mandar una SEGUNDA orden real del mismo simbolo."

git commit -q --allow-empty -m "Una lista de cuentas vacia tambien es 'no se'" -m \
"Completa el arreglo del 21/08. Ese cubria los fallos de HTTP, pero Schwab puede responder 200 con
una lista de cuentas VACIA: consentimiento revocado o re-consentido, mantenimiento, o un token
valido cuya cuenta quedo desvinculada. En ese caso el generador no iteraba, 'fallos' quedaba
vacio, get_all_positions_strict devolvia [] sin lanzar, y la reconciliacion volvia a cerrar TODAS
las posiciones abiertas: el incidente del 21/08 reproducido tal cual."

git commit -q --allow-empty -m "La ventana de gracia del condor estaba muerta desde siempre" -m \
"_reconcile_sending calculaba la edad de la orden anclando en entry_date + medianoche, no en el
momento del envio. Durante el mercado eso da entre 570 y 960 minutos, siempre mayor que los 5 de
_SENDING_GRACE_MINUTES, asi que la gracia NUNCA se aplicaba: una orden recien colocada y todavia
negociandose se declaraba 'no_confirmada' en el primer tick, se cerraba la fila, y la orden real
seguia viva en Schwab. Condor abierto sin marcado, sin profit target y sin stop-loss: el mismo
incidente del 14/08 que esa funcion fue escrita para evitar.

entry_ts guarda fecha Y hora y estaba ahi al lado, sin usarse."

git commit -q --allow-empty -m "min_account_cash_buffer avisa que no esta protegiendo nada" -m \
"El guardian muestra el colchon de cash libre como una capa activa, pero live_engine pasa siempre
AccountSnapshot(cash=1e9) -- en real y en dry-run -- porque no existe ninguna llamada que traiga
el cash o el buying power real de Schwab. Con el valor en 0.0 es inocuo. El dia que alguien ponga
20000 creyendo que se protege, el guardian lo aceptaria, lo mostraria como capa activa y no
frenaria ni un contrato.

No se implementa la lectura del saldo (haria falta probarla contra la API real). Se registra un
ERROR en el log si el tope esta configurado y no se puede aplicar, para que no de una falsa
sensacion de seguridad."

echo
echo "Commits creados. Subilos con:   cd ~/options-income-advisor && git push"
git --no-pager log --oneline -6
