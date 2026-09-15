#!/usr/bin/env bash
# Revisión automática de las 9:00 ET, de lunes a viernes — 30 minutos antes de que abra el mercado.
#
# Pedido del usuario el 2026-09-15: "me gustaría que todas las mañanas 30 min antes que abra el
# mercado hagas esta revisión".
#
# Por qué corre ACÁ, en el servidor, y no desde afuera: el mail del servidor está bloqueado por
# DigitalOcean, y nada de fuera de la red alcanza a esta máquina. El único que siempre puede ver al
# servidor es el servidor. Así que la revisión corre localmente, deja el resultado en
# data/logs/revision_matinal.json, y el dashboard lo muestra arriba de Real Market — donde el
# usuario ya mira todos los días antes de apretar los botones.
#
# Uso (una sola vez, en el servidor):
#     bash deploy/instalar_revision_matinal.sh
#
# Para ver cuándo corre:      systemctl --user list-timers lokshn-revision
# Para correrla a mano:       systemctl --user start lokshn-revision.service
# Para desinstalarla:         systemctl --user disable --now lokshn-revision.timer
set -euo pipefail

PROYECTO="$HOME/options-income-advisor"
UNIDADES="$HOME/.config/systemd/user"
mkdir -p "$UNIDADES"

# El reloj del servidor está en hora de Nueva York (lo fija el candado de zona horaria del robot),
# así que "09:00" acá ya es hora de mercado. Si algún día la máquina cambiara de zona, esto se
# correría con ella — por eso el servicio vuelve a imprimir la hora en su salida.
cat > "$UNIDADES/lokshn-revision.service" <<EOF
[Unit]
Description=Revision previa a la apertura del mercado (Lokshn)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$PROYECTO
Environment=PYTHONPATH=$PROYECTO/src
ExecStart=$PROYECTO/.venv/bin/python $PROYECTO/scripts/listo_para_operar.py --guardar
EOF

cat > "$UNIDADES/lokshn-revision.timer" <<'EOF'
[Unit]
Description=Revision de las 9:00 ET, dias de mercado (Lokshn)

[Timer]
OnCalendar=Mon-Fri 09:00
# Si el servidor estuvo apagado a esa hora, corre igual al encender: mas vale una revision tarde
# que ninguna el dia que algo estaba roto.
Persistent=true
Unit=lokshn-revision.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now lokshn-revision.timer

echo
echo "Listo. La revision va a correr de lunes a viernes a las 9:00 (hora del servidor)."
date
echo
systemctl --user list-timers lokshn-revision --no-pager || true
echo
echo "Corriendola una vez ahora para dejar el resultado cargado..."
systemctl --user start lokshn-revision.service
sleep 3
echo
if [ -f "$PROYECTO/data/logs/revision_matinal.json" ]; then
    echo "Resultado guardado:"
    head -c 400 "$PROYECTO/data/logs/revision_matinal.json"
    echo
else
    echo "OJO: no se genero el archivo de resultado. Mira:"
    echo "    journalctl --user -u lokshn-revision.service -n 30 --no-pager"
fi
