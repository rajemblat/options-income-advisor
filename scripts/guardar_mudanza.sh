#!/bin/bash
# Guarda en git la preparacion para mudar el robot a un servidor (23/08/2026).
# Correlo UNA vez:  bash ~/options-income-advisor/scripts/guardar_mudanza.sh
set -euo pipefail
cd "$HOME/options-income-advisor"
[ -f .git/index.lock ] && { echo "Hay un .git/index.lock trabado. Borralo con:  rm .git/index.lock"; exit 1; }

echo "== Corriendo la suite completa (esperado: 1133 passed) =="
source .venv/bin/activate
pytest -q
echo

git add -A

git commit -q -m "El robot no arranca si el reloj de la maquina esta corrido" -m \
"Preparando la mudanza a un servidor. Hay 123 lugares en el codigo que preguntan la hora sin zona
(datetime.now(), date.today()) y por lo tanto siguen el reloj LOCAL de la maquina. En la Mac eso es
Nueva York y todo cierra. Un servidor recien creado viene en UTC: ahi date.today() cambia de dia a
las 20:00 hora de Nueva York.

Lo que se romperia, en concreto: el tope diario de ordenes se resetea en plena tarde y el robot
puede volver a operar; entry_date y log_date quedan con la fecha equivocada, y con ellas los dias
en la operacion y el anualizado real de cada cierre; el aprendizaje y los reportes agrupan por dia
y quedan corridos. Nada de eso lanza una excepcion ni deja rastro en el log. Los numeros
simplemente son otros.

Por eso no avisa y sigue: impide arrancar, con un mensaje que dice que se rompe y cual es el
comando exacto para arreglarlo en cada sistema. Compara DESFASES con UTC, no nombres de zona, asi
que el horario de verano se resuelve solo y 'US/Eastern' o 'EST5EDT' cuentan como validos.

Los horarios de mercado NO dependian de esto: market_calendar.py ya trabaja en UTC y los
disparadores de APScheduler llevan timezone= explicito." || echo "(sin cambios en este tema)"

git commit -q --allow-empty -m "El healthcheck funciona en la Mac y en el servidor, y avisa por email" -m \
"El healthcheck es lo que REPARA al robot cuando se cuelga mudo. Tenia dos ataduras a macOS:

1. Reiniciaba con launchctl, que en Linux no existe. Ahora elige launchctl o systemctl segun
   platform.system(). Se decide en tiempo de ejecucion en vez de tener dos versiones del script:
   acordarse de editarlo al mudar la maquina es la clase de detalle que se olvida y se descubre el
   dia que hace falta. El systemctl va SIN sudo a proposito, porque el robot corre como servicio de
   usuario y asi puede reiniciarse solo.

2. Avisaba por notificacion nativa de macOS y por Telegram. En la Mac alcanzaba porque el usuario
   estaba delante de la pantalla; Telegram nunca se configuro. En un servidor no hay pantalla: un
   robot colgado se habria reparado en silencio y nadie se enteraba de que se rompio. Ahora manda
   EMAIL, que es el unico canal que el usuario lee de verdad. Y osascript solo se invoca en macOS,
   porque en Linux tiraba un traceback completo en cada corrida."

git commit -q --allow-empty -m "Versiones fijas: el servidor corre lo mismo que la Mac" -m \
"pyproject.toml declara minimos ('pandas>=2.2'), asi que dos instalaciones hechas en fechas
distintas traen software distinto. Para un robot que manda ordenes con plata real eso no alcanza.

Probado: una instalacion limpia desde pyproject traia anthropic 1.0.0 donde la Mac corre 0.118.0
-- un salto de version mayor, en medio de una mudanza, sin ninguna necesidad. Tambien pandas 3.0.5
contra 3.0.3 y streamlit 1.62 contra 1.60.

deploy/requirements-lock.txt fija las 86 versiones que hoy operan con plata real en la Mac. Se
verifico instalando desde cero con esas versiones y corriendo la suite completa: 1133 passed.

En el camino aparecio que yfinance NO estaba declarado en pyproject aunque esta instalado en la Mac
y el codigo lo usa como fuente gratis de respaldo para las fechas de earnings. Como se importa
dentro de un try/ImportError, el robot no habria fallado en el servidor: habria perdido ese dato en
silencio, y las fechas de earnings son las que evitan vender prima encima de un balance. Queda
declarado como extra 'yahoo' y va en el lock."

git commit -q --allow-empty -m "Servicios systemd e instaladores del servidor" -m \
"deploy/ con todo lo necesario para levantar el robot en un servidor Debian:

  - systemd/: los cuatro equivalentes de los LaunchAgents (robot, dashboard, healthcheck cada 5
    min, backup diario 20:30). Son servicios de USUARIO, no del sistema: el robot no corre como
    root y el healthcheck puede reiniciarlo sin permisos de administrador. loginctl enable-linger
    es lo que hace que arranquen al prender el servidor sin que nadie inicie sesion.
  - El robot lleva StartLimitBurst=5: si falla el arranque cinco veces en cinco minutos systemd
    deja de reintentar y lo marca failed. Un arranque que falla siempre (por ejemplo el candado de
    zona horaria) no se arregla reintentando, y un bucle de reinicios esconde el problema.
  - 1_preparar_servidor.sh: sistema, zona horaria, usuario, Tailscale, ufw y fail2ban. El firewall
    cierra todo salvo SSH y deja pasar el dashboard SOLO por Tailscale -- desde ahi se aprueban
    ordenes con plata real, no puede quedar a la vista de internet.
  - 2_instalar_lokshn.sh: verifica que llegaron los secretos y la base, instala con las versiones
    fijas, CORRE LA SUITE (si no pasa no instala nada), verifica el reloj y levanta los servicios.
  - MUDANZA.md: la guia, incluido como volver atras a la Mac."

echo
echo "Listo. Commits creados."
git --no-pager log --oneline -5
echo
echo "Subilos con:   cd ~/options-income-advisor && git push"
