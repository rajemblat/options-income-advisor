# Estado del proyecto — sesión 2026-07-23 → 2026-07-24

Conexión real con Schwab: verificada, conectada y con un bug de datos crítico ya resuelto.
Todo lo de esta sección está commiteado y pusheado a `origin/main` (último commit: `f64992f`).

## Resuelto hoy — segunda mitad de la sesión (auditoría del usuario + trabajo autónomo)

Después del punto 17, el usuario hizo una auditoría de 3 pedidos que yo había dado por hechos
sin verificar bien — encontré que 2 de 3 estaban mal, más una causa raíz operativa importante:

18. **Causa raíz recurrente del día: procesos con código viejo en memoria.** El bug grande de
    esta sesión (234 alertas con emoji/lógica vieja, sección de abajo) se repitió en menor
    escala varias veces más: tanto el scheduler de `launchd` como el proceso de `streamlit run`
    mantienen el código VIEJO en memoria hasta que se reinician — un commit nuevo no alcanza.
    Pasó con `config.py` (Streamlit), con `formatting.py`/`engine.py` (scheduler) y de nuevo con
    `components.py`. **Regla ya aplicada consistentemente el resto de la sesión: después de
    CUALQUIER commit que toque `scheduler/jobs.py`, `alerts/*.py`, `dashboard/components.py` o
    cualquier módulo compartido (no la página de Streamlit que se está viendo), reiniciar AMBOS
    procesos antes de dar el cambio por verificado**:
    ```bash
    launchctl kickstart -k gui/$(id -u)/com.robertoajemblat.options-income-advisor.scheduler
    kill <pid de streamlit>; nohup .venv/bin/streamlit run src/options_advisor/dashboard/app.py > data/logs/streamlit.log 2>&1 &
    ```
19. **Íconos del texto de WhatsApp — fix real, dos rondas.** La primera "modernización" (punto
    13 de arriba) solo había tocado 4 de ~18 emoji del mensaje, dejando una mezcla visible de
    estilos. Primera ronda: reemplacé los ~14 restantes por un set igual de "emoji con color"
    pero coherente. El usuario pidió más — un rediseño completo a un estilo distinto, y esta vez
    pidió ver 3-4 opciones de estilo ANTES de aplicar (geométrico / flat design / minimalista
    monocromo / solo etiquetas de texto, con preview de cada uno). Eligió **"minimalista
    monocromo"**: `✦ ✖ ✓ ⚠ ✧ • ↓ ↑ ▸ ＄ ▲ ▽ ≡ ◎ ○ ▤ ✎`, sin ningún emoji de color, aplicado de
    punta a punta a `alerts/formatting.py`. De paso se encontró y corrigió un bug real: el
    parser de `dashboard/components.py` que separa el comentario final para la tarjeta buscaba
    el marcador viejo (`"💡 Comentario:"` primero, después `"💬 Comentario:"`) — al no encontrarlo
    tras cada cambio de emoji, volcaba el mensaje CRUDO completo (con todos los íconos) en el
    campo "Comentario" de la tarjeta en vez de solo el párrafo narrado. Hay que recordar
    sincronizar ese marcador cada vez que cambie el emoji de "Comentario" en `formatting.py`.
20. **Watchlist real de thinkorswim (96 símbolos) — de verdad integrada esta vez.** El punto 17
    de arriba decía que ya estaba hecha; no era cierto, solo se había recibido y acordado.
    Ahora sí: `config/watchlist_thinkorswim.yaml` (96 símbolos reales del usuario, con nota de
    qué se excluyó — SPX/RUT/NDX/VIX por prefijo `$` sin probar en cadenas de opciones, BTC
    porque en Schwab resuelve a un ETF apalancado, no Bitcoin real) + `load_priority_watchlist_symbols()`
    en `config.py`, unida tanto en `/escaneo` (universo de escaneo) como en `/watchlist` (la
    tabla de indicadores normal, que antes solo mostraba los 13 símbolos fijos). La tabla de
    Watchlist ahora avisa explícitamente cuántos de los símbolos totales todavía no tienen
    snapshot generado, en vez de mostrarlos silenciosamente vacíos.
21. **% de cobertura por alerta** (pedido nuevo del usuario): cuánto tiene que moverse el
    subyacente para llegar al strike de la pata vendida — `(precio - strike) / precio` para
    puts vendidos, `(strike - precio) / precio` para calls vendidos. Calculado al vuelo desde
    los legs + precio ya persistidos (**sin nueva columna en la DB** — es 100% derivable de
    datos existentes). Iron Condor muestra cobertura a la baja Y al alza por separado (dos
    patas vendidas); el resto de estrategias del MVP, una sola. Destacado en la tarjeta con el
    mismo tratamiento visual que el POP badge (`coverage_badge_html` en `components.py`, color
    por umbral: verde ≥15%, amarillo ≥7%, rojo debajo).
22. **Cada corrida evalúa los 3 perfiles de riesgo a la vez** (pedido nuevo del usuario,
    cambio de comportamiento importante): "Correr análisis ahora" (y el scheduler, y el
    escaneo) ya no generan alertas solo para el perfil activo en Configuración — evalúan
    conservador/moderado/agresivo en la misma corrida. Lo caro por símbolo (quote/historial/
    cadena de Schwab, earnings y noticias de Finnhub en `analyze_symbol`) se pide UNA sola vez
    y se reusa entre los 3 perfiles — Finnhub NO se triplica, solo se triplica lo que de verdad
    depende del perfil (selección de strikes, scoring, narración de Claude). De paso se corrigió
    una redundancia previa que pedía las noticias de Finnhub dos veces por símbolo por corrida
    (una en `_refresh_news_for_symbol`, otra en `process_symbol_alerts`).
    **Costo real medido** (13 símbolos): agresivo=33, moderado=26, conservador=14 candidatos →
    ~73 llamadas al narrador de Claude por corrida de los 3 perfiles vs. ~33-38 de un solo
    perfil (~2x, no 3x — conservador filtra mucho más por su IV Rank mínimo más alto). Tiempo:
    ~184s → ~330s para 13 símbolos.
    **Fix necesario que esto forzó**: la clave de dedup (`alerts/dedup.py::build_dedup_key`) no
    incluía el perfil de riesgo — dos perfiles que elegían el mismo strike para el mismo
    símbolo/estrategia colisionaban y solo se guardaba el primero. Ya corregido (se agregó
    `risk_level` a la clave).
    **La página Alertas** (`1_alertas.py`) ya no tiene botón de "regenerar por perfil" (ese fue
    un diseño intermedio que el usuario pidió reemplazar) — ahora el selector de perfil y uno
    nuevo de estrategia (Naked Put/Covered Call/Collar/Iron Condor, agrupando `cash_secured_put`
    + `short_put_naked` bajo "Naked Put") son **filtros puros y combinables** sobre las alertas
    ya generadas, igual de accesibles que el filtro de Símbolo. Verificado con captura real:
    "Agresivo" + "Naked Put" mostró solo las 2 alertas de ese cruce exacto.
    **Efecto secundario a tener en cuenta**: el selector de perfil en Configuración YA NO
    influye en qué alertas se generan (antes era la única fuente de verdad) — queda vigente
    solo como referencia/override de umbral para callers de un solo perfil (tests). El texto de
    esa página todavía no se actualizó para reflejar esto — pendiente, ver abajo.
23. **Portafolio Entrega 3 (análisis de exposición con IA)**: la pieza que faltaba del
    portafolio real. Concentración por símbolo subyacente (% del valor total, valor absoluto
    para que cortos y largos sumen exposición en vez de cancelarse) y clusters de earnings
    simultáneos (símbolos cuya próxima fecha de earnings cae dentro de una ventana de 10 días
    entre sí) — ambos con reglas fijas en `portfolio_analysis.py`, sin que la IA decida ni
    calcule nada, mismo principio que el narrador de alertas. `dashboard/portfolio_narration.py`
    (nuevo, mismo patrón de fallback que `alerts/narrator.py`) redacta 3-5 frases sobre esos
    números ya calculados. Las fechas de earnings se piden en vivo a Finnhub por cada
    subyacente del portafolio (no desde `indicator_snapshots`) para cubrir posiciones en
    símbolos fuera de la watchlist configurada. Verificado en vivo con el portafolio real:
    OKLO 19.3% y NVDA 15.5% como mayores concentraciones, cluster de 13 símbolos con earnings a
    fin de julio detectado correctamente.

24. **Advertencia de liquidez (spread bid/ask ancho)** — trabajo autónomo, no pedido puntual
    del usuario, tomado del backlog "menor" de abajo. Bid/ask/OI/volumen ya eran reales de
    Schwab pero nadie los usaba para nada. Si el spread bid/ask de una pata VENDIDA supera el
    15% de su precio medio, se agrega una advertencia (mismo lugar/estilo que el caveat de
    earnings) tanto en el texto de WhatsApp como en la tarjeta — nunca descarta la alerta, solo
    advierte. Requirió sumar `bid`/`ask` a los legs persistidos (antes solo se guardaba
    `mid_price`). Verificado con datos reales: 11 de 69 alertas de una corrida dispararon la
    advertencia.

25. **Advertencia de riesgo de asignación anticipada por ex-dividendo** — trabajo autónomo,
    tomado del backlog "menor". Schwab expone `divExDate`/`nextDivExDate` (fundamental) pero
    nadie los usaba. Si una call VENDIDA (Covered Call, Collar, o el lado call de Iron Condor)
    sigue viva en o después de la próxima fecha ex-dividendo, se advierte sobre el riesgo de
    que la ejerzan antes para capturar el dividendo. **Bug real encontrado y corregido durante
    la verificación en vivo**: `divExDate` de Schwab NO es siempre la fecha futura — probado
    con dos símbolos reales el mismo día: JNJ la tenía futura (2026-08-25, correcta), QQQ la
    tenía en el PASADO (2026-06-22, ciclo ya pagado) con la fecha real en `nextDivExDate`
    (2026-09-22). Se corrigió tomando la más próxima entre ambos campos que sea hoy o futura,
    no un campo fijo — quedó cubierto con tests que reproducen ambos casos reales. Requirió
    sumar `next_ex_dividend_date` a `Quote` y a `IndicatorSnapshot` (con su migración liviana
    en `storage/db.py`, mismo patrón que `next_earnings_date`).

**Anotado, sin resolver todavía — problema de facturación del usuario, no de código**: durante
una de las regeneraciones de hoy la cuenta de Anthropic se quedó sin crédito
(`Your credit balance is too low`) — las alertas de ese momento cayeron al comentario de
fallback genérico en vez de narración real de Claude (el sistema ya está diseñado para esto,
nunca se pierde una alerta por un fallo del LLM, pero sí se pierde la calidad del comentario).
Confirmado que se resolvió (recargó crédito) porque corridas posteriores volvieron a mostrar
`fuente de la narración: claude`.

## Resuelto hoy

1. **Schwab Trader API verificada en vivo**: login OAuth, quotes, historial de precios y
   cadena de opciones — griegos/IV/open interest/volumen confirmados reales con un contrato
   ATM (no aproximados). `broker.mode: schwab` ya está activo en `config/settings.yaml`.
2. **Fallback de Black-Scholes mejorado**: usa tasa libre de riesgo y dividend yield reales
   de Schwab (chain-level) en vez del valor fijo de config — solo importa en los casos raros
   donde Schwab no da griegos directos.
3. **Bug crítico resuelto — IV Rank contaminado**: `iv_snapshots` tenía 34 sesiones por
   símbolo, 33 de datos **mock** de desarrollo previo mezclados con 1 sola real de hoy. El IV
   Rank comparaba hoy contra un historial mayormente falso (por eso valores pegados en 100.0
   para 7 símbolos distintos). Se limpiaron `iv_snapshots`/`indicator_snapshots` (conservando
   solo el día real) y se vació `alerts`/`candidate_contracts` para regenerar desde cero.
   Confirmado: los 13 símbolos ahora muestran `iv_rank_source = historical_volatility_proxy`
   (correcto para el día 1 de datos reales — pasa a IV real recién a los ~20 sesiones) con
   valores variados y creíbles (5.07 a 98.08). 111 alertas generadas limpias con datos 100%
   reales de Schwab.
4. **Cálculos verificados** para las 4 estrategias prioritarias (ver pendientes) con tests
   unitarios exactos (breakeven/max profit/max loss calculados a mano): Cash-Secured Put,
   Iron Condor, Covered Call, Collar. El motor de payoff es genérico — mismo código sin
   importar si el contrato viene de mock o de Schwab real.
5. **Calendario económico ampliado**: Finnhub `/calendar/economic` da 403 en el plan free
   (confirmado con la key real) — el fallback ahora trae fechas exactas de CPI/empleo/PBI vía
   FRED `/release/dates`, además de FOMC. Se dejó de filtrar impacto "bajo".
6. **Página "Eventos de riesgo"**: calendario combinado FOMC/CPI/empleo/earnings + sección
   nueva de calendario de earnings de toda la watchlist, ordenado por fecha próxima.
7. **Digest pre-apertura** (`job_premarket_digest`, 09:15 ET, configurable en
   `scheduler.premarket_digest_time`): corre el análisis completo y guarda un resumen
   (eventos de riesgo del día + alertas nuevas) en la tabla `notifications` — base para la
   campanita del dashboard (ver pendientes, la UI todavía no existe).
8. **Notifier a Telegram**: implementado y funcional, pero **inerte a propósito** — se
   priorizó la campanita del dashboard en vez de Telegram. Queda listo para activar más
   adelante (solo hace falta cargar `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` en `.env`).
9. **Scheduler corriendo con `launchd`** (no `nohup`): sobrevive cerrar la terminal y
   reinicios de sesión. 4 jobs activos: digest pre-apertura, apertura, polling cada 30 min,
   cierre. Ver sección de abajo sobre sleep/apagado.
10. **Acceso a cuentas reales de Schwab verificado** (solo lectura, sin trading): endpoint de
    posiciones probado contra la cuenta real. Aparecen 2 de 3 cuentas vía
    `/accounts/accountNumbers` — pendiente revisar por qué la tercera no aparece (posible
    permiso de API no habilitado en el portal de Schwab para esa cuenta específica).
11. **Motor enfocado en 4 estrategias** (Cash-Secured Put/Short Put Naked, Covered Call,
    Collar, Iron Condor) vía `settings.strategy.enabled` en `config/settings.yaml` — las otras
    15 quedan en el código sin borrar, alcanza con agregarlas a esa lista para reactivarlas.
    Además, `BrokerClient.get_all_share_positions()` (nuevo) reemplaza la tabla interna
    `assigned_positions` (vacía, sin UI que la llene) por la tenencia REAL de la cuenta Schwab
    para habilitar Covered Call/Collar — probado en vivo, detecta las 300 acciones reales de
    NVDA y otras 11 posiciones reales. 179/179 tests pasando.
12. **Campanita de notificaciones** (`render_notification_bell`, sidebar en las 7 páginas):
    badge con conteo de no leídas, popover con el detalle completo de cada notificación, botón
    "marcar todas como leídas". Probada en el navegador real.
13. **Tarjeta de alerta modernizada**: emojis reemplazados por íconos SVG outline (estilo
    Lucide, `icon()` helper en `components.py`, sin librería/CDN) en toda tarjeta/panel HTML
    propio; color semántico por contexto (beneficio verde, pérdida roja) en vez de fijo.
    Mockup previo aprobado en un artifact antes de aplicar. Limitación real de Streamlit (no
    de diseño): botones/popover/dataframes no aceptan HTML, ahí sigue emoji. Texto que se
    copia para WhatsApp (`alerts/formatting.py`, texto plano) usa un set de emoji más
    consistente en vez de SVG (no se puede poner SVG en WhatsApp).
14. **Score de convicción en %** con 2 colores (verde ≥70%, amarillo debajo) — de paso se
    encontró y corrigió `conviction_threshold_override` pisado en 5 desde una prueba de hace
    2 días (debía ser 55-75 según perfil), causaba alertas de score bajo.
15. **Perfil de riesgo (Conservador/Normal/Agresivo) ahora ajusta la selección real de
    strikes**, no solo qué se muestra: delta objetivo (0.15/0.25/0.35) e IV Rank mínimo para
    vender (60/50/40) por perfil, threadeado hasta `strategy/candidates.py`. Confirmado con
    AAPL real: conservador $305 de strike, normal $315, agresivo $322.5 (precio $331.77).
16. **Portafolio real, Entrega 2** (`dashboard/portfolio_analysis.py`, módulo puro): % de
    retorno por posición, proyección de P&L a vencimiento propio (solo intrínseco, sin IA),
    y proyección a fecha elegida por el usuario (Black-Scholes manteniendo precio/IV
    actuales, IV en vivo vía fetch de cadena solo al apretar "Calcular" — no ralentiza la
    carga normal). `AccountPosition` ahora parsea el símbolo OCC de cada opción (formato
    estable, no depende de `description`). Probado en vivo: portafolio pasa de -$23,674 hoy
    a -$17,427 proyectado al 2026-10-22.
17. **Escaneo de universo amplio** (`dashboard/pages/8_escaneo.py`): universo = watchlist fija
    (13, `config/symbols.yaml`) + watchlist real de thinkorswim (96, `config/watchlist_thinkorswim.yaml`,
    siempre incluidos sin importar el ranking) + `config/universe_sp500.yaml` (386 large-caps de
    referencia, NO es un feed en vivo — Schwab/Finnhub no tienen endpoint de "dame el S&P 500
    actual"). Fase 1 (`SchwabBrokerClient.screen_universe`, gratis/rápida): 1-2 llamadas batch
    filtran por optionable/precio/liquidez y rankean por volatilidad histórica (rango 52 semanas
    ÷ precio) — probado en vivo, 385 símbolos → 60 candidatos en 1 segundo. Fase 2 (cara, varios
    minutos): corre el pipeline existente sobre shortlist + ambas watchlists, gatillada por botón
    explícito, no automática. De paso: `get_quote`/`get_quotes` ahora soportan índices
    (`$SPX`/`$RUT`/`$NDX`/`$VIX`, confirmados en vivo con precios reales — no tienen bid/ask).
    **Corrección 2026-07-24**: una entrada anterior de esta nota decía que la lista real de
    thinkorswim ya estaba integrada — no era cierto, solo se había recibido y acordado, nunca
    escrita a un archivo ni unida en `/escaneo`. Confirmado y corregido en vivo (ver auditoría
    del usuario más abajo).

## Watchlist real del usuario (thinkorswim) — ya disponible

Lista completa (96 símbolos) en `config/watchlist_thinkorswim.yaml`, usada como prioritaria en
el escaneo de universo (punto 17 arriba, siempre incluida sin importar el ranking). Aclaraciones
pendientes de confirmar con el usuario:
- **BTC**: en Schwab resuelve a una ETF apalancada (`assetSubType: ETF`, ~$28-29, rango 52
  semanas $25-56) — **NO es Bitcoin spot**. Si el usuario quiere BTC real, hace falta el
  formato `BINANCE:BTCUSDT` (confirmado funcional en otra prueba de esta sesión), pero la
  llamada vía `/{symbol}/quotes` con `:` en el path falló (400) — probablemente necesita ir
  por el endpoint batch (`/quotes?symbols=...`) en vez del de un símbolo. No resuelto todavía.
- **SPX/RUT/NDX/VIX**: SÍ funcionan, pero con el prefijo `$` (`$SPX`, `$RUT`, `$NDX`, `$VIX`)
  — confirmado en vivo con precios reales. Falta validar si la cadena de OPCIONES de estos
  índices funciona igual (podrían ser cash-settled/europeas, distinto del resto del motor) —
  no probado todavía, no están en el universo de estrategias actual.

## Pendiente — orden de prioridad para retomar

0. **BLOQUEADO, esperando respuesta del usuario (preguntado 2026-07-24, no autónomo — pidió
   confirmar antes de tocar el motor)**: refinar la selección de strikes por perfil de riesgo
   agregando "cobertura mínima" (ya existe el cálculo, punto 21 arriba, falta usarlo como
   FILTRO en `candidates.py`/`selector.py`, no solo mostrarlo) y "calidad del soporte técnico"
   (el strike vendido debe apoyarse en SMA8 para conservador, SMA8 o SMA20 para normal/agresivo).
   Propuesta técnica que mandé y preguntas abiertas (sin responder todavía):
   - Definición propuesta de "buen soporte": precio actual > SMA de referencia (la media
     todavía actúa de piso) Y strike vendido ≤ esa SMA (la media queda entre el precio y el
     strike, colchón técnico adicional al del delta).
   - Pregunta 1: si el strike del delta objetivo no llega al mínimo de cobertura del perfil
     (12% normal / 8% agresivo), ¿descarto el candidato o busco el siguiente strike más OTM
     hasta cumplir el mínimo? Yo me inclino por buscar el siguiente strike, sin confirmar.
   - Pregunta 2: ¿el chequeo de soporte aplica simétrico al lado calls vendidas (Covered Call,
     lado call del Iron Condor) usando la SMA como resistencia? Asumido que sí, sin confirmar.
   - Pregunta 3: delta objetivo de Normal — el usuario dijo ~0.20, hoy en `settings.yaml` está
     en 0.25. ¿Confirma el cambio a 0.20?
   No tocar `strategy/candidates.py` ni `strategy/selector.py` para esto hasta tener las 3
   respuestas — es lógica que afecta directamente qué candidatos se generan y alertan.
1. **BTC real (spot) y opciones sobre índices** ($SPX/$RUT/$NDX/$VIX): ver aclaraciones arriba
   — necesita confirmación del usuario sobre qué quiere (BTC: ETF apalancado vs. spot real) y
   una prueba en vivo de la cadena de opciones de índices antes de sumarlos al motor.
2. **Chat de IA para consultas** sobre alertas/watchlist (mismo narrador, Claude Haiku, con
   contexto de la DB actual).
3. ~~Portafolio real, Entrega 3~~ — **hecho hoy**, ver punto 23 arriba.
4. **Automatizar el escaneo de universo**: hoy es manual (botón en `/escaneo`). Evaluar si
   conviene sumarlo al scheduler (ej. una vez por semana en vez de cada 30 min — la Fase 2 es
   cara) una vez que el usuario lo haya probado manualmente un tiempo.
5. **Texto de la página Configuración desactualizado**: el selector de perfil de riesgo ahí ya
   no dispara qué alertas se generan (ver punto 22 arriba, ahora los 3 perfiles se generan
   siempre) — el texto de esa página todavía no lo aclara, puede confundir. Actualizar la
   próxima vez que se toque esa página.

## Menor / oportunidades identificadas, no implementadas

- Ninguna fuente actual (Schwab ni el plan free de Finnhub) expone target price de
  analistas — confirmado con pruebas reales (403 en ambos). Necesitaría otra fuente.
- ~~Ex-dividendo (`divExDate`/`nextDivExDate`)~~ — **hecho hoy**, ver punto 25 arriba.
- ~~Control de liquidez (spread bid/ask)~~ — **hecho hoy**, ver punto 24 arriba. Todavía no
  se usa open interest/volumen para nada — podría sumar una segunda señal de liquidez además
  del spread (ej. advertir si OI o volumen están muy bajos incluso con spread angosto).
- Rankings de mercado (ganadoras/perdedoras, más activas): **confirmado en vivo hoy** —
  `/marketdata/v1/movers/$SPX` (heredado de TD Ameritrade) funciona y devuelve top movers reales
  con symbol/precio/%change/volumen (probado: INTC -8.6%, NVDA -0.9%, T +4.7%, ...). Sin
  implementar todavía — falta decidir el caso de uso concreto (¿una sección nueva en el
  dashboard? ¿alimentar el escaneo de universo con esto en vez de/además de la lista fija?).
  Es una decisión de producto, no una limitación técnica — confirmar con el usuario antes de
  construir algo.
- Cripto vía Schwab `/quote` (BTC/ETH spot) confirmado gratis y funcional — anotado como
  "barato" en el backlog original, sin implementar.

## Estado del scheduler — leer antes de dejar la Mac

- **CRÍTICO, causó un bug real el 2026-07-24**: `launchd` mantiene el proceso del scheduler
  vivo entre commits — no se reinicia solo al cambiar código. Un commit a las 08:46 quedó
  corriendo con código viejo durante ~4hs y ~10 commits más (fix de emojis, deltas por perfil
  de riesgo, etc.), generando 234 alertas con la lógica vieja sin que nada lo avisara. Detectado
  comparando el timestamp de la alerta más reciente contra el emoji/lógica que debía tener según
  el commit vigente. **Regla en adelante: después de CUALQUIER commit que toque
  `scheduler/jobs.py` o algo de lo que depende (candidates.py, config.py, selector.py, etc.),
  correr `launchctl kickstart -k gui/$(id -u)/com.robertoajemblat.options-income-advisor.scheduler`
  antes de dar el cambio por aplicado.** El dashboard de Streamlit tiene el mismo problema con
  módulos que no son el archivo de la página actual (ej. `config.py`): el hot-reload de
  Streamlit no siempre los recarga — si un cambio a un módulo compartido no aparece en el
  navegador tras refrescar, matar y relanzar el proceso de `streamlit run` resuelve.
- **Confirmado corriendo ahora** (`launchctl print gui/$(id -u)/com.robertoajemblat.options-income-advisor.scheduler` → `state = running`).
- **Sobrevive**: cerrar la terminal, cerrar sesión y volver a entrar, que la app del
  dashboard se caiga. `launchd` la reinicia sola (`KeepAlive`, `ThrottleInterval: 60s`).
- **Con la Mac en sleep (tapa cerrada)**: el proceso se congela — el digest de las 09:15 (u
  otro job programado durante el sleep) **no dispara ni se pone al día solo** al despertar.
  `caffeinate -i` quedó corriendo en background para evitar sleep por inactividad, pero **no
  puede evitar el sleep que fuerza el hardware al cerrar la tapa** en una laptop standalone.
- **Con la Mac apagada completamente**: nada corre. `launchd` recién vuelve a levantar el
  agente al iniciar sesión de nuevo, no solo con prender la máquina.
- **Plan B si se durmió/apagó**: al volver a la compu, abrir el dashboard
  (`http://localhost:8501` si `streamlit` ya está corriendo vía `nohup`, o
  `.venv/bin/streamlit run src/options_advisor/dashboard/app.py` si no) y apretar
  "🔄 Correr análisis ahora" — con `broker.mode: schwab` activo, trae datos 100% reales al
  toque, solo que no automático.

## Comandos útiles

```bash
# ver estado del scheduler
launchctl print gui/$(id -u)/com.robertoajemblat.options-income-advisor.scheduler

# logs del scheduler
tail -f data/logs/scheduler.err.log

# pausar/desinstalar el scheduler
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.robertoajemblat.options-income-advisor.scheduler.plist

# correr los tests
.venv/bin/python -m pytest -q
```
