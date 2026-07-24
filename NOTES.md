# Estado del proyecto — sesión 2026-07-23 → 2026-07-24

Conexión real con Schwab: verificada, conectada y con un bug de datos crítico ya resuelto.
Todo lo de esta sección está commiteado y pusheado a `origin/main` (último commit: `8523c9c`).

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

## Pendiente — orden de prioridad para retomar

1. **Campanita de notificaciones en el dashboard**: el backend ya existe (tabla
   `notifications`, el digest ya escribe ahí) — falta el componente visual (badge con
   contador de no leídas, panel al hacer clic con el detalle). Reemplaza el plan original de
   Telegram.
2. **Formato de alerta rediseñado**: línea compacta (símbolo/hora/precio/POP bien
   destacado/crédito neto) + secciones "Razón" (por qué) y "Qué pasa si" (escenario
   alternativo/plan B) generadas por el narrador — hoy es una explicación plana.
3. **Página de portafolio real + análisis con IA**: diagnosticado y aprobado (endpoints de
   Schwab ya verificados — `GET /trader/v1/accounts/{hash}?fields=positions` funciona con la
   misma autorización que ya está hecha, no hace falta nada adicional; el fetch de posiciones
   ya está resuelto vía `get_all_share_positions()`, reusable acá). No implementado todavía.
   Se había acordado partirlo en 2 entregas: primero posiciones reales (tabla + griegos en
   vivo + caveats de earnings/FOMC reusados), después la capa de análisis por IA.
4. **Chat de IA para consultas** sobre alertas/watchlist (mismo narrador, Claude Haiku, con
   contexto de la DB actual) — pedido después de confirmar los puntos 1-3.
5. **Watchlist ampliada de thinkorswim**: quedó bloqueado — el usuario nunca pegó la lista
   real de símbolos (llegó vacía en el mensaje). Falta esa lista para clasificar en
   acciones/ETFs (directo), índices (SPX/RUT/NDX/VIX, revisar soporte), futuros (`/ES`,
   `/NQ`, etc., Schwab probablemente no los cubre) y cripto (BTC/ETH sí vía Schwab `/quote`
   con formato `BINANCE:...`; distinguir de ETFs cripto como IBIT/ETHA que son símbolos
   normales).

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
