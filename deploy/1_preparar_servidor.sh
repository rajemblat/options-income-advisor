#!/bin/bash
# ============================================================================
#  PASO 1 - Preparar un servidor Debian 12 recién creado para correr Lokshn.
#  Se corre UNA vez, como root, en el servidor:   bash 1_preparar_servidor.sh
#
#  Deja el sistema listo pero NO instala todavía el robot (eso es el paso 2).
#  Es seguro correrlo dos veces: todo lo que hace es idempotente.
# ============================================================================
set -euo pipefail

USUARIO="lokshn"
ZONA="America/New_York"

echo "== 1/7  Actualizando el sistema =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

echo "== 2/7  Zona horaria -> $ZONA =="
# NO es cosmético. El robot tiene 123 llamadas que preguntan la hora sin zona y siguen el reloj
# local. Con el servidor en UTC, 'hoy' cambia a las 20:00 de Nueva York: se resetea el tope diario
# de órdenes a mitad de tarde y las fechas de las operaciones quedan corridas. El robot ahora se
# niega a arrancar si esto no está bien puesto.
apt-get install -y -qq tzdata
timedatectl set-timezone "$ZONA"
echo "   ahora son las $(date '+%H:%M del %d/%m') ($(timedatectl show -p Timezone --value))"

echo "== 3/7  Paquetes necesarios =="
apt-get install -y -qq \
    python3 python3-venv python3-dev python3-pip \
    git rsync curl ca-certificates \
    build-essential \
    sqlite3 \
    ufw fail2ban

echo "   python: $(python3 --version)"

echo "== 4/7  Usuario '$USUARIO' (el robot NO corre como root) =="
if id "$USUARIO" >/dev/null 2>&1; then
    echo "   ya existía"
else
    adduser --disabled-password --gecos "" "$USUARIO"
fi
# 'linger' = los servicios del usuario arrancan al prender el servidor, sin que nadie inicie
# sesión. Sin esto, el robot solo correría mientras haya una sesión SSH abierta.
loginctl enable-linger "$USUARIO"

echo "== 5/7  Tailscale (red privada para llegar al dashboard) =="
if command -v tailscale >/dev/null 2>&1; then
    echo "   ya estaba instalado"
else
    curl -fsSL https://tailscale.com/install.sh | sh
fi

echo "== 6/7  Firewall =="
# Todo cerrado salvo SSH. El dashboard (8501) SOLO se puede alcanzar por Tailscale: la regla es
# por interfaz, así que no hay forma de llegar desde internet aunque se sepa la IP y el puerto.
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp comment 'SSH' >/dev/null
ufw allow in on tailscale0 comment 'Todo el trafico de Tailscale (dashboard)' >/dev/null
ufw --force enable >/dev/null
ufw status verbose | sed 's/^/   /'

echo "== 7/7  fail2ban (bloquea a los que prueban contraseñas por SSH) =="
systemctl enable --now fail2ban >/dev/null 2>&1 || true
systemctl is-active fail2ban | sed 's/^/   fail2ban: /'

cat <<FIN

============================================================================
  SERVIDOR PREPARADO
============================================================================

Ahora, en ESTE mismo servidor, hacé dos cosas:

  1) Poner contraseña al usuario del robot (te la va a pedir dos veces):

       passwd $USUARIO

  2) Conectar Tailscale. Va a imprimir un link: abrilo en el navegador de tu
     computadora e iniciá sesión. Después anotá la IP que te muestre (empieza
     con 100.):

       tailscale up
       tailscale ip -4

Cuando termines esos dos pasos, avisá y seguimos con el paso 2 (mudar el
robot desde la Mac).
============================================================================

FIN
