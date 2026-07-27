# Backlog consolidado

Registro vivo de todo lo pedido, para no perder el hilo en sesiones largas. Se actualiza cada
vez que algo arranca o termina — no es un historial (eso está en `NOTES.md` y en `git log`),
es el estado ACTUAL de qué falta.

Última actualización: 2026-07-27 (madrugada, trabajo autónomo overnight — Pestaña Operaciones
terminada y verificada con una posición real).

## En progreso ahora

Ninguno — Pestaña Operaciones (ver "Terminado y verificado" #19) cerrada y verificada con una
posición real de la cuenta (TSLA, ver evidencia ahí). Sigue el punto 1 de "Pendiente" abajo
(Fed/FRED) en el orden confirmado por el usuario.

## Bug reportado y con mejora aplicada (no 100% confirmado) — 2026-07-27

- **Botón ☰ del sidebar, no respondía al primer clic** (reportado por el usuario, sospecha de
  regresión del simulador de inflación — descartado: ese commit no tocó `components.py`).
  Encontrado investigando: `stSidebarCollapseButton` es un `<div>` que ENVUELVE el `<button>`
  real, a diferencia de `stExpandSidebarButton` que es un `<button>` directo — asimetría real
  del DOM de Streamlit. Fix aplicado (commit `a3e9c6d`): la secuencia completa de eventos de
  puntero (pointerdown/mousedown/pointerup/mouseup/click) en vez de un solo `.click()`, tests
  331/331 en verde. **Honesto**: no pude reproducir el fallo de forma 100% consistente con
  clics automatizados del navegador para confirmar que este es EL root cause exacto — es una
  mejora real y defendible (la asimetría del DOM existe, se confirmó con JS en vivo), pero
  falta que el usuario confirme en su uso normal que el síntoma desapareció. Si vuelve a
  pasar, revisar de nuevo con este contexto ya investigado.

## Pendiente, no empezado — orden confirmado por el usuario 2026-07-26

1. **3 funcionalidades de Fed/FRED**: bloqueo de días de riesgo CPI/NFP, semáforo de
   volatilidad, alertas proactivas. Complejidad: **media** — `market_context/fred_client.py` y
   `economic_calendar.py` ya resuelven fechas de FOMC/CPI/empleo, es integración sobre una base
   ya construida, no invención desde cero.
2. **Calendario de earnings con búsqueda por semana o rango de fechas** en Eventos de Riesgo —
   selector de semana o rango desde/hasta, earnings de la watchlist (y opcionalmente universo
   amplio) dentro de ese rango, ordenados por fecha. Complejidad: **baja** — página de
   filtro/tabla sobre datos de earnings que ya se usan para el aviso de clusters simultáneos.
3. **Simulador de escenarios en Portafolio real**: selector alcista/bajista/neutral + % de
   movimiento, aplicado por igual a todos los subyacentes de posiciones abiertas, recalculado
   con el motor de `payoff.py` existente. Muestra total proyectado vs. hoy (diferencia $ y %).
   Disclaimer de que es una simplificación (todo se mueve igual), no una predicción real.
   Complejidad: **baja/media** — reusa el motor de payoff existente, con precedente directo
   (rendimiento anualizado + cierre anticipado ya hicieron algo similar).

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

## Cómo se usa este archivo

- Antes de arrancar algo nuevo: agregarlo a "Pendiente, no empezado" apenas se pide, aunque
  no se vaya a implementar en el momento.
- Al arrancar: mover a "En progreso ahora" (solo debería haber 1, salvo casos puntuales).
- Al terminar y verificar: mover a "Terminado y verificado" con la fecha, y borrar el detalle
  largo de acá (ese detalle vive en `NOTES.md`, acá solo una línea).
