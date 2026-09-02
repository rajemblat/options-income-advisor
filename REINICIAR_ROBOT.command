#!/bin/bash
# Doble clic para reiniciar el robot y que cargue el codigo nuevo.
# Creado el 2026-09-02 despues de arreglar los cuatro problemas del Iron Condor real.
# El scheduler tiene el codigo cargado en memoria desde que arranco: cambiar los archivos
# no alcanza, hay que reiniciar el proceso.

cd "$(dirname "$0")" || exit 1
ETIQUETA="com.robertoajemblat.options-income-advisor.scheduler"

echo "════════════════════════════════════════════════════════"
echo "  Reiniciando el robot (Lokshn)"
echo "════════════════════════════════════════════════════════"
echo

echo "→ Reiniciando el scheduler..."
if launchctl kickstart -k "gui/$(id -u)/$ETIQUETA"; then
  echo "  OK"
else
  echo "  ⚠️  No se pudo reiniciar. Probá cargarlo de nuevo con:"
  echo "     launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/$ETIQUETA.plist"
  echo
  read -n 1 -s -r -p "Apretá cualquier tecla para cerrar..."
  exit 1
fi

echo
echo "→ Esperando 12 segundos a que levante..."
sleep 12

echo
echo "→ Estado:"
launchctl print "gui/$(id -u)/$ETIQUETA" 2>/dev/null | grep -E "state|pid" | head -4

echo
echo "→ Ultimas lineas del log:"
tail -6 data/logs/robot.log 2>/dev/null || echo "  (todavia no escribio nada)"

echo
echo "════════════════════════════════════════════════════════"
echo "  Listo. El robot ya corre con el codigo nuevo:"
echo "   · precios de SPX en multiplos de 5 centavos"
echo "   · no abre por menos de \$150 de prima"
echo "   · no deja una orden colgada mas de 5 minutos"
echo "   · el stop mide al precio real de salida, no al mid"
echo "════════════════════════════════════════════════════════"
echo
read -n 1 -s -r -p "Apretá cualquier tecla para cerrar..."
