"""Capa de EJECUCIÓN de órdenes reales (a diferencia del simulador, que solo mueve la base local).

Este paquete es la frontera entre "el robot decidió abrir/cerrar" y "se manda una orden al broker
real". Está diseñado con seguridad primero (usuario 2026-08-07, preparando trading real en Schwab con
poco capital: "que nunca ponga más de lo que pedí, que sea perfecto"):

- `live_guard`: valida CADA orden contra topes DUROS antes de que exista cualquier envío. Garantiza,
  por construcción, que la cantidad final de contratos NUNCA supera lo pedido ni los topes.

Nada en este paquete envía órdenes todavía: la colocación real se agrega recién cuando el usuario esté
presente y lo confirme, y siempre detrás de este guard.
"""
