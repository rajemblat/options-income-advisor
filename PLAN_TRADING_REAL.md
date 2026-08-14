# Plan para pasar Lokshn a trading REAL en Schwab

_Preparado el 7 de agosto de 2026. Objetivo: arrancar la semana que viene conectando el robot a tu cuenta real de Charles Schwab con la máxima seguridad, empezando muy chico y escalando solo cuando esté probado._

---

## Lo primero: la promesa de seguridad

Tu miedo principal es "que me ponga más de lo que pedí". Eso ya está resuelto **por diseño**, no por buena voluntad del código.

Construí un **guardián de órdenes** (`execution/live_guard.py`) que se para delante de cualquier envío al broker. Tiene una regla que no se puede violar: **la cantidad final de contratos nunca supera lo que pediste**. Está garantizada con un `assert` en el código y cubierta con 24 tests automáticos (incluido uno que prueba con 1, 2, 3, 4, 7, 20 y 100 contratos pedidos y verifica que jamás sale más de lo pedido ni del tope).

Además, **hoy todo está apagado**: el robot no puede mandar ni una sola orden real hasta que vos, presente, lo actives. El interruptor maestro (`enabled`) viene en `false`.

---

## Cómo está el robot hoy

- El robot **lee** de Schwab en vivo: precios, cadenas de opciones, tus posiciones y tus órdenes ya ejecutadas. Eso funciona y está probado.
- El robot **NO coloca órdenes**: toda la operación es simulada en una base local con datos reales de mercado. Cuando ves "abrió un put", lo abrió en la simulación, no en tu broker.
- O sea: para operar real hay que construir **una capa nueva** que traduzca "el robot decidió abrir/cerrar" en "mandá esta orden a Schwab" — siempre detrás del guardián.

---

## Los topes duros que ya dejé configurados

Todos apagados hasta activarlos, pero listos. Son un **segundo cinturón**, redundante con la estrategia, revisado justo antes de cada envío:

| Tope | Valor inicial | Qué hace |
|---|---|---|
| `enabled` | **false** | Maestro. En false NUNCA se manda una orden real. |
| `dry_run` | **true** | Arma la orden y la registra, pero NO la envía (simulacro). |
| `require_manual_arm` | **true** | Hay que "armar" el trading real **cada día** desde el dashboard. |
| `kill_switch` | false | Freno de emergencia: corta todo, aun estando armado. |
| `max_contracts_per_order` | 4 | Nunca más de 4 contratos por orden. |
| `max_notional_per_order` | $40.000 | Nunca más de ese valor de subyacente por orden. |
| `max_orders_per_day` | 5 | Máximo 5 órdenes reales por día. |
| `max_total_deployed` | $50.000 | Tope de capital comprometido total en el día. |
| `max_underlying_price` | $700 | No opera acciones más caras que esto. |
| `min_account_cash_buffer` | $0 | Cash que siempre queda libre (lo subimos cuando definas). |
| `allowed_symbols` | (vacío) | Si lo llenás, SOLO opera esos símbolos (whitelist). |

Cuando arranquemos, mi recomendación es empezar **mucho más conservador** que estos números (ver Fase 1).

---

## Qué falta construir (mi trabajo esta semana)

1. **Colocación de órdenes en Schwab** (`execution/schwab_orders.py`, nuevo): arma el JSON de una orden de opción (SELL_TO_OPEN de put cash-secured, orden LÍMITE, nunca market) y la manda al endpoint `POST /accounts/{hash}/orders`. En modo `dry_run` construye y loguea el payload sin enviarlo — así lo revisás sin riesgo.
2. **Idempotencia (anti-duplicados)**: cada intención de orden lleva una clave única; si por un reintento o un doble clic se procesa dos veces, la segunda se ignora. Esto evita el clásico "mandó la orden dos veces".
3. **Reconciliación**: después de mandar, el robot confirma contra Schwab qué se llenó DE VERDAD (cantidad y precio reales) y lo compara con lo que pidió. Si no coincide, frena y avisa — no sigue operando a ciegas.
4. **Pre-flight (chequeo previo)** antes de cada orden: ¿el mercado está abierto?, ¿el token vale?, ¿hay cash?, ¿el spread bid/ask no está raro?, ¿la cadena tiene liquidez?, ¿no hay ya una posición en ese símbolo?
5. **Panel de trading real en el dashboard**: botón grande de ARMAR/DESARMAR el día, el kill switch, y una vista de "órdenes reales de hoy" con estado (pendiente/llenada/rechazada). Todo con confirmación doble.
6. **Manejo de rechazos de Schwab**: si el broker rechaza (fondos, permisos, precio), lo registra con el motivo claro y no reintenta a lo loco.

---

## Plan por fases (así arrancamos sin sustos)

**Fase 0 — Preparación (esta semana, sin tocar tu cuenta).**
Termino la capa de ejecución en modo `dry_run`. El robot "haría" órdenes reales pero solo las loguea. Vos revisás que el payload y las cantidades sean exactamente lo que esperás.

**Fase 1 — Primera orden real, mínima (juntos, con vos mirando).**
- `allowed_symbols`: **un solo símbolo** barato y líquido que elijas.
- `max_contracts_per_order`: **1**.
- `max_orders_per_day`: **1**.
- Armamos el día, esperamos que dispare UNA orden de 1 contrato, y la miramos llenarse en Schwab.
- Reconciliamos: ¿lo que se llenó es idéntico a lo que pidió? Si sí, seguimos.

**Fase 2 — Ampliar despacio.**
Subimos a 2-3 símbolos y a tus tramos reales de contratos (hasta $50 → 4, etc.), pero manteniendo `max_orders_per_day` bajo (2-3) hasta ver varios días limpios.

**Fase 3 — Operación normal.**
Recién acá levantamos los topes a lo que definiste (5/día, etc.). El kill switch y el "armar cada día" quedan para siempre.

---

## Lo que necesito de vos (cuando vuelvas)

1. **Permisos de trading en la API de Schwab.** Tu app de developer en Schwab tiene que tener habilitado **Trading** (no solo lectura de market data). Hay que confirmarlo en el portal de developer y quizás regenerar credenciales.
2. **El hash de la cuenta** donde vas a operar (si tenés más de una cuenta vinculada, elegimos la correcta explícitamente — nunca "la primera que aparezca").
3. **El símbolo de la Fase 1** y cuánto cash querés dejar SIEMPRE intocable (`min_account_cash_buffer`).
4. Confirmar los tramos y topes finales (los de arriba son propuesta).

---

## Sobre que "sea rápido"

El robot ya usa quotes en lote (100+ símbolos por llamada) y caché. Para trading real voy a:
- Mantener la sesión HTTP viva (sin reabrir conexión en cada llamada).
- Refrescar el token de forma proactiva (Schwab lo vence; el límite de 7 días ya está manejado).
- Colocar y reconciliar en el mismo ciclo para no perder tiempo entre "mandé" y "confirmé".
- Loguear tiempos de cada paso para detectar cualquier demora.

---

## Riesgos y cómo los tapamos

| Riesgo | Mitigación |
|---|---|
| Manda más de lo pedido | Imposible por diseño (guardián + assert + tests). |
| Orden duplicada | Clave de idempotencia. |
| Se llena distinto a lo pedido | Reconciliación que frena y avisa. |
| Token vencido a mitad de operación | Refresh proactivo + pre-flight de auth. |
| Cuenta equivocada | Hash de cuenta elegido explícitamente, no automático. |
| Precio malo (spread raro) | Solo órdenes límite + chequeo de spread en pre-flight. |
| Querés frenar YA | Kill switch + desarmar el día, cortan todo al instante. |

---

## Estado del código a hoy

- Guardián de seguridad: **construido y testeado** (24 tests). Apagado por default.
- Config de trading real: **agregada**, toda en off.
- 873 tests del proyecto pasan.
- Nada de esto envía órdenes todavía — es andamiaje seguro para revisar.

_Cuando vuelvas, arrancamos por la Fase 0/1. Sin apuro y sin sorpresas._

---

## Especificaciones de ejecución CONFIRMADAS (usuario, 9 de agosto)

### Negociación del spread (naked puts) — "caminar el precio"
Replicar cómo el usuario opera a mano para capturar el spread:

- **Al VENDER el put (abrir):** empezar la orden límite **2 centavos ($2/contrato) por debajo del ask** y, si no llena, **bajar de a 2 centavos** cada paso hasta que se ejecuta. Piso: **NO bajar del mid price** (ahí frena; no regala más de medio spread).
- **Al RECOMPRAR el put (cerrar):** empezar **2 centavos por encima del bid** y **subir de a 2 centavos** hasta que llena. Techo: **NO pasar del mid price**.
- **Tiempo entre pasos:** esperar **máximo 30 segundos** antes de reemplazar la orden con el nuevo precio (order replace).
- Cada reemplazo cuenta contra el Order Limit de la app (por eso se subió a 120/min).

### Arranque diario manual (doble confirmación)
- Cada mañana el usuario tiene que apretar **START** en el dashboard para que el robot empiece a operar real ese día. **Doble confirmación** antes de arrancar.
- Si no le da Start, el robot NO manda ninguna orden real ese día (queda solo en simulación/lectura).
- Esto es exactamente el `require_manual_arm` del guardián de seguridad (ya construido). El botón START = "armar el día".
- Complementos ya previstos: **kill switch** (freno de emergencia) y **desarmar** (frena lo nuevo, sigue gestionando lo abierto).

### Pendiente de definir con el usuario
- Símbolo de la Fase 1 (uno barato y líquido).
- Cash intocable (colchón mínimo que el robot nunca usa como garantía).

---

## Actualización 9 de agosto (parte 2) — decisiones y avances

### Cambios de configuración (Fase 1, sigue APAGADA)
- **Colateral máximo: $10.000** — medido por el MARGEN real que traba el broker (~$300 NU, ~$1.200 C), NO por strike×100. Así con $10K se hacen varias operaciones. (El tope por EXPOSICIÓN diaria se agrega "más adelante", como pediste.)
- **SPY exento del tope de $700**: se opera aunque valga más. Whitelist Fase 1 = AA, AAL, NU, DLO, DAL, BAC, C, WFC, **SPY**.
- 1 contrato/orden · 1 orden/día · 5/semana · solo si se dan los parámetros.

### Negociación del precio — ahora ADAPTATIVA (construido: `execution/price_walker.py`)
El paso NO es fijo en 2 centavos: se adapta al spread de cada opción.
- Spread normal → paso base de 2 centavos ($2/contrato).
- Spread ancho → paso más grande (hasta 1/4 del spread, tope 10 centavos) para llenar en tiempo razonable.
- Spread finito (opción barata) → paso chico, hasta 1 centavo, para no saltear buenos precios.
- Vender: arranca bajo el ask y baja; recomprar: arranca sobre el bid y sube. **Nunca cruza el mid.**
- Reemplaza la orden a lo sumo cada 30 s. Todo probado (15 tests).

### Dos pestañas (pedido del usuario)
- **Simulador**: queda IGUAL. Sigue operando en paper con datos reales y el usuario puntúa todo para que el robot aprenda. No se toca.
- **Real Market** (nueva, a construir): réplica del Simulador pero con órdenes REALES detrás del guardián. Mismo look, misma info, mismos controles — más el botón START diario y el kill switch.

### Aprendizaje (se retroalimenta de TODO)
El robot se hace más experto combinando: (1) tus operaciones REALES, (2) el simulador, (3) el backtesting, (4) tu puntuación de estrategias. Todo alimenta la capa de aprendizaje.

### Estado de código
- Guardián: topes diario/semanal, colateral por margen, exención de precio (SPY). 
- Price walker adaptativo: construido y testeado.
- 896 tests pasan. Todo sigue APAGADO (enabled=false, dry_run=true).

### Lo que sigue (próximo build)
- `execution/schwab_orders.py`: armar y (en dry-run) loguear la orden real a Schwab, usando el price_walker para caminar el precio y la reconciliación.
- Pestaña **Real Market** en el dashboard con START/doble confirmación y kill switch.
