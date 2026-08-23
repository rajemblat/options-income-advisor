#!/bin/bash
# ============================================================================
#  PASO 2 - Instalar el robot en el servidor ya preparado.
#  Se corre como el usuario 'lokshn', DESPUÉS de haber copiado la carpeta
#  del proyecto desde la Mac:
#
#      cd ~/options-income-advisor && bash deploy/2_instalar_lokshn.sh
#
#  Es seguro correrlo de nuevo: reinstala dependencias y servicios sin tocar
#  la base de datos ni los secretos.
# ============================================================================
set -euo pipefail

PROY="$HOME/options-income-advisor"
cd "$PROY"

echo "== 1/6  Verificando que llegó todo lo que no está en git =="
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

echo "== 2/6  Entorno de Python =="
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

echo "== 3/6  Corriendo la suite de tests =="
# Si los tests no pasan en ESTA máquina, no se instala nada. Es la misma barra que en la Mac.
./.venv/bin/pytest -q

echo "== 4/6  Verificando el reloj =="
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

echo "== 5/6  Instalando los servicios =="
mkdir -p "$HOME/.config/systemd/user"
cp deploy/systemd/lokshn-*.service deploy/systemd/lokshn-*.timer "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now lokshn-robot.service
systemctl --user enable --now lokshn-dashboard.service
systemctl --user enable --now lokshn-healthcheck.timer
systemctl --user enable --now lokshn-backup.timer

echo "== 6/6  Estado =="
sleep 5
for s in lokshn-robot lokshn-dashboard; do
    printf "   %-20s %s\n" "$s" "$(systemctl --user is-active $s.service)"
done
for t in lokshn-healthcheck lokshn-backup; do
    printf "   %-20s %s\n" "$t.timer" "$(systemctl --user is-active $t.timer)"
done

IP_TS="$(tailscale ip -4 2>/dev/null | head -1 || true)"
cat <<FIN

============================================================================
  ROBOT INSTALADO
============================================================================

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
