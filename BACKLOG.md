# Backlog consolidado

Registro vivo de todo lo pedido, para no perder el hilo en sesiones largas. Se actualiza cada
vez que algo arranca o termina — no es un historial (eso está en `NOTES.md` y en `git log`),
es el estado ACTUAL de qué falta.

Última actualización: 2026-07-28 (madrugada — Pestaña Operaciones, Fed/FRED, Calendario de
earnings por rango, los 4 bugs urgentes, la vista tabla en Escaneo, la Pestaña Screener, y los
ajustes estéticos de la página General, todos terminados y verificados).

## En progreso ahora

Ninguno. Sigue el punto 1 de "Pendiente" abajo (Simulador de escenarios en Portafolio real).

## Hallazgo sin resolver — scheduler dejó de correr ~2h46m el 2026-07-27

Durante la investigación del bug #24, se encontró que el proceso del scheduler dejó de
ejecutar sus jobs entre las 15:48 y 18:34 del 2026-07-27 (se ve en `data/logs/scheduler.err.log`
como jobs "missed" en vez de ejecutados). Causa no confirmada — posible suspensión de la
laptop, aunque hay un `caffeinate` corriendo hace 92h que en teoría debería prevenir sleep por
inactividad, así que no es la explicación obvia. No bloqueó nada esa vez (el detector alcanzó
a agarrar todo en la corrida manual siguiente). **Estado al cierre de esta sesión (madrugada
2026-07-28)**: proceso del scheduler vivo y confirmado (`ps aux`); su inactividad actual es
ESPERADA, no el mismo bug — los crons de `periodic_poll` y `real_trade_detection` solo corren
en horario de mercado (`hour='9-16'`), así que correctamente esperan hasta las 09:00 ET de
mañana en vez de disparar de noche. Si el scheduler se cae de nuevo POR HORAS DURANTE horario
de mercado, sí podría hacer perder operaciones reales — investigar de nuevo si vuelve a pasar.

## Pendiente, no empezado — orden confirmado por el usuario 2026-07-26/27

1. **Simulador de escenarios en Portafolio real**: selector alcista/bajista/neutral + % de
   movimiento, aplicado por igual a todos los subyacentes de posiciones abiertas, recalculado
   con el motor de `payoff.py` existente. Muestra total proyectado vs. hoy (diferencia $ y %).
   Disclaimer de que es una simplificación (todo se mueve igual), no una predicción real.
   Complejidad: **baja/media** — reusa el motor de payoff existente, con precedente directo
   (rendimiento anualizado + cierre anticipado ya hicieron algo similar).

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
- **BTC real (spot)**: confirmado 2026-07-26 — usar Finnhub `BINANCE:BTCUSDT` en vez del ETF
  apalancado de Schwab. Pendiente de implementar (mostrar precio real, no motor de opciones —
  BTC spot no tiene cadena de opciones en Schwab).

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

## Cómo se usa este archivo

- Antes de arrancar algo nuevo: agregarlo a "Pendiente, no empezado" apenas se pide, aunque
  no se vaya a implementar en el momento.
- Al arrancar: mover a "En progreso ahora" (solo debería haber 1, salvo casos puntuales).
- Al terminar y verificar: mover a "Terminado y verificado" con la fecha, y borrar el detalle
  largo de acá (ese detalle vive en `NOTES.md`, acá solo una línea).
