# Backlog consolidado

Registro vivo de todo lo pedido, para no perder el hilo en sesiones largas. Se actualiza cada
vez que algo arranca o termina — no es un historial (eso está en `NOTES.md` y en `git log`),
es el estado ACTUAL de qué falta.

Última actualización: 2026-07-26.

## En progreso ahora

- **Calendario de earnings con búsqueda por semana o rango de fechas** en Eventos de Riesgo —
  arrancando (ver tarea 1 de "Pendiente" para el detalle).

## Pendiente, no empezado

1. **Simulador de escenarios en Portafolio real**: selector alcista/bajista/neutral + % de
   movimiento, aplicado por igual a todos los subyacentes de posiciones abiertas, recalculado
   con el motor de `payoff.py` existente. Muestra total proyectado vs. hoy (diferencia $ y %).
   Disclaimer de que es una simplificación (todo se mueve igual), no una predicción real.
2. **Pestaña "Operaciones" — réplica automática de operaciones reales** (pedido 2026-07-25):
   detectar en tiempo real cuando se abre una posición nueva en la cuenta real de Schwab (ej.
   vender 1 Put de TSLA strike 320 vence 21/8) y generar automáticamente una "alerta" con el
   mismo formato completo de las alertas de oportunidades (P&L, breakeven, POP, % cobertura,
   noticias recientes, comentario del narrador) pero aplicada a la operación YA ejecutada, no
   a una sugerencia. Después de las 3 tareas de arriba. Respuestas a las 3 preguntas de diseño:
   1. Detección: comparar el snapshot de `get_all_positions()` de la corrida actual contra el
      snapshot guardado de la corrida anterior (nueva tabla, ej. `position_snapshots`, con
      symbol OCC + quantity por cuenta) — una posición de tipo OPTION que aparece nueva o con
      cantidad corta mayor que antes = operación nueva. Usa el parseo OCC que ya existe en
      `AccountPosition` (broker/models.py), no depende de `description`.
   2. Frecuencia: la misma corrida del scheduler existente (`job_poll_and_analyze`, cron
      `*/{poll_interval_minutes}` en horario de mercado — ver `scheduler/runner.py`), no un
      scheduler aparte — evita pegarle dos veces a la API de Schwab y mantiene todo el pipeline
      sincronizado a un solo "tick".
   3. Sí, tabla separada (ej. `real_trade_alerts`) y página propia "Operaciones" — no mezclar
      con `candidate_contracts`/`alerts`, que representan sugerencias no ejecutadas. Mezclarlas
      rompería la semántica actual de esas tablas (candidato vs. hecho real) y complicaría
      cualquier reporte futuro que separe "lo que sugerimos" de "lo que hiciste".

## Bloqueado — esperando al usuario (de antes de hoy, sigue vigente)

- **BTC real (spot) y opciones sobre índices** ($SPX/$RUT/$NDX/$VIX): necesita que el usuario
  confirme qué quiere (BTC: ETF apalancado que ya trae Schwab vs. spot real vía
  `BINANCE:BTCUSDT`) y una prueba en vivo de la cadena de opciones de índices antes de sumarlos.

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

## Cómo se usa este archivo

- Antes de arrancar algo nuevo: agregarlo a "Pendiente, no empezado" apenas se pide, aunque
  no se vaya a implementar en el momento.
- Al arrancar: mover a "En progreso ahora" (solo debería haber 1, salvo casos puntuales).
- Al terminar y verificar: mover a "Terminado y verificado" con la fecha, y borrar el detalle
  largo de acá (ese detalle vive en `NOTES.md`, acá solo una línea).
