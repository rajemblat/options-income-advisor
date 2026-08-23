#!/bin/bash
# ============================================================================
#  PASO 2 - Instalar el robot en el servidor ya preparado.
#  Se corre como el usuario 'lokshn', DESPUÉS de haber copiado la carpeta
#  del proyecto desde la Mac:
#
#      cd ~/options-income-advisor && bash deploy/2_instalar_lokshn.sh
#
#  Instala en MODO PRUEBA por defecto: el robot escanea, evalúa y registra lo
#  que HARÍA, pero no manda órdenes ni avisos. Es el modo correcto mientras el
#  robot de la Mac sigue operando de verdad — así se puede comparar sin riesgo
#  de órdenes dobles ni de emails duplicados.
#
#  Para el día del cambio definitivo, cuando la Mac YA esté apagada:
#      bash deploy/2_instalar_lokshn.sh --real
#
#  Es seguro correrlo de nuevo: reinstala dependencias y servicios sin tocar
#  la base de datos ni los secretos.
# ============================================================================
set -euo pipefail

PROY="$HOME/options-income-advisor"
cd "$PROY"

MODO="prueba"
if [ "${1:-}" = "--real" ]; then
    MODO="real"
    echo
    echo "############################################################################"
    echo "  Vas a instalar el robot en MODO REAL: va a mandar órdenes con tu plata."
    echo
    echo "  Antes de seguir, el robot de la Mac TIENE que estar apagado. Dos robots"
    echo "  en modo real al mismo tiempo mandan cada uno su orden — el candado de"
    echo "  proceso único no puede verlos entre máquinas distintas."
    echo
    echo "  En la Mac:"
    echo "     launchctl bootout gui/\$(id -u)/com.robertoajemblat.options-income-advisor.scheduler"
    echo "############################################################################"
    echo
    read -r -p "  ¿El robot de la Mac ya está apagado? Escribí SI para seguir: " _ok
    [ "$_ok" = "SI" ] || { echo "Cancelado. No se instaló nada."; exit 1; }
fi

echo "== 1/7  Verificando que llegó todo lo que no está en git =="
faltan=0
for archivo in .env data/.schwab_tokens.json config/settings.yaml; do
    if [ -s "$archivo" ]; then
        echo "   ok       $archivo"
    else
        echo "   FALTA -> $archivo"
        faltan=1
    fi
done
if [ ! -s data/app.db ]; then
    echo "   FALTA -> data/app.db  (la base con todo el historial)"
    faltan=1
else
    echo "   ok       data/app.db ($(du -h data/app.db | cut -f1))"
fi
if [ "$faltan" = "1" ]; then
    echo
    echo "Falta algo. Esos archivos NO están en git a propósito (son secretos o datos)."
    echo "Copialos desde la Mac con el rsync del paso anterior y volvé a correr esto."
    exit 1
fi

echo "== 2/7  Entorno de Python =="
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
./.venv/bin/pip install --quiet --upgrade pip setuptools wheel
# Versiones FIJAS (deploy/requirements-lock.txt), no las últimas que haya ese día. El servidor
# tiene que correr exactamente lo mismo que la Mac donde esto ya funciona con plata real.
./.venv/bin/pip install --quiet -r deploy/requirements-lock.txt
./.venv/bin/pip install --quiet -e . --no-deps
echo "   $(./.venv/bin/python --version)"
echo "   pandas $(./.venv/bin/python -c 'import pandas;print(pandas.__version__)') · anthropic $(./.venv/bin/python -c 'import anthropic;print(anthropic.__version__)')"

echo "== 3/7  Corriendo la suite de tests =="
# Si los tests no pasan en ESTA máquina, no se instala nada. Es la misma barra que en la Mac.
./.venv/bin/pytest -q

echo "== 4/7  Verificando el reloj =="
./.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, "src")
from options_advisor.config import load_settings
from options_advisor.scheduler.zona_horaria import exigir_zona_horaria, ZonaHorariaIncorrecta
zona = load_settings().scheduler.timezone
try:
    exigir_zona_horaria(zona)
except ZonaHorariaIncorrecta as exc:
    print(exc); sys.exit(1)
from datetime import datetime
print(f"   ok  el servidor está en {zona}: son las {datetime.now():%H:%M del %d/%m}")
PY

echo "== 5/7  Poniendo el robot en modo $MODO =="
./.venv/bin/python deploy/modo.py "$MODO" | sed 's/^/   /'

echo "== 6/7  Instalando los servicios =="
mkdir -p "$HOME/.config/systemd/user"
cp deploy/systemd/lokshn-*.service deploy/systemd/lokshn-*.timer "$HOME/.config/systemd/user/"

if [ "$MODO" = "prueba" ]; then
    # Segundo freno, independiente del modo del robot: este proceso no le manda NADA a nadie.
    # Sin esto, el servidor mandaría sus propios emails en paralelo con los de la Mac y no se
    # sabría cuál vino de dónde. Se pone en el servicio y no en el .env para que se vea de un
    # vistazo con `systemctl --user cat lokshn-robot`, y para que desaparezca solo al pasar a real.
    for unidad in lokshn-robot lokshn-dashboard lokshn-healthcheck; do
        sed -i "/^\[Service\]/a Environment=LOKSHN_NO_NOTIFY=1" \
            "$HOME/.config/systemd/user/$unidad.service"
    done
    echo "   avisos apagados (LOKSHN_NO_NOTIFY=1): este robot no manda emails"
fi
systemctl --user daemon-reload
systemctl --user enable --now lokshn-robot.service
systemctl --user enable --now lokshn-dashboard.service
systemctl --user enable --now lokshn-healthcheck.timer
systemctl --user enable --now lokshn-backup.timer

echo "== 7/7  Estado =="
sleep 5
for s in lokshn-robot lokshn-dashboard; do
    printf "   %-20s %s\n" "$s" "$(systemctl --user is-active $s.service)"
done
for t in lokshn-healthcheck lokshn-backup; do
    printf "   %-20s %s\n" "$t.timer" "$(systemctl --user is-active $t.timer)"
done

IP_TS="$(tailscale ip -4 2>/dev/null | head -1 || true)"
if [ "$MODO" = "prueba" ]; then
cat <<FIN

============================================================================
  ROBOT INSTALADO EN MODO PRUEBA
============================================================================

  Este robot MIRA pero NO opera y NO manda avisos. El de la Mac sigue siendo
  el que trabaja de verdad. Podés dejarlo así los días que quieras.

  Qué mirar para decidir si confiás en él:
    - Que siga vivo mañana:     systemctl --user status lokshn-robot
    - Qué decidió, comparado
      con lo que hizo la Mac:   el dashboard, pestaña Real Market
    - Que no se quede ciego:    grep -i "sin conex" data/logs/robot.log
    - Que refresque el token:   grep -c refrescando data/logs/robot.log
      (tienen que ser decenas por dia, no decenas de miles)

  Cuando estés convencido, el cambio definitivo es:
    1. En la Mac, apagar el robot:
         launchctl bootout gui/\$(id -u)/com.robertoajemblat.options-income-advisor.scheduler
    2. Volver a copiar la base (la Mac siguio operando mientras tanto):
         rsync -av ~/options-income-advisor/data/app.db lokshn@<IP>:~/options-income-advisor/data/
    3. Aca, pasar a real:
         bash deploy/2_instalar_lokshn.sh --real

  Dashboard:   http://${IP_TS:-<tu-IP-de-tailscale>}:8501
               (solo desde tus dispositivos con Tailscale, no desde internet)

  Ver si está vivo:      systemctl --user status lokshn-robot
  Ver el log en vivo:    tail -f ~/options-income-advisor/data/logs/robot.log
  Reiniciar el robot:    systemctl --user restart lokshn-robot
  Apagar el robot:       systemctl --user stop lokshn-robot

  Reconectar Schwab (cada ~7 días):
      cd ~/options-income-advisor
      ./.venv/bin/python scripts/schwab_login.py
      systemctl --user restart lokshn-robot

============================================================================

FIN
else
cat <<FIN

============================================================================
  ROBOT INSTALADO EN MODO REAL — ESTE ES EL QUE OPERA
============================================================================

  Dashboard:   http://${IP_TS:-<tu-IP-de-tailscale>}:8501

  Ver si está vivo:      systemctl --user status lokshn-robot
  Ver el log en vivo:    tail -f ~/options-income-advisor/data/logs/robot.log
  Apagar el robot:       systemctl --user stop lokshn-robot

  Reconectar Schwab (cada ~7 días):
      cd ~/options-income-advisor
      ./.venv/bin/python scripts/schwab_login.py
      systemctl --user restart lokshn-robot

  El robot de la Mac tiene que quedar apagado. Si algún día querés volver a
  usarlo, primero apagá este y traete la base — está todo en deploy/MUDANZA.md.

============================================================================

FIN
fi
