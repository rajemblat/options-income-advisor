#!/bin/bash
# Instala el respaldo diario de la base como LaunchAgent de macOS.
# Correlo UNA vez:  bash ~/options-income-advisor/scripts/instalar_backup_automatico.sh
set -euo pipefail
LABEL="com.robertoajemblat.options-income-advisor.backup"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROY="$HOME/options-income-advisor"

mkdir -p "$HOME/Library/LaunchAgents" "$PROY/data/logs"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$PROY/scripts/backup_db.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>30</integer></dict>
    <key>RunAtLoad</key><false/>
    <key>StandardOutPath</key><string>$PROY/data/logs/backup.log</string>
    <key>StandardErrorPath</key><string>$PROY/data/logs/backup.err.log</string>
    <key>WorkingDirectory</key><string>$PROY</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Listo. El respaldo corre todos los dias a las 20:30 (mercado cerrado)."
echo "Guarda los ultimos 30 dias en $PROY/data/backups/"
echo
echo "Probalo ahora sin esperar:"
echo "    launchctl kickstart gui/\$(id -u)/$LABEL && sleep 20 && cat $PROY/data/logs/backup.log"
