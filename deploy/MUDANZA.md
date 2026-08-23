# Mudanza del robot a un servidor

Guía completa del traslado de Lokshn desde la MacBook a un servidor propio, para que opere con la
Mac apagada. Escrita en agosto de 2026.

---

## Por qué no alcanza con copiar la carpeta

Tres cosas del robot estaban atadas a macOS y hubo que resolverlas antes de mudar nada:

**El reloj.** Hay 123 lugares en el código que preguntan la hora sin aclarar zona
(`datetime.now()`, `date.today()`). Esas llamadas siguen el reloj local de la máquina. En la Mac
eso es Nueva York; un servidor recién creado viene en UTC, y ahí `date.today()` cambia de día a las
20:00 hora de Nueva York — con el tope diario de órdenes reseteándose en plena tarde y las fechas
de entrada corridas. Nada de eso da error: los números simplemente salen distintos.

El arreglo tiene dos partes: el servidor se pone en `America/New_York`, y el robot **se niega a
arrancar** si el reloj no coincide con `settings.scheduler.timezone`
(`src/options_advisor/scheduler/zona_horaria.py`). Lo segundo es lo que importa: si algún día se
rehace el servidor y alguien se olvida del `timedatectl`, el robot no arranca en vez de operar mal.

Los horarios de mercado NO dependían de esto — `market_calendar.py` ya trabajaba en UTC y los
disparadores de APScheduler llevan `timezone=` explícito.

**El healthcheck.** Es lo que detecta al robot colgado "mudo" y lo reinicia. Reiniciaba con
`launchctl`, que solo existe en macOS, y avisaba por notificación nativa de macOS más Telegram
—que nunca se configuró—. En un servidor eso significaba un robot que se repara en silencio sin
que nadie se entere de que se rompió. Ahora elige `launchctl` o `systemctl` según el sistema, y
**manda un email**, que es el canal que el usuario realmente lee.

**Las versiones.** `pyproject.toml` declara mínimos (`pandas>=2.2`), así que una instalación limpia
trae lo último que haya ese día. Al probarlo, una instalación desde cero traía `anthropic 1.0.0`
donde la Mac corre `0.118.0`: un salto de versión mayor, en medio de una mudanza, sin ninguna
necesidad. Por eso existe `deploy/requirements-lock.txt`, con las versiones exactas que ya operan
con plata real.

Ahí también apareció que **`yfinance` no estaba declarado** en `pyproject.toml` aunque está
instalado en la Mac y el código lo usa como fuente gratis de respaldo para las fechas de earnings.
Como se importa dentro de un `try/ImportError`, el robot no habría fallado en el servidor: habría
perdido ese dato en silencio. Quedó declarado como extra y va en el lock.

---

## Qué queda corriendo en el servidor

| Servicio | Qué hace | Equivalente en la Mac |
|---|---|---|
| `lokshn-robot.service` | El robot: escaneo, decisiones, órdenes reales | LaunchAgent `.scheduler` |
| `lokshn-dashboard.service` | Streamlit en el puerto 8501 | `Iniciar Dashboard.command` |
| `lokshn-healthcheck.timer` | Cada 5 min: detecta el robot colgado y lo reinicia | LaunchAgent `.healthcheck` |
| `lokshn-backup.timer` | 20:30: respaldo de la base | LaunchAgent `.backup` |

Son servicios **de usuario** (`systemctl --user`), no del sistema. Así el robot no corre como root
y el healthcheck puede reiniciarlo sin permisos de administrador. `loginctl enable-linger` es lo
que hace que arranquen al prender el servidor sin que nadie inicie sesión.

---

## Pasos

### 1. Crear el servidor
DigitalOcean → Create Droplet → **Debian 12**, Basic, **$12/mes** (1 vCPU / 2 GB), región **New
York**, autenticación por contraseña, hostname `lokshn`.

Los 2 GB no son capricho: el dashboard de Streamlit más el robot con pandas no entran cómodos en
1 GB, y quedarse sin memoria a mitad de rueda es una falla cara.

### 2. Preparar el servidor
```
ssh root@<IP>
bash 1_preparar_servidor.sh      # pegar el contenido del script
passwd lokshn                    # elegir una contraseña
tailscale up                     # abre un link para iniciar sesión
tailscale ip -4                  # anotar la IP 100.x.x.x
```
Deja el sistema actualizado, en horario de Nueva York, con el usuario `lokshn`, Tailscale,
firewall (`ufw`) y `fail2ban`.

El firewall cierra todo salvo SSH y **todo lo que venga por Tailscale**. El dashboard queda
alcanzable desde el celular y la Mac, y no desde internet — importa más que de costumbre, porque
desde el dashboard se aprueban órdenes con plata real.

### 3. Apagar el robot de la Mac
**Antes de copiar nada.** Dos robots operando a la vez mandarían órdenes dobles, y además copiar la
base mientras se escribe puede llevarse un archivo inconsistente.
```
launchctl bootout gui/$(id -u)/com.robertoajemblat.options-income-advisor.scheduler
```

### 4. Copiar el proyecto desde la Mac
```
rsync -av --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
      ~/options-income-advisor/ lokshn@<IP>:~/options-income-advisor/
```
El `rsync` lleva también lo que **no está en git** y sin lo cual el robot no arranca: `.env`,
`data/.schwab_tokens.json` y `data/app.db` con todo el historial.

### 5. Instalar
```
ssh lokshn@<IP>
cd ~/options-income-advisor && bash deploy/2_instalar_lokshn.sh
```
Verifica que llegaron los secretos y la base, arma el entorno con las versiones fijas, **corre la
suite completa** (si no pasa, no instala nada), verifica el reloj y levanta los cuatro servicios.

---

## Uso diario

```
systemctl --user status lokshn-robot          # ¿está vivo?
tail -f ~/options-income-advisor/data/logs/robot.log
systemctl --user restart lokshn-robot
systemctl --user stop lokshn-robot            # apagarlo
```

Dashboard: `http://<IP-de-tailscale>:8501`

**Reconectar Schwab (cada ~7 días, no hay forma de evitarlo):**
```
cd ~/options-income-advisor
./.venv/bin/python scripts/schwab_login.py     # imprime un link, se abre en cualquier navegador
systemctl --user restart lokshn-robot          # sin esto sigue usando el token viejo en memoria
```

---

## Volver atrás

La Mac queda intacta: el código, la base y los LaunchAgents siguen ahí. Para volver, apagar el
robot del servidor y volver a prender el de la Mac:
```
# en el servidor
systemctl --user stop lokshn-robot lokshn-dashboard

# en la Mac
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.robertoajemblat.options-income-advisor.scheduler.plist
```
Si el robot llegó a operar en el servidor, antes de volver hay que traerse la base
(`data/app.db`) para no perder esas operaciones — con el robot del servidor ya apagado.

**Nunca los dos a la vez.** El candado de proceso único (`single_instance.py`) protege contra dos
robots en la MISMA máquina; entre máquinas distintas no puede hacer nada, y los topes diarios se
leen de la base local de cada una.
