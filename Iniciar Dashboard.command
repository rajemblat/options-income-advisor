#!/bin/bash
# Doble clic para abrir el dashboard de OptionsUp. Dejá esta ventana abierta mientras lo usás.
cd "$HOME/options-income-advisor" || { echo "No encuentro la carpeta"; read; exit 1; }
pkill -f "streamlit run" 2>/dev/null
source .venv/bin/activate
echo "==================================================="
echo "  Abriendo el dashboard..."
echo "  Cuando diga 'You can now view', abrí el navegador."
echo "  DEJA ESTA VENTANA ABIERTA mientras lo uses."
echo "==================================================="
streamlit run src/options_advisor/dashboard/app.py
