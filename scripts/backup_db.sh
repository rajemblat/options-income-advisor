#!/bin/bash
# Respaldo diario de la base del robot (usuario 2026-08-22: "protege el codigo y guardalo para que no
# se pierda si se rompe la Mac").
#
# Por que existe: data/app.db esta EXCLUIDA de git A PROPOSITO (git es para codigo, no para datos que
# cambian a cada minuto), asi que GitHub no la protege. Ahi viven ~46.000 decisiones del robot, 153
# ordenes reales y todo el historial de P&L. Y esa base YA se corrompio dos veces: quedan las carpetas
# data/_corrupt/ y data/_corrupt2/ y un app_reparada.db de esos incidentes.
#
# Usa la API de backup de SQLite y NO un `cp`: con la base en modo WAL, copiar el archivo puede agarrar
# un estado a medio escribir mientras el robot opera. La API toma una foto consistente aunque haya
# escrituras en curso, asi que es seguro correrlo con el robot prendido.
set -euo pipefail
PROY="$HOME/options-income-advisor"
cd "$PROY"
[ -x .venv/bin/python ] && PY=.venv/bin/python || PY=python3
"$PY" scripts/backup_db.py
