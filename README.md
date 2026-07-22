# Options Income Advisor — Fase 1

Motor de análisis y alertas de opciones para el escenario **Ingreso a Largo Plazo** (venta de
prima), según la hoja de ruta funcional del producto. Ver el plan completo en
`.claude/plans/snuggly-baking-micali.md` (o el equivalente que se te haya compartido).

Alcance de esta fase: conexión al broker, cálculo de IV Rank / Griegos / RSI / Medias Móviles /
Soportes-Resistencias sobre una lista de símbolos configurable, y alertas para Ingreso a Largo
Plazo únicamente. GEX, valor extrínseco, bid-ask spread, earnings y noticias son Fase 2/3.

## Estado actual

- **Broker**: `MockBrokerClient` (fixtures locales) por defecto. El acceso a la Schwab Trader
  API está pendiente de aprobación — ver la sección "Conectar con Schwab" más abajo.
- **LLM**: narración de alertas con `claude-haiku-4-5-20251001` vía la API de Anthropic. Si no
  hay `ANTHROPIC_API_KEY` configurada, o la llamada falla, se usa una plantilla local — una
  alerta nunca se pierde por un fallo del narrador.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # completar ANTHROPIC_API_KEY (y las de Schwab cuando lleguen)
python scripts/seed_fixtures.py   # genera datos simulados para los símbolos de config/symbols.yaml
```

## Uso

**Correr los tests:**
```bash
pytest
```

**Ver el dashboard** (con `broker.mode: mock` en `config/settings.yaml`):
```bash
streamlit run src/options_advisor/dashboard/app.py
```
Desde la página principal hay un botón "Correr análisis ahora" que corre el pipeline completo
una vez sin necesidad de levantar el scheduler.

**Correr el scheduler** (polling periódico en horario de mercado, ver Sección 6 del plan):
```bash
python scripts/run_scheduler.py
```

## Configuración

- `config/settings.yaml`: modo de broker, umbrales de convicción por perfil de riesgo, perfil
  de inversor por defecto, cadencia del scheduler, tasa libre de riesgo para el fallback de
  griegos.
- `config/symbols.yaml`: lista de símbolos a monitorear. Editable directamente, sin tocar código.
- El perfil de inversor también se puede editar desde el dashboard (página Configuración), lo
  que pisa el default de `settings.yaml`.

## Conectar con Schwab (cuando lleguen las credenciales)

1. Completar `SCHWAB_CLIENT_ID` y `SCHWAB_CLIENT_SECRET` en `.env`.
2. `python scripts/schwab_login.py` — login manual único (hay que repetirlo cada ~7 días,
   cuando vence el refresh_token; es una particularidad de la Schwab API).
3. `python scripts/verify_schwab_client.py AAPL SPY` — confirma que el mapeo de campos de
   `schwab_client.py` es correcto contra la API real (en particular, si Schwab expone griegos
   directamente o hace falta el fallback de `indicators/greeks.py`). **No verificado todavía.**
4. Cambiar `broker.mode: schwab` en `config/settings.yaml`. Ningún otro código cambia — toda
   la arquitectura de negocio programa contra la interfaz `BrokerClient`, no contra una
   implementación concreta.

## Limitaciones conocidas de Fase 1

- **IV Rank**: hasta acumular 12 meses de historial de IV real, se usa volatilidad histórica
  realizada como proxy (ver `indicators/volatility.py`). El dashboard muestra la fuente
  (`iv_rank_source`) y cuántas sesiones hay acumuladas.
- **Earnings**: todavía no se verifica si el vencimiento de la opción cae después de un
  reporte de resultados (es Fase 3). El narrador de alertas siempre agrega una nota pidiendo
  confirmar esto manualmente.
- **Griegos del fallback**: usan una tasa libre de riesgo fija (configurable) y no modelan
  dividendos por símbolo — simplificación aceptable dado que solo se usan cuando el broker no
  provee los griegos directamente.
