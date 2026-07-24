# Backlog consolidado

Registro vivo de todo lo pedido, para no perder el hilo en sesiones largas. Se actualiza cada
vez que algo arranca o termina — no es un historial (eso está en `NOTES.md` y en `git log`),
es el estado ACTUAL de qué falta.

Última actualización: 2026-07-24.

## En progreso ahora

- **Calculadora de interés compuesto** en la página Configuración — arrancando.

## Pendiente, no empezado

1. **Buscador de noticias por símbolo libre** en la página Noticias: cualquier símbolo (no solo
   watchlist), cotización + noticias en tiempo real al buscar, error claro si el símbolo no
   existe. Pregunta del usuario sin responder todavía: ¿llamada en vivo cada vez, o cachear
   ~5 min para no golpear rate limits si se repite la búsqueda?
2. **Calendario de earnings con búsqueda por semana o rango de fechas** en Eventos de Riesgo —
   selector de semana o rango desde/hasta, earnings de la watchlist (y opcionalmente universo
   amplio) dentro de ese rango, ordenados por fecha.
3. **Simulador de escenarios en Portafolio real**: selector alcista/bajista/neutral + % de
   movimiento, aplicado por igual a todos los subyacentes de posiciones abiertas, recalculado
   con el motor de `payoff.py` existente. Muestra total proyectado vs. hoy (diferencia $ y %).
   Disclaimer de que es una simplificación (todo se mueve igual), no una predicción real.

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

## Cómo se usa este archivo

- Antes de arrancar algo nuevo: agregarlo a "Pendiente, no empezado" apenas se pide, aunque
  no se vaya a implementar en el momento.
- Al arrancar: mover a "En progreso ahora" (solo debería haber 1, salvo casos puntuales).
- Al terminar y verificar: mover a "Terminado y verificado" con la fecha, y borrar el detalle
  largo de acá (ese detalle vive en `NOTES.md`, acá solo una línea).
