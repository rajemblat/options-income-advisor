#!/bin/bash
# Guarda Y SUBE el trabajo de backtesting de los iron (23/08/2026, noche).
# Correlo UNA vez:  bash ~/options-income-advisor/scripts/guardar_backtest_iron.sh
set -euo pipefail
cd "$HOME/options-income-advisor"
[ -f .git/index.lock ] && { echo "Hay un .git/index.lock trabado. Borralo con:  rm .git/index.lock"; exit 1; }

echo "== Corriendo la suite completa (esperado: 1169 passed) =="
source .venv/bin/activate
pytest -q
echo

git add -A

git commit -q -m "El backtest de los iron mide las reglas que el robot realmente usa" -m \
"Pedido del usuario mirando la pantalla de Backtesting: 'los parametros de 0DTE y delta no son los
que usamos, quiero que hagas testing con los que usas vos'. Tenia razon, y al revisar aparecieron
tres cosas encadenadas:

1) EL DASHBOARD MEZCLABA LAS DOS ESTRATEGIAS. Habia UN solo `ipar`, armado con los valores del
   CONDOR, y se le pasaba tambien al butterfly. O sea que el backtest del butterfly media 35% del
   credito y stop de \$100, cuando su regla real es cerrar a +\$50 FIJOS con stop de -\$70. Todo
   backtest de butterfly corrido hasta hoy describia una estrategia que no existe.

2) EL FILTRO DE DIA CALMO NUNCA SE APLICABA. `calm_range_pct` estaba DEFINIDO en IronParams y no lo
   usaba nadie. El condor en vivo solo entra si el mercado viene tranquilo (<= 0.4% de rango), pero
   el backtest abria uno TODOS los dias, incluidos los violentos que el robot habria salteado.
   Medido sobre 5 anios sinteticos: 1285 operaciones sin filtro contra 51 con el filtro. Le estaba
   mostrando al usuario 25 veces mas operaciones de las que su estrategia tomaria.

   Cuidado con la trampa, y por eso probablemente el filtro habia quedado sin usar: en vivo eso se
   decide mirando los primeros 30 minutos. Con datos diarios solo se conoce el rango del dia ENTERO
   y usarlo seria decidir con informacion del futuro. El filtro mira el rango del dia ANTERIOR, que
   si esta disponible al abrir. Es una aproximacion declarada del gatillo real, no el gatillo real.

3) LA DISTANCIA DE LOS CORTOS ESTABA CLAVADA. Habia un '1.04 * sigma' escrito a mano. El numero no
   era arbitrario -- es exactamente el equivalente en sigmas de delta 0.15 -- pero quedaba fijo, asi
   que cambiar `short_delta_max` en la configuracion no movia el backtest. Ahora se deriva con
   z = inv_cdf(1 - delta), que a 0.15 devuelve 1.036: mismo resultado, pero siguiendo la config.
   El credito estimado tambien escala con el delta; antes se cobraba lo mismo vendiendo a 0.05 que
   a 0.30, que es absurdo.

`params_del_condor()` y `params_del_butterfly()` son ahora el UNICO lugar donde se traduce config a
parametros de backtest, y los usan tanto el dashboard como el trabajo semanal. Antes cada uno
armaba los suyos y se habian separado sin que nadie lo notara.

Los tres backtests corren con los valores EFECTIVOS (los que el aprendizaje ya movio), no con los
de settings.yaml." || echo "(sin cambios en este tema)"

git commit -q --allow-empty -m "El backtest semanal automatico incluye Iron Condor y Butterfly" -m \
"Hasta hoy el trabajo de los domingos corria SOLO el sweep de naked puts, aunque el motor ya tenia
condor y butterfly desde hace semanas. Los 0DTE quedaban disponibles nada mas que a mano desde el
dashboard, asi que en la practica no se median nunca.

Ahora corre los tres: naked puts sobre la watchlist a varios deltas, y condor + butterfly sobre
\$SPX, que es el subyacente que operan de verdad. Van en su propio bloque aislado con doble
try/except: si falla el historico de \$SPX, el informe de naked puts -- que es el que venia
funcionando -- sale igual.

Hallazgo de paso: revisando la base, el backtest semanal NUNCA dejo un informe desde que se
programo el 7 de agosto. La explicacion mas probable es que los domingos a las 18:00 la Mac estaba
dormida; el pmset que desactiva el sueño recien se puso el 20 de agosto."

git commit -q --allow-empty -m "El butterfly se mide contra su punto de equilibrio" -m \
"IronParams acepta ahora ganancia por MONTO fijo (`profit_dollars`), porque el Iron Butterfly cierra
a +\$50 y no a un porcentaje del credito (settings.yaml `intraday_butterfly.profit_target`, decision
del usuario del 10/08: 'scalp rapido').

Eso hace visible una aritmetica que estaba escondida: ganando \$50 y perdiendo \$70 hace falta
acertar mas del 58.3% de las veces SOLO para empatar. El dashboard ahora muestra ese numero al lado
del resultado, para poder compararlo de un vistazo con el porcentaje de acierto real.

No es una conclusion sobre la estrategia -- hace falta correrlo sobre historico real de \$SPX --
pero es la pregunta que el backtest existe para contestar, y hasta hoy no se estaba haciendo."

git commit -q --allow-empty -m "El informe semanal deja de esconder el riesgo" -m \
"Encontrado el 23/08 a las 18:00, la PRIMERA vez que el backtest semanal corrio de verdad. Dos
problemas en como presentaba los resultados, los dos capaces de empujar a una mala decision:

1) EL TITULAR DE NAKED PUTS OCULTABA EL DRAWDOWN. Decia 'el delta que MAS rindio fue 0.35 (win
   98.7%, P&L \$389.121, anualizado 770%)' y se callaba una caida maxima de \$879.416 -- MAS DEL
   DOBLE de la ganancia. Ademas el ranking ordena por P&L total, asi que por construccion siempre
   va a coronar al delta mas arriesgado: vender mas cerca del dinero cobra mas prima y gana casi
   siempre, hasta la vez que no. Un informe que ordena por retorno y esconde el riesgo no informa,
   empuja. Ahora el titular trae la caida maxima al lado, cuantas veces la ganancia representa, y
   dice explicitamente que el ranking premia al mas arriesgado.

2) EL CREDITO ASUMIDO POR LOS IRON QUEDABA INVISIBLE. Es el supuesto mas fragil de todo el backtest
   de 0DTE. La formula (precio x sigma x 0.20) escala con el nivel del indice, asi que con \$SPX a
   7.700 devuelve creditos de mas de \$1.000 por operacion. Los condors REALES del usuario cobraron
   entre \$167 y \$202. Medido: el modelo asume \$1.332 donde la realidad es \$185, o sea 7.2 veces
   mas generoso, y eso infla el P&L de \$49.000 a \$470.000. Ahora el informe dice el credito que
   asumio, para poder compararlo con el real antes de creerle al resultado.

Se agrega ademas un test que verifica que el backtest semanal NO aplique cambios por su cuenta: con
un credito 7 veces mayor que el real y un ranking que premia el riesgo, que ademas tocara parametros
solo seria peligroso."

echo
echo "== Subiendo a GitHub =="
git push
echo
echo "Listo: guardado y subido."
git --no-pager log --oneline -4
