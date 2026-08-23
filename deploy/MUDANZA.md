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

## La mudanza es en dos etapas, no en una

Pedido explícito del usuario (23/08): *"no quiero que se borre de la Mac hasta comprobar que
funciona bien en otro lado"*. Es la decisión correcta, y se puede hacer — con una condición que no
se negocia:

> **Nunca puede haber dos robots en MODO REAL al mismo tiempo.** El candado de proceso único
> (`single_instance.py`) protege contra dos robots en la MISMA máquina, con un `flock` sobre un
> archivo local. Entre máquinas distintas no puede ver nada, y cada una lleva sus topes diarios en
> su propia base. Dos robots reales mandan cada uno su orden.

Por eso el servidor se instala en **modo prueba** y se queda ahí todos los días que haga falta. En
ese modo (`dry_run: true` + `kill_switch: true`) el robot escanea, evalúa y registra lo que HARÍA,
pero el camino real está cortado en cuatro lugares distintos del código: no manda órdenes, no
cierra posiciones, no re-precia órdenes en espera y no manda emails de apertura ni de cierre.

Además el servicio lleva `LOKSHN_NO_NOTIFY=1`, que es un freno **independiente del modo**: ese
proceso no manda ni un email ni un mensaje de Telegram, pase lo que pase. Sin eso, cada aviso
llegaría dos veces —uno de la Mac y otro del servidor— y no se sabría cuál vino de dónde. Es
justamente la confusión que ya costó un susto el 23/08 con los emails de los tests.

Lo único que sí comparten las dos máquinas es la cuenta de Schwab, para leer precios. No es un
problema: el `refresh_token` de Schwab no rota (devuelve siempre el mismo con su vencimiento
original), así que los dos pueden refrescar sin pisarse. Sí duplica las llamadas a la API — si
aparecieran errores 429 conviene acortar la validación, aunque el cliente ya los reintenta con
backoff.

---

## Etapa 1 — Levantar el servidor mirando

### 1.1 Crear el servidor
DigitalOcean → Create Droplet → **Debian 12**, Basic, **$12/mes** (1 vCPU / 2 GB), región **New
York**, autenticación por contraseña, hostname `lokshn`.

Los 2 GB no son capricho: el dashboard de Streamlit más el robot con pandas no entran cómodos en
1 GB, y quedarse sin memoria a mitad de rueda es una falla cara.

### 1.2 Preparar el servidor
Desde la Mac, copiar el preparador (un comando por vez, y la IP va sin `< >`):
```
scp ~/options-income-advisor/deploy/1_preparar_servidor.sh root@LA_IP:/root/
ssh root@LA_IP
```
Y ya adentro del servidor:
```
bash 1_preparar_servidor.sh
tailscale up                     # abre un link para iniciar sesión
tailscale ip -4                  # anotar la IP 100.x.x.x
```

El script le copia al usuario `lokshn` la misma llave SSH que usa `root`. Sin ese paso no habría
forma de entrar como `lokshn`: se crea sin contraseña, y un droplet creado con llave SSH viene con
`PasswordAuthentication` apagado, así que ponerle una contraseña tampoco serviría. Es lo que hace
que funcione el `rsync` desde la Mac.
Deja el sistema actualizado, en horario de Nueva York, con el usuario `lokshn`, Tailscale,
firewall (`ufw`) y `fail2ban`.

El firewall cierra todo salvo SSH y **todo lo que venga por Tailscale**. El dashboard queda
alcanzable desde el celular y la Mac, y no desde internet — importa más que de costumbre, porque
desde el dashboard se aprueban órdenes con plata real.

### 1.3 Copiar el proyecto (la Mac sigue trabajando)
```
rsync -av --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
      ~/options-income-advisor/ lokshn@LA_IP:~/options-income-advisor/
```
El `rsync` lleva también lo que **no está en git** y sin lo cual el robot no arranca: `.env`,
`data/.schwab_tokens.json` y `data/app.db`.

La base se copia con el robot de la Mac corriendo, así que puede quedar un poco desactualizada o
incluso a medio escribir. Para la etapa de prueba no importa: es una foto para arrancar, y esa
copia se descarta en la etapa 2. Lo que **no** hay que hacer es copiarla al revés más adelante.

### 1.4 Instalar en modo prueba
```
ssh lokshn@LA_IP
cd ~/options-income-advisor && bash deploy/2_instalar_lokshn.sh
```
Verifica que llegaron los secretos y la base, instala con las versiones fijas, **corre la suite
completa** (si no pasa, no instala nada), verifica el reloj, pone el modo prueba y levanta los
servicios.

### Nota: el swap no es opcional

Los droplets vienen con `Swap: 0B`. En una máquina de 2 GB eso significa que un pico de memoria
—el escaneo del universo con pandas, más Streamlit— hace que el kernel mate el proceso más grande
en vez de paginar. O sea el robot, en pleno horario de mercado, sin avisar. Medido con el robot y
el dashboard corriendo y el mercado cerrado: 720 MB usados, 1.2 GB disponibles. Hay aire, pero no
tanto como para apostar a que ningún pico se pase.

`1_preparar_servidor.sh` agrega 2 GB de archivo de swap con `swappiness=10`, así el kernel lo usa
solo cuando está realmente apretado y en operación normal no lo toca.

### 1.5 Mirarlo unos días
Qué observar antes de confiarle plata:

- **Que siga vivo mañana** — `systemctl --user status lokshn-robot`
- **Que decida parecido a la Mac** — el dashboard, pestaña Real Market, contra lo que hizo la Mac
- **Que no se quede ciego** — `grep -i "sin conex" data/logs/robot.log`
- **Que el token se refresque bien** — `grep -c refrescando data/logs/robot.log` tiene que dar
  decenas por día, no decenas de miles (eso último era el síntoma del apagón de red del 21/08)
- **Que sobreviva a un reinicio** — `sudo reboot`, y que los cuatro servicios vuelvan solos

---

## Etapa 2 — El cambio definitivo

En este orden, sin saltear:

**1. Apagar el robot de la Mac.**
```
launchctl bootout gui/$(id -u)/com.robertoajemblat.options-income-advisor.scheduler
```

**2. Copiar la base de nuevo.** La Mac siguió operando durante toda la validación, así que su base
tiene operaciones que la copia del servidor no. Ahora sí se copia con el robot ya apagado, o sea
consistente:
```
rsync -av ~/options-income-advisor/data/app.db lokshn@LA_IP:~/options-income-advisor/data/
```

**3. Pasar el servidor a real.**
```
ssh lokshn@LA_IP
cd ~/options-income-advisor && bash deploy/2_instalar_lokshn.sh --real
```
Pide confirmación escrita de que la Mac está apagada antes de hacer nada.

## Uso diario

```
systemctl --user status lokshn-robot          # ¿está vivo?
tail -f ~/options-income-advisor/data/logs/robot.log
systemctl --user restart lokshn-robot
systemctl --user stop lokshn-robot            # apagarlo
```

Dashboard: `http://<IP-de-tailscale>:8501`

**Ver o cambiar el modo de una máquina:**
```
./.venv/bin/python deploy/modo.py estado    # ¿esta máquina opera o solo mira?
./.venv/bin/python deploy/modo.py prueba    # ponerla a mirar
./.venv/bin/python deploy/modo.py real      # ponerla a operar
systemctl --user restart lokshn-robot       # el cambio toma efecto al reiniciar
```
El robot también lo dice en su primera línea al arrancar, en la consola y en el log.

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
