#!/bin/bash
# Doble clic para prender el robot (scheduler). Deja esta ventana abierta y la Mac encendida.
cd "$HOME/options-income-advisor" || { echo "No encuentro la carpeta"; read; exit 1; }
source .venv/bin/activate
echo "==================================================="
echo "  Robot en marcha (evalua el mercado en horario de bolsa)."
echo "  DEJA ESTA VENTANA ABIERTA y la Mac encendida."
echo "==================================================="
caffeinate -s python scripts/run_scheduler.py
