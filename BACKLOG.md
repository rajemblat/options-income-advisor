# Backlog consolidado

Registro vivo de todo lo pedido, para no perder el hilo en sesiones largas. Se actualiza cada
vez que algo arranca o termina — no es un historial (eso está en `NOTES.md` y en `git log`),
es el estado ACTUAL de qué falta.

Última actualización: 2026-07-31 (Página "Gráfico de velas" con VWAP completada — ver ítem
#51. Queda 1 tarea confirmada pendiente, no empezada: conectar el gráfico con alertas — ver
sección "Pendiente, no empezado". Antes en la sesión: Modelo de barra intradía + VWAP
completado — ver ítem #50. Antes: Rebranding a "OptionsUp" completado — ver ítem #49.
Antes en la sesión: Pestaña Operaciones: corrección de layout sobre la vista de
tabla del día anterior — aclaración del usuario: lo pedido no era un cambio de lógica sino
ESTÉTICO. La vista de Tabla ahora es 1 fila compacta por operación, apertura o roll por igual
(Symbol, Time/Date, Now, Orig, IVR, Description, Action, Price) — ya no se arma 1 bloque
amarillo/verde ahí mismo. Clic en una fila (`st.dataframe(..., on_select="rerun",
selection_mode="single-row")`) abre el detalle completo debajo (P&L, breakeven, POP, cobertura,
noticias, comentario, check histórico), mostrando el bloque amarillo/verde de roll solo ahí.
Filtros Symbol/Rango de fechas/Estrategia/Tipo siempre visibles arriba. Tabla pasa a ser la
vista default (antes Tarjetas). Verificado en vivo con capturas: tabla compacta y detalle
abierto al clickear la fila TSLA — Roll (bloque amarillo "Operación anterior (cerrada)" +
verde "Operación nueva (abierta)", seguido de la tarjeta completa de P&L/POP/cobertura/riesgo).
Bug real encontrado y corregido en el camino: st.dataframe en esta versión de Streamlit
muestra el texto literal "None" para valores faltantes en una columna numérica (con NaN
Y con None, con o sin `format` en column_config — confirmado con una repro mínima aislada) —
la única forma de que se vea en blanco es pre-formatear esos valores como strings ("" en vez
de None/NaN) en vez de dejarlos como columna numérica. 631/631 tests en verde. Interrupción
en medio de la sesión: el refresh_token de Schwab expiró (límite documentado de ~7 días, no
un bug) rompiendo TODOS los datos en vivo del dashboard; resuelto con el usuario re-logueándose
manualmente vía `scripts/schwab_login.py` en su propia Terminal — todas las páginas
re-verificadas después (General, Alertas, Watchlist, Portafolio, Operaciones, Escaneo).
Antes en la sesión: Pestaña Operaciones: cambio de alcance sobre la Fase 1 —
ahora SÍ se muestran los ROLLS (antes se saltaban del todo), agrupados visualmente como
"Operación anterior" (amarillo) + "Operación nueva" (verde) en un mismo bloque; además, vista
de tabla plana nueva (toggle Tarjetas/Tabla) tipo feed de operaciones, con precio EN VIVO y
Tipo (Apertura/Roll). Confirmado antes de implementar cómo mostrar el roll (2 filas conectadas
dentro de un bloque, no 1 fila con columnas dobles — evita amontonar 2 strikes/2 vencimientos
en las mismas celdas) y que `_is_roll()` ya distingue con certeza vía /orders. Verificado en
vivo con un roll real de TSLA (mismo strike $350, vencimiento 08/07→09/04): el bloque muestra
"Operación anterior (cerrada) · 1 Put $350.00 · Cerrado a $44.68" en amarillo y "Operación
nueva (abierta) · 1 Put $350.00 · Entrada $48.08 · $4,808.00 crédito" en verde, contado como 1
sola "operación" (no 2) en el encabezado de fecha; la vista de tabla muestra el precio en vivo
($44.75 vs. $48.08, verde por ser favorable) y las etiquetas "Roll · Nuevo"/"Roll · Cerrado"
correctamente. Antes en la sesión: Market Movers: top 10 REAL por % en vez del ranking por
volumen de `/movers` de Schwab. Investigado a fondo antes de programar: confirmado en vivo,
probando los 3 índices × 4 valores de `sort` × 6 valores de `frequency`, que ese endpoint
SIEMPRE devuelve las mismas 10 acciones de mayor volumen — nunca 10 ganadoras + 10 perdedoras
reales, y la sospecha del usuario de que era "solo un problema del Dow (30 empresas)" no era la
causa raíz (pasaba igual en S&P 500 y Nasdaq). El usuario eligió la opción de más esfuerzo:
traer los componentes REALES de cada índice (S&P 500/Nasdaq-100/Dow 30, de Wikipedia, wikitext
crudo parseado con regex — NO resumen de IA, que inventó tickers incorrectos en un intento
previo) y calcular el ranking real localmente con quotes en batch, en vez de depender del
ranking de Schwab. Con esto, la sospecha original del usuario SÍ se confirmó como limitación
matemática real pero solo para Dow (30 nombres): un umbral mínimo de magnitud (±0.5%) filtra
movimientos insignificantes, mostrando MENOS de 10 en vez de rellenar con ruido cuando no hay
suficientes — verificado en vivo, Dow mostró 7 ganadoras/9 perdedoras un día con pocos
movimientos grandes, mientras S&P 500 y Nasdaq mostraron el top 10 completo sin problema.
Antes en la sesión: check histórico refinado con una banda de tolerancia de
plazo (±1 semana) Y magnitud (±3 puntos porcentuales) — pedido explícito del usuario para
buscar "movimientos SIMILARES" en vez de solo "movimientos de al menos este tamaño". Confirmado
con el usuario ANTES de programar (ejemplo concreto con números reales, aclarando que es una
pregunta distinta a la del badge existente) y con 2 preguntas de diseño respondidas: los
crashes más grandes que la banda se muestran APARTE (no se esconden), y el badge nuevo
COEXISTE con el existente (no lo reemplaza). Verificado en vivo con datos reales de Citigroup:
"pasó 76 veces — y hubo 22 veces una caída AÚN MÁS GRANDE en un plazo similar", junto al badge
original ("tocó este nivel 32 veces"). Antes en la sesión: bug real URGENTE corregido: la
detección de Operaciones
procesaba cada pata VENDIDA de una orden combinada por separado, así que un Iron Condor de 4
patas de AMD se guardaba como 2 alertas sueltas de 1 pata desnuda cada una (Cash-Secured Put +
Short Call Naked), ignorando las 2 patas COMPRADAS que definen el riesgo acotado. Fix: la orden
completa se clasifica según su composición REAL antes de procesar nada — reconoce Iron Condor
(4 patas) y credit spreads (2 patas, Bull Put/Bear Call) — y arma UNA sola alerta multi-pata en
vez de varias sueltas. Corregido en vivo contra la posición real de AMD del usuario (verificado
con el order log crudo de Schwab: `complexOrderStrategyType: "IRON_CONDOR"`, 4 patas reales) —
la tarjeta ahora muestra "AMD — Iron Condor" con las 4 patas y pérdida máxima ACOTADA ($81) en
vez de "Cash-Secured Put"/"Short Call Naked" sueltos con riesgo mal representado. 17 tests
nuevos — 581/581 en verde. Antes en la sesión: Pestaña Alertas: mismo filtro de rango de fechas
que Operaciones, con agrupamiento visual por fecha nuevo ahí también — default "Hoy" a pedido
explícito del usuario, sin borrar nada del historial. `filter_by_date_range` generalizado
(antes `filter_trades_by_date_range`, específico de Operaciones) con un parámetro `date_field`
para reusarse en ambas páginas. Verificado en vivo: "Hoy" sin alertas hoy (mensaje distinto de
"sin alertas nunca"), "Todo" muestra el historial agrupado por fecha. Antes en la sesión:
filtro de rango de fechas en la Pestaña Operaciones: Hoy / Última semana / Últimos 15 días /
Último mes / Todo, manteniendo el agrupamiento visual por fecha que ya existía — verificado en
vivo con capturas de "Hoy" (solo el grupo de hoy) y "Últimos 15 días" (hoy + 28/07 juntos).
Antes en la sesión: healthcheck automático del scheduler
implementado a pedido del usuario tras la 3ra recurrencia del cuelgue en 3 días: un LaunchAgent
nuevo corre cada 5 min todo el día, detecta "colgado mudo" (proceso vivo, log sin actividad
durante horario de mercado), lo reinicia solo, corre un catch-up de operaciones reales con
ventana proporcional al tiempo perdido, y notifica con una notificación nativa de macOS
inmediata — validado en vivo de punta a punta (log fabricado como "viejo", detectó, reinició,
catch-up encontró y suprimió correctamente un roll real). Antes en la sesión: URGENTE resuelto:
scheduler colgado de nuevo —
esa 3ra vez — dejó pasar operaciones reales de hoy hasta que se detectó y reinició manualmente;
al investigar en vivo se encontró Y corrigió un bug real de duplicados en la detección de
Operaciones, causado por una carrera entre 2 procesos corriendo la detección al mismo tiempo,
con un índice UNIQUE nuevo en la base como fix de raíz. Antes en la sesión: Market Movers ahora
cubre Nasdaq y Dow Jones además de S&P 500, con pestañas para elegir cuál ver — confirmado en
vivo que `/movers/$COMPX` y `/movers/$DJI` de Schwab funcionan igual que `/movers/$SPX`. Antes
en la sesión: refinado el
"check histórico" de las alertas a pedido del usuario: el texto ya no muestra "N de M ventanas
(X.X%)" — ahora agrupa rachas de ventanas solapadas que ven la misma caída/suba sostenida en un
solo evento real, y el badge dice simplemente "el precio tocó este nivel N veces" en los
últimos ~5 años, sin porcentajes ni jerga técnica. Verificado en vivo: el ejemplo de WFC bajó
de 330 ventanas solapadas a 41 eventos distintos. Antes en la sesión (2026-07-28): "check
histórico" nuevo en las alertas: de
todas las ventanas de N días (=DTE) en los últimos ~5 años, cuántas veces el precio se movió
tanto como necesitaría moverse hoy para llegar al strike, calculado una vez al generar la
alerta y guardado (no en tiempo real), aplicado a Alertas de candidatos y Operaciones reales.
Antes en la sesión:
reordenadas las columnas del Screener a pedido explícito (las 15 "originales" primero en orden
fijo, las 6 derivadas al final); bug real de filas duplicadas en el Screener (Cash-Secured Put vs Short Put (Naked),
mismo contrato exacto) corregido de raíz en el motor + deduplicado a nivel de visualización
para el historial ya persistido; filtros de earnings/FOMC sumados al Screener; una alarma falsa
de GDX investigada y confirmada como detección correcta (el scheduler nuevo funcionaba bien, el
usuario chequeó justo antes del ciclo); selector de estrategia Naked Put/Covered Call/Ambas en
el Screener; rediseño completo de detección de Operaciones vía `/orders` de Schwab reemplazando
el diff de posiciones y resolviendo de raíz el bug de mark price, con un incidente real durante
el despliegue (60 notificaciones de WhatsApp falsas ya enviadas, documentado íntegro);
scheduler colgado diagnosticado y arreglado; bug de Market Movers corregido; BTC intentado y
pausado a pedido del usuario — ver secciones dedicadas abajo).

## En progreso ahora

Ninguno.

## Pendiente, no empezado

- **Conectar el gráfico de velas con alertas** (confirmado 2026-07-31): mostrar en el gráfico
  los niveles/strikes de las alertas activas (candidatos y operaciones reales) del símbolo, para
  ver el precio en contexto de la posición.

## Resuelto — scheduler colgado ~15h, incluyendo horario de mercado (2026-07-28)

**Recurrencia mucho más grave del hallazgo del 2026-07-27** (ver historial en git log de esta
sección): esta vez el proceso NO se limitó a estar inactivo fuera de horario — se colgó de
verdad y se quedó colgado hasta DENTRO del horario de mercado de hoy, con riesgo real de perder
operaciones. Diagnóstico confirmado (no especulación):
- **Dos procesos `run_scheduler.py` corriendo a la vez** (PID `99687`, gestionado por
  `launchd`/`com.robertoajemblat.options-income-advisor.scheduler.plist` con `KeepAlive`, y PID
  `99639`, huérfano, iniciado manualmente por fuera de `launchd` en una sesión anterior — sin
  gestión de reinicio propia).
- **Ambos colgados**: ninguno escribió una línea al log desde las 18:34 del 2026-07-27, pese a
  que el cron de `real_trade_detection` corre cada 3 min en horario de mercado y ya deberían
  haber corrido ~7 veces desde la apertura de hoy (09:00 ET) al momento de detectarlo (~09:20).
- Ambos con un socket TCP a Schwab en estado `CLOSE_WAIT` (conexión muerta nunca cerrada) —
  consistente con el log de `pmset`, que confirma que la laptop entró en sueño profundo hoy a
  las 09:14 pese al `caffeinate` corriendo. `KeepAlive` de `launchd` solo reinicia un proceso
  que MUERE, no uno que se cuelga (sigue "vivo" para macOS aunque no procese nada) — por eso no
  se auto-recuperó.
- **Confirmado con la base de datos real**: el timestamp más reciente en `position_snapshots`
  antes del fix era de anoche 21:30 (el re-poblado MANUAL del fix de rolls de la sesión
  anterior, no una corrida automática) — sin evidencia de que el scheduler haya detectado nada
  por su cuenta desde entonces.

**Fix aplicado**: matado el proceso huérfano (`99639`), reiniciado el proceso oficial vía
`launchctl kickstart -k gui/$(id -u)/com.robertoajemblat.options-income-advisor.scheduler`,
corrida manual de `job_detect_real_trades` inmediatamente después para cubrir el gap sin
esperar al próximo tick del cron (confirmado: `position_snapshots` pasó de 21:30 de ayer a
09:30:39 de hoy, 0 operaciones nuevas detectadas — no había aperturas pendientes). Cron
automático reiniciado a las 09:30:14 ET, confirmado corriendo solo de nuevo poco después.

**Riesgo pendiente sin resolver** (root cause real, no solo el síntoma): nada en el sistema
detecta un CUELGUE (vs. una muerte) del proceso — si vuelve a pasar durante horario de mercado
sin que alguien lo note, operaciones reales podrían pasar desapercibidas otra vez. Ideas no
implementadas: un healthcheck externo (ej. cron separado que verifique que
`scheduler.err.log` tuvo actividad reciente y mate+reinicie si no) o timeouts más agresivos en
los clientes `httpx` de `schwab_client.py` (hoy 15s, pero el proceso entero parece quedar
suspendido por el sistema operativo, no solo la llamada de red, así que un timeout más corto no
lo hubiera arreglado esta vez). **El riesgo se concretó de nuevo al día siguiente — ver sección
siguiente.** Sigue sin implementarse un healthcheck automático; queda como riesgo abierto.

## Resuelto — 3ra recurrencia del scheduler colgado + bug de duplicados descubierto al investigar (2026-07-29, URGENTE)

**Reporte del usuario**: operaciones reales de hoy (NVDA y Citigroup, vendidas ~10:39 ET) no
aparecían en la Pestaña Operaciones — la última visible era de ayer. Investigado con evidencia
real, no especulación:

- **Scheduler otra vez colgado** (3ra vez, mismo patrón que 2026-07-27 y 2026-07-28): el
  proceso (PID vivo desde las 20:43 de anoche) no escribió NINGUNA línea al log desde que
  arrancó, pese a que el cron de `real_trade_detection` (cada 3 min, 9-16 ET) ya debería haber
  corrido ~32 veces desde la apertura. `pmset -g log` confirmó que la laptop entró en sueño
  profundo (`Clamshell Sleep`) a las 23:31 de anoche y pasó por múltiples ciclos de
  mantenimiento/dark-wake durante toda la noche — mismo mecanismo que rompe la conexión TCP sin
  matar el proceso, documentado en el incidente anterior. **Fix inmediato**: reiniciado vía
  `launchctl kickstart -k`, corrida manual de `job_detect_real_trades` inmediatamente después
  para cubrir el gap.
- **Bug real descubierto al investigar** (no estaba buscándolo, apareció en la corrida manual):
  la primera corrida manual falló con `ReadTimeout` pidiendo órdenes de la cuenta 74257810
  (típico justo después de que la laptop despierta de un sueño profundo — DNS/conexión aún no
  totalmente restablecida). El reintento inmediato tuvo éxito, PERO coincidió en el tiempo (el
  scheduler recién reiniciado disparó su propio ciclo automático al mismo minuto) — **2
  procesos de detección corriendo a la vez**. Ambos leyeron `get_alerted_order_leg_keys` ANTES
  de que ninguno insertara, ambos concluyeron "esta orden es nueva", y ambos insertaron: NVDA y
  Citigroup quedaron duplicados (2 filas idénticas cada uno, mismo `order_id`).
  **Confirmado con Telegram**: `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` no están configurados en
  este entorno, así que no se llegó a enviar ninguna notificación duplicada — pero el bug de
  fondo (2 filas en la tabla, 2 tarjetas en la UI) era real y se hubiera notificado 2 veces con
  Telegram activo.
  **Fix de raíz** (no solo limpiar los datos): índice `UNIQUE` nuevo sobre
  `(order_id, occ_symbol)` en `real_trade_alerts` (`db.py::_migrate`, con una limpieza previa de
  duplicados existentes vía `DELETE ... WHERE id NOT IN (SELECT MIN(id) ...)` para no romper la
  creación del índice contra una base ya existente) — el chequeo en Python nunca iba a alcanzar
  contra una carrera real entre 2 procesos, hacía falta la garantía a nivel de base.
  `repository.py::insert_real_trade_alert` ahora atrapa el `IntegrityError` y devuelve `None`;
  `alerts/real_trades.py::_build_and_persist_real_trade_alert` trata `None` como "ya la detectó
  otra corrida" y NO reenvía la notificación. 7 tests nuevos (`test_repository.py`,
  `test_real_trades.py`) verifican explícitamente: 2do insert de la misma orden/pata devuelve
  `None` sin romper; una orden con 2 patas DISTINTAS (rolls, iron condors) sigue insertando
  ambas sin problema; filas viejas sin `order_id` (NULL) no chocan entre sí.
  Migración aplicada en vivo contra `data/app.db`: 2 pares duplicados limpiados (NVDA y
  Citigroup, de 11 filas a 9), índice creado, verificado sin duplicados.
- **Verificado en vivo en el navegador** (Pestaña Operaciones, sin filtro): "Hoy · 4
  operaciones" — SPXW Put + SPXW Call (straddle 0DTE detectado automáticamente por el
  scheduler ya reiniciado, con las 2 patas correctamente separadas por tener `occ_symbol`
  distinto, sin duplicar), Citigroup CSP, NVDA CSP — cada una aparece exactamente una vez.
  545+5 = 550/550 tests en verde.

**Riesgo cerrado — ver siguiente sección**: implementado el healthcheck automático pedido
explícitamente por el usuario tras esta 3ra recurrencia.

## Resuelto — healthcheck automático del scheduler (pedido explícito 2026-07-29, 3ra recurrencia)

El usuario pidió priorizar esto de inmediato: "ya son 3 veces en 3 días, no puedo estar
revisando manualmente". Diseño con 2 frentes, ambos evaluados:

**Detección + auto-reparación** (implementado por completo):
`scheduler/healthcheck.py::run_healthcheck()` — módulo separado y testeable (funciones puras
`is_stale()`/`catchup_lookback_minutes()` + una función de orquestación con
`get_scheduler_pid`/`restart_scheduler`/`notify` inyectados, mismo patrón de dependencias que
`broker: BrokerClient` en el resto del motor). Solo actúa durante horario de mercado regular
("abierto", vía `market_calendar.market_session()`) — fuera de esa ventana un log viejo no
significa nada. "Colgado mudo" = proceso vivo (`pgrep`) pero el log del scheduler no tuvo
actividad en `max(6 min, poll_interval × 3)` — con `real_trade_poll_interval_minutes=3` por
defecto, son 9 min de margen antes de actuar (evita falsos positivos por una llamada lenta
puntual a Schwab). Al detectarlo: `launchctl kickstart -k` reinicia el scheduler, y corre un
catch-up de `detect_and_alert_real_trades` con una ventana proporcional al tiempo perdido
(gap + 5 min de margen, piso de 15, tope de 180 para no repetir el timeout real ya documentado
pidiendo demasiada historia de órdenes de una sola vez). Notifica con
`osascript display notification` (inmediata, nativa de macOS, no depende de que Telegram esté
configurado — confirmado que NO lo está en este entorno) + intento de Telegram si algún día se
configura (`notifier.send_text`, no-op silencioso si no hay token). Corre cada 5 min todo el
día vía un LaunchAgent NUEVO y SEPARADO
(`~/Library/LaunchAgents/com.robertoajemblat.options-income-advisor.healthcheck.plist`,
`StartInterval=300`, sin `KeepAlive` porque es un script de un solo shot que termina cada vez,
no un proceso residente — un cuelgue DEL healthcheck mismo no puede repetir el problema que
está vigilando). `alerts/real_trades.py::detect_and_alert_real_trades` ganó un parámetro
opcional `lookback_minutes` (default preserva el comportamiento normal del cron) para que el
catch-up pueda pedir una ventana más ancha que los 15 min de siempre.
11 tests nuevos (`test_healthcheck.py`) cubren: umbral de "colgado" con piso para intervalos
chicos, escalado de la ventana de catch-up, no-op fuera de horario de mercado, no-op con log
fresco, reinicio sin catch-up cuando el proceso ni siquiera está vivo, reinicio + catch-up
vacío, y reinicio + catch-up que SÍ encuentra una operación perdida (2 notificaciones
distintas). 561/561 en verde.
**Validado en vivo de punta a punta** (no solo con tests): corrida manual confirmó el camino
sano (log fresco → no-op, sin acción); luego se fabricó un log "viejo" (20 min, vía `touch -t`
sobre el log real) y se corrió de nuevo — detectó el cuelgue, reinició el scheduler real
(PID nuevo confirmado), y el catch-up real contra Schwab encontró y suprimió correctamente un
roll genuino de la cuenta (pata OPENING+CLOSING en la misma orden, sin alerta — comportamiento
esperado de Fase 1). LaunchAgent registrado con `launchctl bootstrap`, corrida de `RunAtLoad`
confirmada sin acción (sistema ya sano en ese momento).

**Prevención de sueño** (evaluado, decisión final: no aplicar por ahora): `sudo pmset -c
disablesleep 1` evitaría el sueño profundo mientras esté enchufada (root cause de las 3
recurrencias), pero es un cambio de configuración del sistema — fuera de lo que puedo ejecutar
yo mismo, y requiere una contraseña tipeada en una Terminal real (no vía `!` en el chat, que no
soporta prompts interactivos). Se le pasó el comando exacto al usuario varias veces; decidió no
aplicarlo por ahora y confiar en el healthcheck (2026-07-29). Queda documentado acá por si se
quiere retomar más adelante — no es una dependencia para que el sistema se recupere solo, el
healthcheck ya cubre el caso real.

## Alcance confirmado — Pestaña Operaciones, Fase 1 (aclarado por el usuario 2026-07-28)

**Solo APERTURAS genuinas generan alerta.**
- **Cierres** — nunca generan alerta (diseño original, sin cambios).
- **Rolls** (cerrar una opción y abrir otra distinta del mismo subyacente, típicamente una sola
  orden combinada de Schwab) — nunca generan alerta. **La "Fase 2" que quedaba pendiente
  (confirmar rolls vía `/orders` en vez de una heurística de ventana temporal) ya está
  implementada** — ver "Rediseño de detección vía /orders" abajo: una orden con una pata
  OPENING y una CLOSING es un roll DETERMINÍSTICO por su propia composición, no una inferencia
  entre corridas. La heurística vieja (y su falso negativo conocido) quedó completamente
  reemplazada.

## Investigación pendiente — sin prioridad inmediata

- **Options Time & Sales** (pedido 2026-07-27, idea del mismo ejemplo de Barchart que el
  screener de arriba): detalle de cada operación individual ejecutada en una opción (precio,
  tamaño, hora, si fue en bid/ask) — no datos agregados del día. **Investigado antes de
  prometer nada** (búsqueda web, sin acceso a la documentación oficial completa de Schwab):
  el Trader API de Schwab SÍ tiene una capa de streaming separada (WebSocket, autenticación
  vía un endpoint REST distinto — `get_user_principals` — no las mismas API keys REST que usa
  hoy `schwab_client.py`) con un servicio nombrado `TIMESALE_OPTIONS` mencionado en fuentes de
  terceros como agregadores tipo Grokipedia. **Pero** la documentación más detallada que
  encontré (`schwab-py`, librería no oficial ampliamente usada) NO confirma `TIMESALE_OPTIONS`
  como funcionando — solo lista Level One Quotes, Level Two Order Book, OHLCV Charts, Screener
  y Account Activity como streams confirmados, y advierte explícitamente que "algunos streams
  nunca funcionaron, aunque la documentación (vieja, de la API predecesora TDA) los mencionaba".
  **Conclusión honesta**: viable en principio, pero sin confirmar en la práctica. Complejidad
  real independientemente de si el stream existe: **alta** — es una arquitectura nueva
  (cliente WebSocket persistente con su propio manejo de reconexión/heartbeat) distinta a las
  consultas REST periódicas que usa todo el resto de la app (`scheduler/jobs.py` corre en
  cron, no mantiene conexiones abiertas) — no es sumar un endpoint, es sumar un subsistema.
  **Antes de programar nada**: hacer un spike chico con las credenciales reales para confirmar
  si `TIMESALE_OPTIONS` responde algo en la práctica, antes de invertir en la arquitectura de
  streaming completa. Sin prioridad asignada, el usuario lo pidió explícitamente "sin
  prioridad inmediata".

## Bloqueado / diferido — decisión de producto o diseño pendiente

- **SPX 0DTE**: evaluado 2026-07-26, **no se integra al motor existente**. El buscador de
  expiraciones (`strategy/candidates.py:15-17`) sólo busca ventanas de 25-50 días — 0 DTE nunca
  entra en ese rango. El IV Rank (`iv_rank.min_sessions_for_real_iv: 20` en `settings.yaml`) es
  un percentil sobre ~20-252 sesiones diarias, sin sentido para una decisión intradía. Merece
  ser un módulo separado (cadencia de polling propia, indicadores intradía propios — rango de
  apertura/VWAP/movimiento esperado del día en vez de RSI/SMA/IV Rank diario —, selección de
  strike y métrica de retorno propias). Diseño pendiente, no programar sin conversación previa.
- **VIX como subyacente del motor**: diferido. VIX se basa en futuros, no en spot — el fallback
  de Black-Scholes del motor calcularía griegos con el precio spot en vez del futuro relevante,
  lo cual da griegos incorrectos si hay contango/backwardation (frecuente en VIX). Falta decidir
  cómo tratar esto antes de sumarlo.
- **BTC real (spot)**: **pausado explícitamente por el usuario 2026-07-28** — "no es
  importante ahora mismo, el ETF apalancado que ya muestra Schwab es suficiente por ahora".
  Decisión de producto (usar Finnhub `BINANCE:BTCUSDT`) sigue confirmada del 2026-07-26 para si
  se retoma más adelante, pero el intento de implementación de esta sesión (`get_crypto_quote`
  en `finnhub_client.py`, `cached_btc_quote` en `components.py`, cableado en el ticker de
  General) se **revirtió por completo** — llegó a romper la app en vivo (`ImportError` por un
  problema de reload de Streamlit) antes de terminar de verificarse, y el usuario pausó la
  tarea en ese momento. 0 rastro de código en el repo; si se retoma, empezar de cero.

## Terminado y verificado hoy (ver NOTES.md para el detalle técnico completo de cada uno)

1. Set de íconos del texto de WhatsApp — unificado a estilo minimalista monocromo (elegido por
   el usuario entre 4 opciones presentadas).
2. Watchlist real de thinkorswim (96 símbolos) integrada en `/escaneo` y en `/watchlist`.
3. Selector de perfil de riesgo en Alertas — evolucionó a filtro puro combinable con estrategia.
4. Cada corrida evalúa los 3 perfiles de riesgo a la vez (antes: solo el perfil activo).
5. % de cobertura por alerta (cuánto tiene que moverse el subyacente para llegar al strike
   vendido) — destacado en la tarjeta, con badges separados para Iron Condor (baja/alza).
6. Portafolio Entrega 3: análisis de exposición narrado con IA (concentración + clusters de
   earnings simultáneos).
7. Advertencia de liquidez (spread bid/ask ancho) en la pata vendida.
8. Advertencia de riesgo de asignación anticipada por ex-dividendo en calls vendidas (con un
   bug real de Schwab encontrado y corregido en el camino).
9. Texto de Configuración actualizado para reflejar que el selector de perfil ya no decide qué
   se genera.
10. Confirmado en vivo que el endpoint de market movers de Schwab funciona (sin construir UI
    todavía — decisión de producto pendiente sobre el caso de uso).
11. Refinamiento de selección de strikes por perfil (cobertura mínima + soporte técnico vía
    SMA8/SMA20) — las 3 preguntas de diseño confirmadas por el usuario antes de implementar.
12. Rendimiento anualizado sobre capital en riesgo + proyección de cierre anticipado
    (30%/50%/100%, con disclaimer de que asume precio/IV constantes).
13. Calculadora de interés compuesto en Configuración — prellenada con el promedio real de
    rendimiento anualizado de las alertas (no un número arbitrario), tabla + gráfico año a año,
    disclaimer de que es proyección. Verificado en navegador (screenshot) y con 11 tests
    (`test_compound_interest.py`, `test_repository.py`) — 279/279 tests del repo en verde.
14. Buscador de noticias por símbolo libre en Noticias (cotización + noticias de cualquier
    símbolo, cacheado 5 min) + fix del botón ☰ para reabrir el sidebar. Verificado en navegador
    (NFLX en vivo) y con 3 tests nuevos — 282/282 tests del repo en verde.
15. RUT + NDX sumados al motor existente (`config/symbols.yaml`, plazos normales) +
    `index_quote_symbol()` en `broker/models.py` para traducir el root OCC "pelado" al símbolo
    `$`-prefijado que Schwab exige al cotizar/pedir cadena (usado en Portafolio real para
    subyacentes de índice). Badge de riesgo real en dólares (⚠) cuando la pérdida máxima de una
    sola posición supera el 25%/100% del capital configurado en Configuración — en la alerta de
    WhatsApp y en la tarjeta del dashboard. Verificado con 13 tests nuevos —
    295/295 tests del repo en verde.
16. **Bug real encontrado por el usuario y corregido**: la alerta de Collar no avisaba que hace
    falta tener/comprar 100 acciones del subyacente (sin ellas la call vendida es un Call
    desnudo, riesgo no acotado) — la advertencia solo existía para Covered Call. Se unificó en
    `share_requirement_line()` (`alerts/formatting.py`), usada por el texto de WhatsApp y la
    tarjeta del dashboard, y se sumó precio actual + costo total de las 100 acciones al texto
    ("Requiere 100 acciones de X a $Y (~$Z)"). Confirmado con un ejemplo real (AAPL, cadena
    mock): el cálculo de beneficio/pérdida máxima YA incluía correctamente el valor de las
    acciones (Collar cap en el put protector, $814 de pérdida máxima vs. Covered Call sin
    protección, $27,492 — casi el costo total de las acciones menos la prima); el bug era solo
    de texto, no de números. 7 tests nuevos — 302/302 tests del repo en verde.
17. **Rediseño de página principal estilo CNBC**: indicador de sesión de mercado (pre-market/
    abierto/after-hours/cerrado, punto pulsante verde/ámbar/gris según sesión — usa
    `market_session()` de `scheduler/market_calendar.py`), carrusel de cotizaciones (cinta
    horizontal con scroll infinito en CSS puro, verde/rojo según variación, símbolos de la
    watchlist), y sección Market Movers (ganadoras/perdedoras de `$SPX` vía el endpoint
    `/movers` de Schwab confirmado en vivo el 2026-07-25). Página principal renombrada de
    "app" a "General" en el menú lateral (migrado a `st.navigation`/`st.Page` en `app.py`, las
    demás páginas de `pages/` quedaron sin tocar — Streamlit no permite sobreescribir esa
    etiqueta con la detección automática de carpeta). Verificado en navegador en vivo (modo
    `schwab` real, mercado cerrado: badge gris correcto, ticker con precios reales, movers
    vacío con el mensaje esperado) y con 21 tests nuevos (`Mover`/`get_movers` en mock y
    Schwab, campos `net_change`/`net_change_pct`/`post_market_change_pct` de `Quote`,
    `market_session()`) — 323/323 tests del repo en verde.
18. **Simulador de inflación / depreciación del dinero**, sumado a la página "Configuración"
    (renombrada a "Perfil y Simulación" para reflejar que ya no es solo ajustes) —
    `dashboard/inflation_simulator.py::project_inflation_scenarios()` proyecta 3 escenarios
    (sin invertir / banco / inversión alternativa) a 5 años, deflactando con la fórmula exacta
    `real = nominal / (1+inflación)^años` (no la aproximación nominal-inflación). La tasa de
    inflación se prellena SIEMPRE con el CPI interanual real de FRED, sin edición manual del
    dato de origen — para eso `market_context/fred_client.py::_latest_observation()` ahora
    devuelve también la fecha que FRED asocia al dato (el mes que mide, no la fecha del job),
    guardada en la nueva columna `macro_snapshot.cpi_yoy_date` (migración vía
    `storage/db.py::_NEW_COLUMNS_BY_TABLE`) y mostrada en la UI ("CPI interanual de FRED: X%
    (dato de mes año)"). Verificado en vivo contra la API real de FRED: el dato de CPI
    (`CPALTT01USM659N`) resultó ser de abril 2025 pese a correr en julio 2026 — confirma que
    mostrar la fecha del dato (y no solo el valor) es necesario, esa serie de FRED se publica
    con varios meses de rezago. 8 tests nuevos (`test_inflation_simulator.py` + cobertura de
    `cpi_yoy_date` en `test_fred_client.py`/`test_repository.py`) — 331/331 tests en verde.
19. **Pestaña "Operaciones" — réplica automática de operaciones reales** (pedido 2026-07-25,
    trabajo autónomo overnight 2026-07-26/27, terminado y verificado). Detecta ventas nuevas de
    opciones en la cuenta Schwab real (diff de `get_all_positions()` contra el snapshot de la
    corrida anterior, tabla `position_snapshots` reemplazada por completo cada corrida — no
    upsert, para que una posición cerrada y reabierta se detecte como operación nueva) y genera
    una alerta con el mismo formato completo que las de candidatos (P&L, breakeven, POP,
    cobertura, noticias, comentario del narrador) aplicada a la posición YA ejecutada. Piezas
    nuevas: `strategy/candidates.py::find_contract/build_from_contract` (reconstruye el
    contrato exacto desde strike/vencimiento ya conocidos, no elegidos por delta),
    `alerts/narrator.py::build_real_trade_context/narrate_real_trade` (prompt y fallback
    propios, sin conviction_score — nada se puntuó), `formatting.format_alert_message` con
    header parametrizable, `alerts/real_trades.py` (el módulo de detección/orquestación,
    equivalente a `alerts/engine.py` para operaciones ya ejecutadas), tabla `real_trade_alerts`,
    página `pages/9_operaciones.py`. Cash-Secured Put siempre para puts vendidos (el motor no
    trackea efectivo/margen); Covered Call vs. Call desnuda para calls vendidas según
    `share_positions` real. **Verificado con una posición REAL de la cuenta** (no fixture): put
    de TSLA strike $340 vence 21/8, detectada sembrando el resto de las ~63 posiciones cortas
    existentes como baseline (para no generar 63 alertas de golpe en el primer run — comportamiento
    correcto también para USO en producción real) — corrió contra Schwab en vivo (cotización +
    cadena de opciones reales), Finnhub (noticias reales) y Claude (narración real, no fallback),
    P&L calculado en $3,622.50 de beneficio máximo / $30,377.50 de pérdida máxima, badge de
    riesgo real "60% de tu capital configurado", correctamente avisó que la reunión FOMC del
    29/7 cae dentro del vencimiento de la posición. Filtro de símbolo de la página corregido en
    el mismo verificado (bug encontrado ahí mismo: usaba la watchlist de 15 símbolos del motor
    de sugerencias en vez de los símbolos que realmente aparecen en `real_trade_alerts` — la
    cuenta real opera SLV/SOFI/EWY/AA/DKNG/etc., ninguno en esa watchlist). 42 tests nuevos
    (storage, candidates, formatting, narrator, notifier, real_trades, jobs) — 365/365 en verde.
    De paso, mejora aplicada (no confirmada 100%, ver sección de arriba) al botón ☰ del sidebar
    por bug reportado por el usuario esa misma noche.
20. **Bug real encontrado y corregido: Market Movers mostraba +0.00%/$0.00 en todos los
    símbolos** (reportado por el usuario 2026-07-27). `_parse_mover()` (`broker/schwab_client.py`)
    leía campos `last`/`change`/`direction` que Schwab NO devuelve en `/movers` — el shape real
    en vivo es `lastPrice`/`netChange`/`netPercentChange` (sin `direction`), confirmado pidiendo
    el endpoint real con el mercado abierto. Todo caía en los defaults (0.0/0.0/"up") desde que
    se construyó la sección (2026-07-25), nunca detectado porque esa verificación cayó con el
    mercado CERRADO (`screeners: []`, el parseo de una fila real nunca se ejercitó) y el único
    test usaba un payload fabricado con los nombres viejos. `netPercentChange` viene como
    fracción en este endpoint (a diferencia de `/quotes`, donde ya viene en %) — confirmado con
    la matemática real de un ítem (`netChange=-10.6` sobre `lastPrice=196.24` ≈ `-0.0512`).
    Corregido y verificado en navegador en vivo con datos reales (NVDA -5.08%, AAPL +0.71%,
    etc.) — 1 test actualizado con el shape real capturado, 369/369 en verde.
21. **Detección de operaciones reales separada a un cron propio más seguido** (pedido
    2026-07-27: verla reflejada casi en tiempo real, no esperar 30 min). Nuevo
    `job_detect_real_trades()` (liviano: solo diffea posiciones, sin indicadores/scoring/
    narración de candidatos) con cadencia propia
    (`settings.scheduler.real_trade_poll_interval_minutes`, default 3 min), registrado como job
    separado (`real_trade_detection`) en `scheduler/runner.py`, independiente de
    `periodic_poll`. Desplegado en el proceso real del scheduler (`scripts/run_scheduler.py`,
    reiniciado para tomar el cambio — se encontró corriendo desde el domingo con código viejo).
    5 tests nuevos (incluye verificación del cron real vía introspección del trigger de
    APScheduler) — 369/369 en verde.
22. **3 funcionalidades de Fed/FRED** (pedido 2026-07-26, sin las 3 preguntas de diseño
    resueltas de antemano — interpretación propia documentada acá porque nadie estaba
    disponible para confirmarla, revisar si no calza con lo esperado):
    1. **Bloqueo de días de riesgo CPI/NFP**: `settings.strategy.block_new_candidates_on_high_risk_days`
       (default `true`, configurable) — no genera candidatos NUEVOS en días donde sale CPI, NFP
       o hay reunión FOMC (reusa la clasificación de riesgo que ya usaba la página Eventos de
       riesgo, `alerts/risk_calendar.py`). Nunca afecta alertas ni posiciones ya existentes.
    2. **Semáforo de volatilidad**: badge basado en el spot de VIX (bandas estándar de mercado:
       <15 baja, 15-25 normal, >25 alta), junto al indicador de sesión de mercado en la página
       General. Solo lectura para mostrar contexto — distinto de la decisión ya tomada de NO
       integrar VIX como subyacente del motor de estrategias (ver "Bloqueado" más abajo, esa
       restricción es sobre griegos/futuros, no aplica a mostrar una cotización). Verificado en
       vivo (VIX 19.23 · Volatilidad normal).
    3. **Alertas proactivas**: `build_proactive_risk_warnings()` avisa 2 y 1 día ANTES de un
       evento de riesgo alto (no solo el día de), insertado como notificación de la campanita
       🔔 en el digest pre-apertura, con dedup para no repetir el mismo aviso si el job corre
       más de una vez el mismo día.
    28 tests nuevos — 390/390 en verde.
23. **Calendario de earnings con búsqueda por rango de fechas** en Eventos de riesgo (pedido
    2026-07-26): selector Desde/Hasta con atajos (Esta semana/Próxima semana/Próximos 30 días)
    sobre la watchlist (sin llamadas nuevas, reusa `repo.get_latest_next_earnings_date`) +
    checkbox "Incluir universo amplio" que trae earnings de TODAS las empresas en el rango con
    una sola llamada a Finnhub (`finnhub_client.get_earnings_calendar_range`, sin filtro de
    symbol) en vez de una consulta por símbolo. Columna "En mi watchlist" distingue el origen.
    Verificado en navegador en vivo (6 earnings reales de la watchlist, 1235 con universo
    amplio). 15 tests nuevos — 394/394 en verde. **Nota**: el usuario reportó después que
    AMD/MSFT no aparecían — root cause y fix en el punto #25 de abajo.
24. **Bug real encontrado y corregido: Operaciones no detectaba operaciones reales de hoy**
    (reportado 2026-07-27 noche). Root cause encontrado con el endpoint real `/orders` de
    Schwab (no expuesto antes en el código, usado acá puntualmente para diagnosticar): el
    usuario abrió 3 posiciones nuevas esa mañana entre las 13:00-13:04 EDT (AMD
    260904P00325000, EWY 260904P00130000, y un roll de SOFI de Aug21→Sep18 $21P) — pero el
    script manual de siembra del baseline de la noche anterior (para la demo de TSLA, corrido a
    las ~13:41 EDT) capturó el estado de la cuenta EN ESE MOMENTO como "ya existente",
    tragándose silenciosamente esas 3 operaciones reales sin alertar. El AMD ya se había
    cerrado para cuando se investigó (15:12 EDT, buy-to-close) — sin alerta retroactiva para
    esa (el usuario confirmó que por ahora solo quiere APERTURAS, no cierres). EWY y SOFI
    seguían abiertas: generadas manualmente con los datos reales del fill de Schwab
    (`_build_and_persist_real_trade_alert` llamado directo, `trade_ts` corregido al horario
    real de cada fill) y verificadas en navegador — P&L real, POP real, narración real de
    Claude. Confirmado con una corrida de detección posterior: 0 pendientes. Fue un efecto
    secundario puntual del script manual de la noche anterior, no un bug de la lógica de
    detección en sí — sin más siembras manuales no debería repetirse (ver hallazgo aparte del
    scheduler más arriba, sin resolver).
25. **Bug real encontrado y corregido: AMD/MSFT no aparecían en Eventos de riesgo** (reportado
    2026-07-27 noche). Root cause: la página usaba solo `get_symbols()` (los 15 símbolos fijos)
    para armar `earnings_by_symbol`, en vez de la unión con la watchlist REAL de thinkorswim
    (~96 símbolos) que ya usa Escaneo — AMD no está en la lista corta, nunca aparecía sin
    marcar "universo amplio" aunque ya tuviera el dato en la DB. MSFT sí estaba en la lista
    corta y ya funcionaba (confirmado con datos reales antes de tocar nada). Fix: unión de
    `get_symbols()` + `load_priority_watchlist_symbols()`, mismo patrón que Escaneo. Verificado
    en navegador en vivo: AMD (2026-08-04, visible con "Próximos 30 días") y MSFT (2026-07-29)
    ambos correctos, marcados "En mi watchlist".
26. **Eventos de riesgo separado del Calendario de earnings** (pedido 2026-07-27): la sección de
    arriba ahora muestra SOLO eventos de la Fed (FOMC/CPI/empleo, niveles alto/medio/bajo ya
    existentes) — earnings de símbolos individuales quedan exclusivamente en el Calendario de
    earnings de más abajo. Cambio mínimo: `build_risk_calendar(upcoming_events, {}, ...)` en vez
    de pasar `earnings_by_symbol` — esa función ya soportaba earnings vacío sin cambios de
    código. Verificado en navegador en vivo: la lista de arriba quedó 100% FOMC/PBI/Nonfarm
    Payrolls/CPI, sin earnings mezclados.
27. **Watchlist: agrega precio actual y % de cambio del día** (pedido 2026-07-27) — vía
    cotización EN VIVO (`cached_quotes`, 60s cache, mismo mecanismo que el ticker de la página
    General), distinto del "Precio (snapshot)" ya existente (renombrado para desambiguar, es el
    precio DE CUANDO CORRIÓ el último análisis, el que hay que mirar para interpretar el RSI/SMA
    de esa misma fila). Verificado en navegador en vivo con datos reales (AMD -5.84%, coincide
    con la caída real observada esa misma noche).

    (24-27: 0 tests nuevos combinados — todos son cableado de datos ya testeados o cambios de
    página sin lógica nueva; verificados en navegador con datos reales de producción en cada
    caso. 394/394 tests del repo en verde en todo momento.)
28. **Vista tabla en Escaneo**: `_leg_dict()` (`payoff.py`) ahora copia
    `open_interest`/`volume` de cada `OptionContract` (antes se perdían al persistir en
    `legs_json`, aunque el broker ya los devolvía). Nuevo
    `repo.get_recent_single_leg_candidates()` (candidatos de las 4 estrategias de una sola pata
    — Cash-Secured Put/Short Put/Covered Call/Short Call — con IV Rank ya unido vía LEFT JOIN a
    `indicator_snapshots`) y `dashboard/scanner_table.py::build_scanner_rows()` (lógica pura,
    sin Streamlit) arman la fila plana: Symbol/Estrategia/Price/Exp Date/Strike/Moneyness(%)/
    Bid/Breakeven/%BE/Volume/Open Interest/IV Rank/Delta/Return(%)/Rendimiento Anualizado/POP.
    Moneyness y %BE reusan el mismo sentido que `compute_coverage` (positivo = OTM, a favor del
    vendedor); Return(%) es el retorno del PERÍODO (prima/pérdida máxima), distinto del
    Rendimiento Anualizado ya existente. Tabla nueva al final de Escaneo vía `st.dataframe`,
    ordenable nativamente por columna (sin JS adicional). Verificado en navegador en vivo: 412
    candidatos reales, orden por IV Rank confirmado funcionando (flecha + valores ascendentes);
    Volume/Open Interest en "None" para candidatos viejos (de antes del fix) pero confirmado
    por separado con datos reales de Schwab que un candidato nuevo sí los trae
    (open_interest=16305, volume=1726 en un put real de AAPL) — se van a ir poblando con cada
    análisis nuevo. 17 tests nuevos (payoff, repository, scanner_table) — 408/408 en verde.
    **Confirmado el 2026-07-28 a pedido del usuario**: el orden por columna de `st.dataframe`
    es nativo de Streamlit (glide-data-grid), sin JS adicional — primer clic en un encabezado
    ordena ascendente (flecha ↑), segundo clic en el MISMO encabezado alterna a descendente
    (flecha ↓), igual que cualquier spreadsheet. Verificado en navegador con capturas antes/
    clic 1/clic 2 sobre la columna Moneyness (%) con datos reales.
29. **Pestaña "Screener" — buscador de opciones con filtros ajustables** (pedido 2026-07-27,
    completado 2026-07-28). Nueva `payoff.py::probability_otm()` — distinta del POP ya
    calculado (mide contra el STRIKE, no el breakeven — corrección a la auditoría del pedido
    original, que asumía que el POP alcanzaba). `Quote.instrument_type` ("stock"/"etf"/
    "index") vía `assetMainType`/`assetSubType`, que Schwab YA devuelve en la MISMA respuesta
    de `/quotes` que ya se pedía — no hizo falta `/instruments` ni ninguna llamada nueva
    (corrección a la auditoría: el usuario creía que ya estaba clasificado, no lo estaba, pero
    resultó más simple de lo estimado). Nuevo `dashboard/screener_filters.py` (lógica pura):
    clasificadores de balde para Volumen/Open Interest/Moneyness/Delta (umbrales documentados
    como primer paso, no percentiles calibrados — ajustables) + `apply_filters()` (AND de
    todos los criterios activos). Página nueva `pages/10_screener.py`: sliders (DTE/Strike/
    Probabilidad OTM mínima) + multiselects (Delta/Moneyness/Instrumento/Volumen/Open
    Interest) sobre los mismos candidatos de la Vista tabla de Escaneo. Verificado en
    navegador en vivo: 412 candidatos reales, filtro Delta "Muy alto" da 0 resultados
    (correcto, el motor apunta a deltas bajos) con el mensaje de vacío bien renderizado,
    filtro "Bajo (0-0.25)" narrowea correctamente a 269 candidatos con deltas 0.11-0.13
    visibles, Instrumento mostrando "stock" real. 32 tests nuevos (payoff, schwab_client,
    scanner_table, screener_filters) — 445/445 en verde. **Pedido aparte cumplido**: barrida
    completa de referencias a "Barchart" (código/comentarios/UI) — era solo inspiración de
    diseño del usuario, no debía aparecer en el producto; 0 ocurrencias confirmadas con grep.
30. **Ajustes estéticos de la página General** (pedido 2026-07-28): título nuevo "Stock Market
    Overview" (antes "Options Income Advisor — Fase 1"), sin subtítulo. Quitado el bloque de 3
    métricas (Modo de broker/Símbolos monitoreados/Umbral) y el bloque de texto de navegación
    final (lista de las 8 páginas) — layout reorganizado sin esos huecos: header + badges de
    sesión/volatilidad, ticker, aviso de modo mock (si aplica), botón de análisis, y los 3
    paneles de datos (Market Movers, Portafolio, Contexto macro) con separadores consistentes.
    Verificado en navegador en vivo — termina limpio después del panel macro, sin texto
    colgando. 445/445 tests en verde (cambio puramente de presentación, sin tests nuevos).
31. **Simulador de escenarios en Portafolio real** (pedido 2026-07-26): nueva
    `portfolio_analysis.py::scenario_price()` (alcista/bajista/neutral + % uniforme) reusa el
    motor ya existente (`effective_projected_pnl_at_date` con `target_date=hoy`, sin
    decaimiento de tiempo) — mismo patrón que "Proyección a una fecha específica", pidiendo
    cadenas de opciones en vivo para repricear con Black-Scholes. Total proyectado vs. hoy +
    tabla por posición + disclaimer de simplificación. Verificado en navegador en vivo contra
    la cuenta real: escenario alcista 10% dio -$29,251.25 (-$1,535.62 vs. hoy) — matemáticamente
    correcto para esta cartera con calls Y puts vendidos mezclados (los puts vendidos ganan
    valor al subir el precio, el único put comprado pierde, confirmado línea por línea). 5 tests
    nuevos — 450/450 en verde.
32. **Bug real corregido: Screener con Volumen/Open Interest sin aviso de datos faltantes**
    (reportado 2026-07-28: "muestra 0 resultados incluso quitando todos los filtros
    restrictivos"). Investigado y confirmado que NO era un bug de `apply_filters` (sin filtros
    da 412/412) — Volume/Open Interest recién empezaron a persistirse esa misma noche, así que
    HOY 0 de 412 candidatos existentes tienen ese dato; cualquier selección en esos 2 filtros
    garantizaba 0 resultados sin explicación. Fix: caption de aviso junto a cada filtro
    ("Solo X/N candidatos tienen este dato") + el mensaje de 0 resultados ahora señala
    específicamente cuál filtro sin datos es la causa probable. Verificado en navegador en
    vivo. 450/450 tests en verde (cambio de UI/mensajería).
33. **Bug real corregido: rolls generaban alerta indebida en Pestaña Operaciones** — ver detalle
    completo en "Alcance confirmado" arriba. `PositionSnapshot` ahora guarda `underlying_symbol`;
    antes de alertar una apertura nueva, se verifica si algún símbolo del MISMO subyacente
    (misma cuenta) se cerró del todo en la MISMA corrida — si es así, se trata como roll y se
    suprime. Confirmado con un caso real: el roll de SOFI (Aug21→Sep18 $21P) de la noche
    anterior había generado una alerta indebida — eliminada retroactivamente de la DB
    (TSLA/EWY, aperturas genuinas, quedan). `position_snapshots` re-poblado con
    `underlying_symbol` para las 62 posiciones cortas reales actuales. 9 tests nuevos
    (real_trades, repository) — 456/456 en verde.
34. **Bug real corregido: Market Movers mostraba las MISMAS empresas en Ganadoras Y Perdedoras
    a la vez** (reportado 2026-07-28, con ejemplos reales: NVDA/INTC/T/F/MU/AAPL/TSLA/SMCI en
    ambas columnas). Root cause confirmado pidiendo el endpoint real en vivo con las dos
    direcciones: Schwab `/movers/$SPX` devuelve el MISMO set de símbolos (los de más volumen)
    sin importar si se pide `sort=PERCENT_CHANGE_UP` o `PERCENT_CHANGE_DOWN` — confiar en el
    `sort` del broker para separar ganadoras/perdedoras producía la lista duplicada en ambas
    columnas. Fix: `dashboard/components.py::split_gainers_losers()` — un solo pedido
    (`sort="VOLUME"`) y la separación se hace del lado nuestro por el signo real de
    `change_pct` (positivo/negativo, ordenados por magnitud), eliminando además la llamada
    duplicada a la API. Verificado en navegador en vivo con datos reales de mercado abierto:
    Ganadoras (KO +2.88%, MDT +2.65%, AAPL +1.21%) y Perdedoras (GLW -13.79%, MU -6.85%, INTC
    -5.44%, PLTR, SMCI, TSLA, NVDA) sin overlap y sin ceros. 6 tests nuevos
    (`test_components.py`) — 466/466 en verde.
35. **Bug real encontrado y corregido: Operaciones calculaba prima/breakeven/riesgo máximo con
    el MARK price actual de la cadena en vivo, no con el fill REAL de la operación** (reportado
    2026-07-28 con una posición real de HOOD Cash-Secured Put — verificación matemática exacta
    del usuario: breakeven correcto $71.85 vs. $72.13 mostrado, prima $630 vs. $574, riesgo
    máximo $14,370 vs. $14,426). Root cause: `strategy/payoff.py::_net_premium` siempre usaba
    `leg.contract.mid_price` (bid/ask de la cadena pedida al momento del análisis), aunque
    `alerts/real_trades.py` YA tenía el fill real disponible en `position.average_price`
    (Schwab lo reporta por posición) — nunca se lo pasaba al motor de payoff. Presente desde el
    lanzamiento de la Pestaña Operaciones (2026-07-25/26), afectaba las 4 alertas reales
    generadas hasta ahora, no solo HOOD.
    Fix: `strategy/candidates.py::Leg` suma un campo `override_premium` (último, con default
    `None` — no rompe ninguna otra estrategia del motor de candidatos, que sigue usando
    `mid_price` porque ahí sí es una operación hipotética); `build_from_contract(...,
    entry_price=...)` lo setea; `payoff.py::_leg_premium()` prioriza `override_premium` sobre
    `mid_price` en `_net_premium` y en el campo `"premium"` de `_leg_dict`.
    `alerts/real_trades.py` pasa `entry_price=position.average_price`. Importante: `bid`/`ask`
    del contrato NO se tocan — siguen siendo el spread real de mercado, correcto para
    `assess_liquidity` (advertir sobre spreads anchos es una pregunta de "¿es operable ahora?",
    distinta de "¿qué pagué?").
    **Límite conocido, resuelto del todo por el ítem #36**: en el momento de este fix, si Schwab
    reportaba un `average_price` BLENDEADO (posición con varios lotes del mismo contrato
    comprados/vendidos en momentos distintos) el precio usado era el promedio de TODOS los lotes,
    no el fill exacto del lote incremental — mejor que el mark price, pero no perfecto. El
    rediseño vía `/orders` (#36) elimina esta limitación por completo: cada orden ya trae su
    fill exacto, sin promediar nada.
    Verificado con la posición real de HOOD: los 3 números del fix coinciden EXACTO con el
    cálculo manual del usuario. Las 4 alertas reales históricas corregidas retroactivamente en
    la DB (HOOD, TSLA, EWY x2 — una de ellas con fill real distinto al promedio actual de la
    cuenta, corregida con recálculo matemático directo en vez de repedir datos en vivo, para no
    aplicarle el promedio blendeado ACTUAL a un fill que ya no lo representa). Verificado en
    navegador en vivo: las 4 tarjetas muestran números y comentario consistentes. 3 tests
    nuevos (`test_payoff.py`, `test_candidates.py`, `test_real_trades.py`) — 465/465 en verde.
36. **Rediseño completo de detección de Operaciones: de diffear posiciones a leer órdenes
    LLENADAS vía `/orders`** (pedido explícito 2026-07-28, tras evaluar complejidad primero).
    Reemplaza TODO el mecanismo de la Fase 1 (tabla `position_snapshots`, diff de
    posiciones/promedio blendeado, heurística de roll por ventana temporal) — confirmado en
    vivo probando el endpoint real contra la cuenta: cada orden trae, por pata,
    `instruction`/`positionEffect` (`SELL_TO_OPEN`/`OPENING` vs. `BUY_TO_CLOSE`/`CLOSING`) y el
    fill EXACTO (`orderActivityCollection[].executionLegs[].price`) de ESA orden puntual — sin
    promediar con otras aperturas del mismo contrato en momentos distintos. Un roll es una sola
    orden con una pata OPENING y una CLOSING (confirmado con el roll real de SOFI de días
    atrás: `complexOrderStrategyType: "CALENDAR"`), detectable con certeza por la composición
    de la orden — ya no hace falta inferir nada entre corridas.
    Piezas nuevas: `broker/models.py::FilledOrder/FilledOrderLeg` + `parse_occ_option_symbol`
    (movido desde `schwab_client.py`, formato de la industria, ahora compartido);
    `SchwabBrokerClient.get_recent_filled_orders(since)` (ventana angosta, ~15 min — pedir
    varios días de una vez da timeout, confirmado pidiendo 3 días); `MockBrokerClient` devuelve
    `[]` (sin cuentas reales en modo mock, mismo criterio que el resto). `real_trades.py`
    reescrito: `_is_roll()` (una orden con pata OPENING + CLOSING), dedup por `(order_id,
    occ_symbol)` vía la nueva columna `real_trade_alerts.order_id` — las ventanas de detección
    se solapan a propósito entre corridas (más margen que la cadencia del cron), el dedup hace
    que pedir de más sea inofensivo. Tabla `position_snapshots` DROPeada de la base real (ya
    no la usa nada). Verificado en vivo: la orden real de HOOD reproduce el fill exacto
    ($3.15), y una segunda apertura incremental de EWY (mismo contrato, distinto momento) trajo
    su fill propio ($5.90) — confirmando además, con datos reales, que el promedio blendeado
    usado por el ítem #35 ($5.225) YA no era exacto para ese lote puntual, motivo extra que
    confirma el valor del rediseño. UI: Pestaña Operaciones ahora agrupa las tarjetas por fecha
    ("⚡ Hoy · N operaciones" vs. fechas viejas), pedido explícito para distinguir de un
    vistazo qué es nuevo. 34 tests nuevos (`test_models.py`, `test_schwab_client.py`,
    `test_mock_client.py`, `test_real_trades.py`, `test_repository.py`) — 480/480 en verde.

    **Incidente real durante el despliegue, con causa raíz y resolución** — se documenta
    íntegro por transparencia: al reiniciar el scheduler con el código nuevo, el proceso VIEJO
    (todavía corriendo con el mecanismo anterior mientras se terminaba de verificar el nuevo)
    tuvo una falla transitoria de red pidiendo `/accounts/accountNumbers` a las 11:06 ET.
    `get_all_positions()` devolvía `[]` en ese caso (sin excepción, `_iter_raw_positions`
    swallowea el error) — y el código VIEJO, sin distinguir "sin posiciones" de "falló la
    consulta", reemplazaba igual el snapshot completo con esa lista vacía
    (`replace_position_snapshots(conn, [])`), borrando la base de comparación. La corrida
    siguiente (3 min después) vio TODAS las ~60 posiciones cortas reales de la cuenta como
    "nuevas" (nada en el snapshot vacío para compararlas), generando 60 alertas falsas — **y 60
    notificaciones de WhatsApp reales enviadas al usuario** por operaciones que NO eran nuevas,
    solo posiciones que ya tenía abiertas desde antes. Esto era un bug LATENTE del diseño
    viejo (no introducido por el rediseño) que nunca se había disparado hasta esta falla de red
    puntual. Acción tomada: desplegado el código nuevo de inmediato (que no tiene este riesgo —
    sin tabla de snapshot que vaciar, una falla de red en `/orders` simplemente da 0 órdenes
    esa corrida, sin corromper nada), las 60 filas espurias identificadas por su firma (mismo
    rango de tiempo, `order_id IS NULL`, fuera de las 4 filas legítimas ya conocidas) y
    borradas de la base. **Las 60 notificaciones de WhatsApp ya enviadas no se pueden
    deshacer** — quedó reportado directamente al usuario en el momento.
37. **Pestaña Screener: selector de estrategia Naked Put / Covered Call / Ambas** (pedido
    2026-07-28). Confirmado ANTES de programar (a pedido explícito): el Screener ya tenía
    acceso completo a los candidatos de Covered Call — `repo.get_recent_single_leg_candidates`
    ya incluía `covered_call` en `SINGLE_LEG_STRATEGIES` (junto a `cash_secured_put`/
    `short_put_naked`/`short_call_naked`) y `scanner_table.py::build_scanner_rows` ya calculaba
    Moneyness/%BE/Probabilidad OTM correctamente para el lado call (mismo signo que
    `compute_coverage`, ya generalizado desde que existe la Vista tabla de Escaneo) — 46
    candidatos reales de Covered Call ya en la base al momento de confirmar. Lo único que
    faltaba era el selector en la página (antes las 4 estrategias se mezclaban sin forma de
    aislar una).
    Fix: `screener_filters.py::STRATEGY_GROUP_LABELS`/`filter_by_strategy_group()` (filtra por
    la etiqueta legible "Estrategia" que ya trae cada fila, ANTES del resto de filtros — así
    los rangos de DTE/Strike se recalculan solo sobre el grupo elegido, no sobre las 4
    estrategias mezcladas). Radio "Naked Put" (Cash-Secured Put + Short Put Naked) / "Covered
    Call" / "Ambas" en `pages/10_screener.py`. Short Call (Naked) queda fuera de los dos grupos
    a propósito (perfil de riesgo distinto — sin acciones, riesgo no acotado) y solo se ve con
    "Ambas", igual que el comportamiento de antes de este selector.
    Verificado en navegador en vivo con datos reales: "Covered Call" da 39 candidatos (Strike
    Price recalculado a $7.50–$235, distinto del rango de Naked Put), strikes correctamente por
    ENCIMA del precio del subyacente (SOFI $16.77 → strike $18.50), Moneyness positivo (OTM a
    favor del vendedor). Filtro de Delta "Bajo (0-0.25)" combinado con la estrategia bajó
    correctamente de 39 a 20 candidatos, todos Covered Call con delta en el rango pedido. 8
    tests nuevos (`test_screener_filters.py`) — 487/487 en verde.
38. **Pestaña Screener: filtros de earnings y reunión FOMC antes del vencimiento** (pedido
    2026-07-28). Reusa datos que YA existían (los mismos que alimentan los caveats de las
    tarjetas de alerta y Eventos de riesgo) — sin llamada nueva a ninguna API, solo exponerlos
    como filtro. `scanner_table.py::build_scanner_rows()` suma 2 columnas derivadas ("Earnings
    antes del vencimiento" / "FOMC antes del vencimiento", `True`/`False`/`None` — mismo
    criterio de comparación de fechas ISO que ya usan `_earnings_caveat_html`/
    `_fed_event_caveat_html`). `screener_filters.py::apply_filters()` suma
    `exclude_earnings_before_expiration`/`exclude_fomc_before_expiration`: EXCEPCIÓN
    documentada a la regla general de "dato faltante = se excluye" — acá solo excluye cuando el
    riesgo está CONFIRMADO (`True`), un dato desconocido (`None`) NO se excluye (no hay forma
    de saber si es seguro, ocultarlo penalizaría candidatos válidos por falta de dato). 2
    checkboxes en `pages/10_screener.py`, con caption de cobertura de datos igual al patrón ya
    usado para Volumen/Open Interest.
    Verificado en navegador en vivo con datos reales: sin filtros, 464 candidatos (Naked Put);
    con "Excluir earnings antes del vencimiento" activo, baja a 214 (AAPL/AMZN/BA con earnings
    el 28-30/7, antes del vencimiento 21/8, correctamente excluidos; ACN con earnings 24/9,
    después del vencimiento, correctamente incluido). Filtro de FOMC verificado por separado:
    con la reunión FOMC real del 29/7 y todos los candidatos venciendo semanas después, da 0 —
    matemáticamente correcto (HOY, con ese calendario, todo candidato tiene FOMC antes del
    vencimiento), no un bug. 19 tests nuevos (`test_scanner_table.py`,
    `test_screener_filters.py`) — 502/502 en verde.
39. **Bug real corregido: filas duplicadas en el Screener (Cash-Secured Put vs Short Put
    (Naked), mismo contrato exacto)** (reportado 2026-07-28, ejemplo real: 2 filas idénticas
    para FDS). Root cause en `strategy/candidates.py::build_candidate()`: ambas estrategias
    rutean a la MISMA `_build_single_short_leg(strategy_type, chain, "put", ...)` — mismo
    strike por delta, mismo payoff (a diferencia de Covered Call vs Short Call Naked, que sí
    difieren porque una incluye la posición de acciones en el cálculo, ver
    `payoff.py::_STOCK_INCLUDED_STRATEGIES`). `strategy/selector.py` generaba las DOS como
    candidatos separados cada corrida — `config.py` ya documentaba la intención original de
    tratarlas como una sola categoría "Naked Put", pero solo se aplicaba en la UI (Alertas,
    Screener), no en la generación.
    Fix de raíz: `selector.py` ya no agrega `SHORT_PUT_NAKED` a la lista de candidatos —
    genera solo `CASH_SECURED_PUT` (la más específica, a pedido del usuario). Complementado con
    `scanner_table.py::_dedupe_naked_put_aliases()` (defensa en profundidad): colapsa filas de
    Cash-Secured Put / Short Put (Naked) del MISMO contrato exacto (símbolo/vencimiento/strike/
    bid) a una sola, prefiriendo la etiqueta Cash-Secured Put. Necesario porque el historial
    real de `candidate_contracts` no se puede limpiar sin más — confirmado que **190 alertas ya
    generadas** (algunas probablemente ya notificadas por WhatsApp) referencian
    `candidate_contract_id` de filas `short_put_naked` duplicadas; borrarlas rompería esa
    trazabilidad, así que el historial se deja intacto y la deduplicación pasa a ser puramente
    de visualización (afecta tanto Screener como la Vista tabla de Escaneo, mismo
    `build_scanner_rows` compartido).
    Verificado en navegador en vivo: el total de "Naked Put" bajó de 464 a 216 candidatos
    (evidencia de que la deduplicación aplica a todo el dataset, no solo a FDS); FDS pasó de
    tener una fila "Short Put (Naked)" duplicada a mostrar solo "Cash-Secured Put" en sus 3
    filas restantes (legítimamente distintas entre sí — strikes o días de escaneo distintos, no
    duplicados). 12 tests nuevos (`test_scanner_table.py`, `test_selector.py`) — 509/509 en
    verde.
40. **Reordenadas las columnas de la tabla del Screener** (pedido explícito 2026-07-28, tras
    confirmar con captura antes de tocar nada). Las 15 columnas "originales" que el usuario
    quería primero, en este orden exacto: Symbol, Price, Exp Date, Strike, Moneyness (%), Bid,
    Breakeven, %BE, Volume, Open Interest, IV Rank, Delta, Return (%), Rendimiento Anualizado
    (%), POP (%) — las 6 derivadas más recientes (Instrumento, Estrategia, Probabilidad OTM
    (%), DTE, Earnings antes del vencimiento, FOMC antes del vencimiento) al final, sin
    quitarlas (el usuario las quiere, solo pidió reordenar). Cambio puramente de orden de
    inserción del dict en `scanner_table.py::build_scanner_rows()` — `pd.DataFrame` respeta ese
    orden como orden de columnas, sin tocar nombres/valores. Afecta también a la Vista tabla de
    Escaneo (mismo `build_scanner_rows` compartido).
    Verificado en navegador en vivo con captura del nuevo orden completo (scroll horizontal).
    1 test nuevo (`test_build_scanner_rows_column_order`) — 510/510 en verde.
41. **"Check histórico" en las alertas** (pedido 2026-07-28, con evaluación de viabilidad
    confirmada antes de programar). De todas las ventanas de N días CALENDARIO (=DTE de la
    alerta) posibles en los últimos ~5 años de precio real, cuántas veces el precio se movió al
    menos tanto como necesitaría moverse HOY para llegar al strike — usando el % de cobertura
    aplicado sobre el precio de INICIO de cada ventana histórica (no un strike en dólares fijo
    contra el pasado), aclaración de alcance del usuario ya cubierta por el diseño original sin
    cambios de código. Usa el RANGO de cada barra (low para puts, high para calls), no solo el
    cierre, para capturar si el precio tocó el nivel en algún momento dentro de la ventana.
    Viabilidad confirmada en vivo: Schwab soporta `period=5` años en `/pricehistory` (1255
    barras reales para AAPL, 2021-07-27 a 2026-07-27, en 0.42s) — antes el pipeline solo pedía
    2 años (`period=2`, hardcodeado) recortados a 300 días. Se subió `period` a 5 en
    `schwab_client.py::get_price_history` (los callers existentes, con `lookback_days=300`,
    no notan la diferencia — siguen recortando igual).
    Piezas nuevas: `strategy/backtest.py::historical_move_frequency()` (función pura,
    ventana O(n) sobre las barras) + `compute_historical_move_check()` (envoltorio: encuentra
    la pata vendida principal, pide 5 años a Schwab, corre el cálculo — nunca rompe al caller,
    None si falta cualquier dato). Calculado UNA VEZ al generar la alerta (no en cada vista de
    página, mismo patrón que payoff/P&L ya establecido) y guardado en 2 columnas nuevas
    (`historical_move_occurrences`/`historical_move_total_windows`) en `candidate_contracts` y
    `real_trade_alerts` — `window_days` no se persiste aparte, ya es el `dte` existente.
    `alerts/engine.py::process_symbol_alerts()` suma un parámetro `broker` opcional (None en
    tests que no lo necesitan) para poder pedir el historial justo antes de persistir un
    candidato que ya pasó el umbral — no se pide para TODO el universo escaneado, solo para lo
    que realmente se alertaría, evitando inflar la carga de la corrida regular.
    UI: `dashboard/components.py::_historical_move_caveat_html()` — a propósito NO es un simple
    ✅/❌: 0 ocurrencias muestra un check verde con el total de ventanas evaluadas, ocurrencias
    >0 muestra "Ocurrió en N de M ventanas de Dd (X.X%)" en ámbar — SIEMPRE con la aclaración
    "análisis histórico, no garantiza el futuro" en ambos casos. Sin narrador (LLM) por ahora,
    alcance acotado a pedido explícito. Aplicado tanto a `render_alert_card` (Alertas) como
    `render_real_trade_card` (Operaciones).
    Verificado con datos 100% reales de Schwab, no sintéticos: AAPL, put, 45 DTE, 15% de
    cobertura → ocurrió en 96 de 1,226 ventanas (7.8%) en los últimos 5 años. Retroalimentada y
    verificada EN NAVEGADOR sobre una alerta real ya persistida hoy (AAPL Cash-Secured Put,
    strike $305, 31 DTE, ~9.5% de cobertura): "Ocurrió en 260 de 1,235 ventanas de 31d (21.1%)"
    — coherente con el ejemplo de 45 DTE (menor cobertura exigida = mayor frecuencia histórica,
    relación matemáticamente esperada). 26 tests nuevos (`test_backtest.py`,
    `test_repository.py`, `test_real_trades.py`, `test_components.py`) — 541/541 en verde.

    **Refinamiento 2026-07-29 (feedback real del usuario: el texto "N de M ventanas (X.X%)" no
    se entendía de un vistazo, y el usuario detectó correctamente que el conteo crudo de
    ventanas infla el número — una sola caída sostenida es "vista" por decenas de ventanas
    solapadas que arrancan en días distintos, contando el mismo evento de mercado muchas veces).**
    `historical_move_frequency()` ahora agrupa rachas de ventanas consecutivas que cumplen la
    condición en un solo evento real: un evento nuevo arranca solo cuando aparece una ventana
    que cumple justo después de una (o más) que no cumplió (el precio se recuperó y volvió a
    caer/subir en un episodio distinto) — así una caída sostenida de varios días sigue contando
    como 1, no una vez por cada punto de partida que alcanza a verla. `total_windows` se
    conserva en la base para referencia interna pero ya no se muestra en el badge. Texto nuevo,
    sin porcentajes ni jerga técnica: "Nunca tocó este nivel en los últimos ~5 años" (0 casos) o
    "En los últimos ~5 años, el precio tocó este nivel N veces" (N>0) — siempre con la misma
    aclaración de que es análisis histórico, no garantía futura.
    Verificado con el mismo ejemplo real ya mostrado antes en vivo (WFC Iron Condor, put
    strike $79, 31 DTE): el crudo de ventanas solapadas daba 330 de 1,235 (26.7%) — con el
    conteo de eventos distintos da **41 veces**, una reducción de ~8x que confirma que la
    métrica anterior sobrecontaba. Recalculados también los otros 5 registros reales ya
    persistidos esta sesión (AAPL 260→26, USO 291→58 ×3, VNQ 36→5) — todos bajan de forma
    consistente. 4 tests nuevos que verifican explícitamente el agrupamiento (una sola caída
    vista por muchas ventanas solapadas = 1 evento; dos caídas separadas = 2 eventos; un
    movimiento sostenido de varios días = 1 evento, no uno por día) — 545/545 en verde.

42. **Market Movers: agregados Nasdaq y Dow Jones** (pedido 2026-07-29, solo mostraba S&P 500).
    Confirmado en vivo ANTES de programar (pedido explícito del usuario) que el mismo endpoint
    `/movers/{index}` de Schwab que ya funcionaba con `$SPX` soporta `$COMPX` (Nasdaq Composite)
    y `$DJI` (Dow Jones) sin cambios — mismo formato de respuesta, probado con el mercado
    abierto (10 movers reales devueltos para cada uno). Entre las 2 opciones planteadas (pestañas
    vs. los 3 paneles en paralelo) se eligió pestañas: cada panel ya trae ganadoras+perdedoras
    con hasta 8 filas cada una, ponerlos en paralelo hubiera sido demasiado espacio vertical.
    `dashboard/components.py::MARKET_MOVERS_INDICES` (dict código→nombre para mostrar) +
    `render_market_movers_panel(index)` ahora usa el nombre amigable en el título en vez del
    código crudo del índice. `app.py::render_general_page` reemplaza la llamada única por
    `st.tabs(...)` iterando sobre los 3 índices. Verificado en vivo con las 3 pestañas: S&P 500
    (Ford +5.21%, AMD -4.05%), Nasdaq (Nocera +113.43%, Li Bang Intl -18.34%) y Dow Jones
    (Coca-Cola +2.54%, Procter & Gamble -2.69%) — datos reales, no simulados. 545/545 en verde
    (sin tests nuevos: es reuso directo de la función ya probada `split_gainers_losers`, solo
    cambia qué índice se le pasa).

43. **Pestaña Operaciones: filtro de rango de fechas** (pedido 2026-07-29). Selectbox nuevo
    junto al de Símbolo, mismas 5 opciones pedidas en el orden pedido: Hoy / Última semana /
    Últimos 15 días / Último mes / Todo (default "Todo", preserva el comportamiento de antes
    para quien no toque el filtro). `dashboard/components.py::DATE_RANGE_OPTIONS` (dict
    etiqueta→días hacia atrás, `None` = sin filtro) + `filter_trades_by_date_range(trades,
    range_label, today)` — función pura, filtra sobre las filas ya traídas por
    `repo.get_real_trade_alerts` (no una query nueva a la base, el volumen actual de operaciones
    reales no lo justifica). "Hoy" usa 0 días hacia atrás (`trade_date >= today`), evita un
    chequeo de igualdad aparte. El agrupamiento visual por fecha (`itertools.groupby`, "⚡ Hoy"
    vs "🕐 DD/MM/AAAA") sigue funcionando igual porque el filtro se aplica ANTES de agrupar, no
    después — no hizo falta tocar esa lógica. Mensaje de "sin resultados" mejorado: distingue
    "no hay ninguna operación detectada todavía" (mensaje original) de "hay operaciones pero
    ninguna coincide con el filtro actual" (mensaje nuevo), para no confundir al usuario cuando
    el filtro simplemente no tiene resultados. 7 tests nuevos (`test_components.py`) — 568/568
    en verde. Verificado en vivo con capturas: "Hoy" muestra solo el grupo "⚡ Hoy · 4
    operaciones" (el grupo del 28/07 desaparece); "Últimos 15 días" muestra ambos grupos juntos,
    agrupamiento intacto.

44. **Pestaña Alertas: filtro de rango de fechas + agrupamiento visual por fecha** (pedido
    2026-07-29, "que se limpie automáticamente cada día mostrando solo las de hoy"). Aclarado
    con el usuario antes de programar: NO borrar nada de la base — historial completo
    conservado, solo la vista por defecto cambia. `filter_trades_by_date_range` (Operaciones) se
    generalizó a `filter_by_date_range(rows, range_label, today, date_field="trade_date")` —
    mismo `DATE_RANGE_OPTIONS`, ahora con un parámetro `date_field` para reusarse en Alertas
    (`date_field="alert_date"`) sin duplicar la función. A diferencia de Operaciones (default
    "Todo"), acá el default es "Hoy" — pedido explícito del usuario de que la vista al entrar
    muestre solo lo de hoy. Se sumó el mismo agrupamiento visual por fecha que ya tenía
    Operaciones (`itertools.groupby` sobre `alert_date`, "⚡ Hoy" vs "🕐 DD/MM/AAAA"), que Alertas
    no tenía antes (mostraba todo en una lista plana). Selectbox nuevo como 4ta columna junto a
    Símbolo/Perfil de riesgo/Estrategia. Mensaje de "sin resultados" también distingue "nunca
    hubo alertas" de "hay alertas pero ninguna coincide con el filtro actual". 8 tests nuevos
    (`test_components.py`, incluyendo uno que verifica el `date_field` genérico) — 569/569 en
    verde. Verificado en vivo: con "Hoy" seleccionado (default) no había alertas de candidatos
    generadas hoy todavía — mensaje correcto de "sin resultados para el filtro" (no confundido
    con "nunca hubo alertas"); con "Todo" se ve el historial completo agrupado, ej. "🕐
    28/07/2026 · 200 alertas".

45. **Bug real URGENTE corregido: Iron Condor de 4 patas detectado como pata desnuda suelta en
    Pestaña Operaciones** (reportado 2026-07-29, posición real de AMD). `detect_and_alert_real_
    trades` procesaba cada pata SELL_TO_OPEN de una orden por separado — nunca miraba las patas
    BUY_TO_OPEN, así que un Iron Condor (2 vendidas + 2 compradas, riesgo acotado por diseño) se
    guardaba como 2 alertas de posición DESNUDA sueltas (riesgo NO acotado, mal representado —
    grave con plata real). `alerts/real_trades.py::_classify_opening_legs` clasifica TODAS las
    patas OPENING de la orden por su composición real antes de procesar nada: reconoce Iron
    Condor (1 sell put + 1 buy put + 1 sell call + 1 buy call) y credit spreads de 2 patas
    (Bull Put / Bear Call, solo si son crédito neto — prima vendida > prima comprada, un debit
    spread no es venta de prima y queda fuera de este detector de INGRESO). Si la orden no
    matchea ninguna composición reconocida, degrada al camino de 1 pata por vez de siempre
    (sigue correcto para posiciones genuinamente desnudas). `strategy/candidates.py::
    build_from_real_legs` generaliza `build_from_contract` (1 pata) a N patas ya conocidas
    (strikes/precios reales de la orden, no elegidos por delta) — el resto del pipeline (payoff,
    cobertura, liquidez, riesgo de dividendo, check histórico) ya era genérico a N patas, el bug
    estaba puramente en cómo se armaba el `CandidateBuild`. Corregido en vivo contra la posición
    real: confirmado con el order log crudo de Schwab (`complexOrderStrategyType: "IRON_CONDOR"`,
    sell call $440/buy call $442.5/sell put $440/buy put $435) que la orden es un Iron Condor
    genuino; se limpiaron las 2 filas rotas y se regeneró con el código corregido — la tarjeta
    ahora muestra "AMD — Iron Condor", las 4 patas completas, y pérdida máxima ACOTADA ($81) en
    vez de "Cash-Secured Put"/"Short Call Naked" sueltos con riesgo no acotado mal representado.
    17 tests nuevos (`test_real_trades.py`: clasificación de Iron Condor/credit spread/debit
    spread rechazado/composición no reconocida + integración end-to-end con dedup;
    `test_candidates.py`: `build_from_real_legs` con 4 y 2 patas, greeks netos) — 581/581 en
    verde.

46. **Check histórico refinado: banda de tolerancia de plazo + magnitud, aparte del badge
    existente** (pedido 2026-07-29). El badge original ("tocó este nivel N veces") usa un
    umbral "o más" (cualquier caída IGUAL o MAYOR al % de cobertura de hoy cuenta) — el usuario
    pidió algo más preciso: "movimientos de magnitud SIMILAR en un plazo similar", no "al
    menos tan grande". Antes de programar, confirmé el entendimiento con un ejemplo concreto
    (strike $300/precio $350/45 DTE → 14.3% requerido, banda de plazo 38-52 días con ±1 semana,
    banda de magnitud 11.3%-17.3% con ±3 puntos porcentuales) y expliqué que es una pregunta
    DISTINTA, no una extensión — un crash mucho más grande que la cobertura de hoy (ej. -40%)
    queda FUERA del conteo "similar" aunque hubiera perforado la posición con margen de sobra.
    2 decisiones de diseño confirmadas por el usuario antes de implementar: (1) los crashes
    "más grandes que la banda" se muestran APARTE en vez de esconderse, (2) el badge nuevo
    coexiste con el existente en vez de reemplazarlo.
    `strategy/backtest.py::historical_similar_move_frequency()` — para cada punto de partida
    posible, revisa TODOS los días entre `window_days-7` y `window_days+7`; si el movimiento
    acumulado cae dentro de la banda porcentual en algún punto, es "similar"; si nunca entra a
    la banda pero el movimiento final del rango la superó por arriba, es "más grande todavía".
    Mismo agrupamiento de rachas consecutivas en un evento distinto que ya usa el badge
    original. `compute_historical_checks()` reemplaza al viejo `compute_historical_move_check`
    — pide el historial de 5 años a Schwab UNA sola vez y corre AMBOS análisis sobre los mismos
    datos, para no duplicar la llamada de red por tener 2 checks en vez de 1.
    2 columnas nuevas en `candidate_contracts`/`real_trade_alerts`
    (`similar_move_occurrences`/`similar_move_bigger_occurrences`), mismo patrón de migración
    ya establecido. Badge nuevo en `components.py::_similar_move_caveat_html()`, mostrado
    DEBAJO del badge existente en ambas tarjetas (Alertas y Operaciones): "Con más precisión
    (mismo % aprox. ±3 puntos, mismo plazo aprox. ±7 días): pasó N veces" + "y hubo M veces una
    caída AÚN MÁS GRANDE en un plazo similar" cuando M>0 — siempre con la misma aclaración de
    que es análisis histórico, no garantía futura.
    24 tests nuevos (`test_backtest.py`: banda de magnitud, crash grande clasificado aparte,
    movimiento chico sin contar en ninguno, tolerancia de plazo, agrupamiento de rachas,
    wrapper combinado pide el historial una sola vez; `test_components.py`: badge con/sin
    crashes grandes, singular/plural, aclaración histórica siempre presente;
    `test_repository.py`: persistencia de las 2 columnas nuevas en ambas tablas) — 602/602 en
    verde. Verificado en vivo con datos reales de Citigroup (CSP, strike $115, 37 DTE): badge
    original "tocó este nivel 32 veces", badge nuevo "pasó 76 veces — y hubo 22 veces una caída
    AÚN MÁS GRANDE en un plazo similar" — ambos badges visibles a la vez en la misma tarjeta.

47. **Market Movers: top 10 REAL por %, no el ranking por volumen de Schwab** (pedido
    2026-07-30, 2 problemas reportados: solo mostraba 5 en vez de 10, y aparecían "movers"
    insignificantes como +0.65%/-0.02% en Dow Jones). Investigado a fondo ANTES de programar:
    confirmado en vivo (los 3 índices × 4 `sort` × 6 `frequency`) que `/movers` de Schwab
    SIEMPRE devuelve las mismas 10 acciones de mayor VOLUMEN — nunca 10 ganadoras + 10
    perdedoras reales, y la sospecha del usuario ("es que Dow solo tiene 30 empresas") NO era
    la causa raíz: pasaba igual en S&P 500 y Nasdaq, ambos con universos mucho más grandes. Se
    le presentaron 2 opciones (filtrar y aceptar el techo de 10 vs. construir un top 10 real) y
    eligió la segunda, más trabajo pero datos genuinamente útiles.
    `config/movers_universe/{sp500,nasdaq100,dow30}.yaml` — componentes REALES de cada índice
    (503/103/30 tickers), obtenidos de Wikipedia el 2026-07-29 con **wikitext crudo parseado
    con regex, no un resumen de IA** (un intento inicial vía `WebFetch` inventó tickers
    incorrectos — CDN en vez de CDNS, FORTW en vez de FTNT, SDSK en vez de SNDK — inaceptable
    para símbolos financieros donde un carácter mal puesto muestra la empresa equivocada).
    Nasdaq usa el Nasdaq-100 (no el Composite completo, ~3000+ símbolos, impracticable de
    cotizar). `config.py::load_movers_universe(index)` carga la lista correspondiente.
    `Quote` ganó 2 campos (`description`/`total_volume`, ya venían en la respuesta de
    `/quotes` de Schwab sin costo extra) — `dashboard/components.py::cached_movers(index)`
    ahora cotiza en batch TODO el universo del índice (chunks de 200, mismo tamaño ya probado
    en `screen_universe`) y arma el ranking real localmente con `_movers_from_quotes()` +
    `split_gainers_losers()` (reusada sin cambios) en vez de llamar a `broker.get_movers()`.
    Límite de la tabla subido de 8 a `MOVERS_TOP_N=10`. `filter_significant_movers()` — umbral
    `MOVERS_MIN_SIGNIFICANT_PCT=0.5` (±0.5 puntos porcentuales): confirma la sospecha original
    del usuario, pero acotada correctamente a índices chicos — con S&P 500/Nasdaq-100 casi
    nunca filtra nada (siempre hay de sobra genuinamente significativo), con el Dow (30
    nombres) puede mostrar MENOS de 10 en una columna un día tranquilo, en vez de rellenar con
    ruido para forzar el número.
    `MockBrokerClient.get_quotes()` ahora tolera símbolos sin fixture (se omiten, no tiran
    abajo todo el batch) — necesario porque el universo de Market Movers en modo mock
    casi seguro no tiene fixture propia para cada ticker.
    17 tests nuevos (`test_config.py`: tamaños de cada universo, sin duplicados;
    `test_components.py`: conversión quote→mover, filtro de significancia con casos límite;
    `test_schwab_client.py`: parseo de description/total_volume; `test_mock_client.py`:
    tolerancia a símbolos sin fixture) — 617/617 en verde.
    Verificado en vivo con datos 100% reales: S&P 500 mostró el top 10 completo en ambas
    columnas (ganadora más chica +6.39% GEHC — nada de ruido), Nasdaq-100 igual con un set de
    nombres genuinamente distinto (ARM -13.38%, NBIS -11.11%), y Dow Jones mostró exactamente
    lo que predecía la limitación real: 7 ganadoras / 9 perdedoras (no 10), confirmando que
    filtrar en vez de forzar el número era la decisión correcta.

48. **Pestaña Operaciones: rolls visibles (cambio de alcance) + vista de tabla plana** (pedido
    2026-07-30). Dos pedidos relacionados, implementados juntos porque la tabla necesita una
    columna "Tipo" que solo tiene sentido si los rolls ya se detectan.

    **Rolls** (antes se saltaban del todo, ver Fase 1 original): `_is_roll(order)` seguía
    identificándolos con certeza (pata OPENING + CLOSING en la misma orden, vía /orders) — el
    cambio fue dejar de saltear y en cambio generar 2 registros que comparten `order_id`:
    `alerts/real_trades.py::_build_and_persist_roll_closed_leg` arma un registro LIVIANO de la
    pata cerrada (símbolo/strike/vencimiento/precio de cierre real, sin cotización en vivo ni
    P&L propio — ya no es una posición activa) con `leg_role="roll_closed"`;
    `_process_opening_legs(..., leg_role="roll_opened")` reusa el pipeline COMPLETO de apertura
    de siempre (incluye la clasificación de Iron Condor/credit spread ya construida) para la
    pata nueva. Columna nueva `leg_role` en `real_trade_alerts` (NULL/roll_closed/roll_opened).
    Confirmado con el usuario ANTES de programar cómo mostrarlo: 2 filas conectadas dentro de
    un mismo bloque "🔄 Roll" (no 1 fila con columnas dobles — un roll casi siempre cambia
    strike Y vencimiento a la vez, cramear 2 de cada uno en la misma celda sería ilegible) —
    `dashboard/components.py::group_roll_pairs()` (agrupa por `order_id`, generaliza sin
    cambios a rolls de varias patas) + `render_roll_group()` ("Operación anterior (cerrada)" en
    amarillo, "Operación nueva (abierta)" en verde, la tarjeta completa de la pata nueva debajo
    para quien quiera el detalle a fondo). Contado como 1 sola "operación" en el encabezado de
    fecha, no 2.

    **Tabla plana** (ADEMÁS de las tarjetas, no en su reemplazo — decisión confirmada con el
    usuario: las tarjetas tienen avisos/P&L/comentario que la tabla no muestra a propósito, son
    2 casos de uso distintos). Toggle "Tarjetas"/"Tabla" arriba de los filtros existentes.
    Columnas: Symbol, Fecha/Hora, Precio actual (EN VIVO, confirmado con el usuario — no el
    valor guardado al detectar la operación) vs. entrada, IV Rank (más reciente, hoy si hay
    snapshot), Estrategia, Precio (crédito/débito), Tipo (Apertura/Roll·Nuevo/Roll·Cerrado).
    `cached_option_quotes()` — mismo endpoint batch `/quotes` de Schwab que ya usa
    `cached_quotes`, confirmado en vivo que también acepta símbolos OCC de opciones sin cambios.
    `Quote` ganó `description`/`total_volume` en la sesión anterior (Market Movers), reusados
    acá también. Color de precio actual: para una posición VENDIDA (el caso normal), bajar
    desde la entrada es favorable (verde), subir es desfavorable (rojo) — mismo criterio que el
    resto de la app.
    18 tests nuevos (`test_real_trades.py`: roll genera 2 registros con datos correctos, dedup
    entre corridas, pata cerrada persiste aunque la cadena no tenga el contrato nuevo;
    `test_components.py`: agrupamiento de rolls con casos límite incluyendo multi-pata, filas de
    la tabla con/sin quote en vivo, pata cerrada sin datos en vivo) — 628/628 en verde.
    Verificado en vivo con un roll REAL de la cuenta (encontrado en los logs del scheduler,
    3 órdenes de roll que se habían saltado antes del fix — reprocesadas con el código nuevo):
    TSLA, mismo strike $350, vencimiento 07/08→04/09. Tarjetas: bloque amarillo/verde correcto,
    "Hoy · 2 operaciones" (no 3). Tabla: precio en vivo $44.75 vs. entrada $48.08 en verde,
    etiquetas "Roll · Nuevo"/"Roll · Cerrado" correctas. IV Rank verificado por separado
    (AAPL con snapshot de hoy mostró 61.64 real) para confirmar que "N/D" en TSLA/TLT es
    porque genuinamente no tienen snapshot hoy, no un bug.

49. **Rebranding a "OptionsUp"** (pedido 2026-07-31, alcance confirmado con el usuario: solo
    texto visible en UI + README/docs, sin tocar nombre de repo/paquete ni identidad visual).
    Barrida completa del nombre viejo "Options Income Advisor": la UI del dashboard ya no lo
    mostraba en ningún lado desde un cambio estético anterior (ítem #30, título de General pasó
    a "Stock Market Overview") — quedaban 3 lugares reales: título de `README.md`, la
    notificación nativa de macOS del healthcheck (`scripts/healthcheck_scheduler.py`) y el
    mensaje de log/notify del scheduler colgado (`src/options_advisor/scheduler/healthcheck.py`).
    Los 3 actualizados a "OptionsUp". Verificado en navegador en vivo (screenshot de la página
    General, sin restos del nombre viejo) y 631/631 tests en verde.

50. **Modelo de barra intradía + VWAP** (confirmado 2026-07-31, base para el gráfico de velas
    de abajo). `broker/models.py::IntradayBar` (OHLCV con `timestamp`, a diferencia de
    `PriceBar` que es 1 barra = 1 día) + `BrokerClient.get_intraday_bars(symbol, session_date,
    interval_minutes)`. Investigado en vivo antes de programar: `SchwabBrokerClient` pide
    `/pricehistory` con `startDate`/`endDate` explícitos en vez del parámetro `period` que ya
    usa `get_price_history` — confirmado que `period=1` con `periodType=day` devuelve la
    sesión ANTERIOR, no la de hoy, aun después del cierre (un rango explícito sí trae la
    sesión pedida). `needExtendedHoursData=False` acota a la sesión regular (9:30-16:00 ET),
    base correcta para VWAP (resetea cada sesión). Solo 1/5/10/15/30 min son intervalos
    válidos para Schwab (confirmado en vivo: 2 y 3 dan 400 Bad Request) — validado del lado
    del cliente antes de pedir. `scheduler/market_calendar.py` suma
    `session_bounds()`/`latest_trading_session()` (reusa `pandas_market_calendars`, igual que
    `market_session()` ya existente, sin manejar DST a mano). `MockBrokerClient` sintetiza
    barras deterministas (seed por símbolo+fecha+intervalo) a partir del OHLC diario del
    fixture, sin necesitar fixtures intradía nuevas. `indicators/intraday.py::compute_vwap()`
    — precio típico (H+L+C)/3 ponderado por volumen, acumulado desde el inicio de la sesión.
    Verificado en vivo contra Schwab real (AAPL, sesión de hoy, intervalo 5 min): 78 barras,
    VWAP $303.76 coherente contra el cierre real $309.03. 20 tests nuevos — 651/651 en verde.

51. **Página "Gráfico de velas" con VWAP** (confirmado 2026-07-31, sobre el modelo de barra
    intradía del ítem #50). Nueva `pages/11_grafico.py` — selector de Símbolo/Intervalo
    (1/5/10/15/30 min)/Sesión, candlestick (Plotly `go.Candlestick`) + línea de VWAP superpuesta
    + volumen en un subplot separado abajo (nunca doble eje Y, ver skill de dataviz). Colores
    reusan la paleta ya validada del resto de la app: velas suba/baja en `GOOD`/`CRITICAL`
    (mismo verde/rojo direccional que Market Movers y el resto de la UI), VWAP en el naranja ya
    usado para líneas de indicador en Indicadores (`pages/3_indicadores.py`), volumen coloreado
    por la dirección de su propia vela. `dashboard/components.py::cached_intraday_bars()`
    (cache 60s, mismo patrón que `cached_quotes`/`cached_option_quotes`).
    Bug real encontrado y corregido en la verificación en vivo: el eje X mostraba las horas en
    UTC (14:00-19:00) en vez de horario de mercado ET (9:30-16:00) — `IntradayBar.timestamp`
    es UTC a propósito (correcto para guardar/calcular), pero Plotly grafica el tz-aware tal
    cual sin convertir; fix fue convertir a `America/New_York` solo para el eje del gráfico,
    sin tocar el modelo de datos. Registrada en `st.navigation` de `app.py` como "Gráfico".
    Verificado en navegador en vivo con datos reales de Schwab (SPY y AAPL, sesión de hoy,
    cambio de símbolo recalcula el gráfico y el VWAP correctamente). 651/651 tests en verde
    (sin tests nuevos — la página es cableado de `get_intraday_bars`/`compute_vwap`, ya
    testeados en el ítem #50; verificación en vivo en el navegador en su lugar).

## Cómo se usa este archivo

- Antes de arrancar algo nuevo: agregarlo a "Pendiente, no empezado" apenas se pide, aunque
  no se vaya a implementar en el momento.
- Al arrancar: mover a "En progreso ahora" (solo debería haber 1, salvo casos puntuales).
- Al terminar y verificar: mover a "Terminado y verificado" con la fecha, y borrar el detalle
  largo de acá (ese detalle vive en `NOTES.md`, acá solo una línea).
