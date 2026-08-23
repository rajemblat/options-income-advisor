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

# Darle al usuario la MISMA llave SSH que usa root para entrar.
#
# Sin esto no se puede entrar como 'lokshn' de ninguna forma: el usuario se crea sin contraseña, y
# los droplets creados con llave SSH vienen con PasswordAuthentication apagado en sshd — así que
# ponerle una contraseña con `passwd` tampoco alcanzaría. Copiar la llave es lo que hace que
# funcionen `ssh lokshn@...` y, sobre todo, el `rsync` que trae el proyecto desde la Mac.
if [ -s /root/.ssh/authorized_keys ]; then
    install -d -m 700 -o "$USUARIO" -g "$USUARIO" "/home/$USUARIO/.ssh"
    install -m 600 -o "$USUARIO" -g "$USUARIO" /root/.ssh/authorized_keys "/home/$USUARIO/.ssh/authorized_keys"
    echo "   llave SSH copiada: ya podés entrar con  ssh $USUARIO@<IP>"
else
    echo "   ATENCION: root no tiene authorized_keys, así que no se pudo habilitar el acceso"
    echo "   de '$USUARIO'. Sin eso el rsync desde la Mac no va a funcionar."
fi
# 'linger' = los servicios del usuario arrancan al prender el servidor, sin que nadie inicie
# sesión. Sin esto, el robot solo correría mientras haya una sesión SSH abierta.
loginctl enable-linger "$USUARIO"

echo "== 4b/7  Memoria de intercambio (swap) =="
# Los droplets vienen SIN swap. En una máquina de 2 GB eso significa que si el escaneo del universo
# pega un pico —pandas cargando cientos de símbolos, con Streamlit ya ocupando su parte— el kernel
# no tiene a dónde recurrir y mata el proceso más grande: el robot, en pleno horario de mercado y
# sin avisar.
#
# 2 GB de archivo de swap es el colchón. `swappiness=10` le dice al kernel que lo use solo cuando
# esté realmente apretado, así en operación normal no lo toca (el swap en disco es lento).
if swapon --show | grep -q .; then
    echo "   ya había swap configurado"
else
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "   2 GB de swap agregados (persisten al reiniciar)"
fi
sysctl -w vm.swappiness=10 >/dev/null
echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
free -h | sed 's/^/   /'

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

Falta una sola cosa acá: conectar Tailscale. Va a imprimir un link —
abrilo en el navegador de tu computadora e iniciá sesión. Después anotá
la IP que te muestre (empieza con 100.):

       tailscale up
       tailscale ip -4

El usuario '$USUARIO' ya quedó con tu misma llave SSH, así que no hace
falta ninguna contraseña: desde la Mac vas a entrar con

       ssh $USUARIO@<la IP publica>

Cuando termines, avisá y seguimos con el paso 2 (mudar el robot desde
la Mac).
============================================================================

FIN
