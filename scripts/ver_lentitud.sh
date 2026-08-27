#!/bin/bash
# Diagnostico de lentitud de la Mac. Solo LEE, no cambia nada.
echo "======== DISCO ========"
df -h / | awk 'NR==1||NR==2'
echo
echo "======== MEMORIA / CARGA ========"
top -l 1 -n 0 | grep -E "PhysMem|Load Avg|CPU usage"
echo
echo "======== LOS 8 QUE MAS CPU USAN ========"
ps aux | sort -nrk 3 | head -8 | awk '{printf "CPU %6s%%  RAM %5s%%  %s %s\n",$3,$4,$11,$12}'
echo
echo "======== LOS 8 QUE MAS MEMORIA USAN ========"
ps aux | sort -nrk 4 | head -8 | awk '{printf "RAM %6s%%  CPU %5s%%  %s %s\n",$4,$3,$11,$12}'
echo
echo "======== PROCESOS DEL ROBOT (lo nuestro) ========"
ps aux | grep -E "run_scheduler|streamlit|healthcheck_sched" | grep -v grep \
  | awk '{printf "CPU %6s%%  RAM %5s%%  %s %s %s\n",$3,$4,$11,$12,$13}'
echo "(si run_scheduler aparece mas de una vez, hay robots duplicados)"
echo
echo "======== CUANTOS PROCESOS PYTHON HAY ========"
ps aux | grep -c "[p]ython"
echo
echo "======== PRESION DE MEMORIA (swap) ========"
sysctl vm.swapusage 2>/dev/null
echo
echo "======== FIN ========"
