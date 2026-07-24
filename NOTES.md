# Estado del proyecto — sesión 2026-07-23 → 2026-07-24

Conexión real con Schwab: verificada, conectada y con un bug de datos crítico ya resuelto.
Todo lo de esta sección está commiteado y pusheado a `origin/main` (último commit: `422b2c9`).

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

1. **BTC real (spot) y opciones sobre índices** ($SPX/$RUT/$NDX/$VIX): ver aclaraciones arriba
   — necesita confirmación del usuario sobre qué quiere (BTC: ETF apalancado vs. spot real) y
   una prueba en vivo de la cadena de opciones de índices antes de sumarlos al motor.
2. **Chat de IA para consultas** sobre alertas/watchlist (mismo narrador, Claude Haiku, con
   contexto de la DB actual).
3. **Portafolio real, Entrega 3 (análisis con IA)**: narrativa sobre exposición total,
   concentración, riesgo de earnings simultáneo — la Entrega 2 (cuantitativo, sin IA) ya está.
4. **Automatizar el escaneo de universo**: hoy es manual (botón en `/escaneo`). Evaluar si
   conviene sumarlo al scheduler (ej. una vez por semana en vez de cada 30 min — la Fase 2 es
   cara) una vez que el usuario lo haya probado manualmente un tiempo.

## Menor / oportunidades identificadas, no implementadas

- Ninguna fuente actual (Schwab ni el plan free de Finnhub) expone target price de
  analistas — confirmado con pruebas reales (403 en ambos). Necesitaría otra fuente.
- Schwab expone `divExDate`/`nextDivExDate` (fecha ex-dividendo) que no usamos — relevante
  para riesgo de asignación anticipada en Covered Call.
- No hay ningún control de liquidez (ancho de spread bid/ask, tamaño de book) antes de
  sugerir una estrategia — con plata real esto importa. Bid/ask/OI/volumen ya son reales,
  pero nadie los usa todavía para filtrar/advertir sobre iliquidez.
- Rankings de mercado (ganadoras/perdedoras, más activas): Schwab (heredado de TD Ameritrade)
  probablemente tiene un endpoint `/marketdata/v1/movers/{index}` — no verificado todavía,
  evaluar antes de buscar otra fuente.
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
