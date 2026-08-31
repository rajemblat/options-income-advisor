#!/bin/bash
set -e
cd ~/options-income-advisor
rm -f .git/index.lock
echo "══════ TESTS ══════"
.venv/bin/python -m pytest -q
echo "✅ Tests OK."
echo "══════ GUARDANDO ══════"
git add src/options_advisor/dashboard/components.py \
        tests/test_dashboard/test_components.py \
        scripts/guardar_vix.sh
git commit -F - <<'MSG'
Semaforo del VIX con los umbrales del usuario

Usuario 2026-08-31: "menos de 14 en verde, de 14.50 a 16 amarillo, y de 16.10
arriba en rojo". Antes usaba los genericos de manual (15 / 25). Los nuevos son
mas ajustados a proposito: para vender prima de condor no importa si el VIX es
alto en terminos historicos, sino si esta lo bastante quieto para que el rango
aguante el dia. Con 14.92 el chip pasa de verde a amarillo, que es la lectura
correcta para esta estrategia.

El usuario dejo dos huecos -- 14.00-14.50 y 16.00-16.10 -- y se cierran hacia el
color mas benigno para que ningun valor quede sin color. Hay un test que recorre
de 5 a 40 de a un centavo verificando justamente eso.

La banda del medio pasa a llamarse "Volatilidad media" en vez de "normal":
15 ya no es normal bajo estos umbrales.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AayqVJ6rqcpTMXv9V2aBYz
MSG
echo "══════ SUBIENDO ══════"
git push
echo "✅ LISTO."
git log --oneline -2
