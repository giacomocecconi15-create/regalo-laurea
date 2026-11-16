#!/usr/bin/env bash
# Attiva (o disattiva con "off") il controllo automatico dei segnali su Mac: ogni 3 ore, anche ad app chiusa,
# scarica le partite in programma e invia su Telegram i segnali nuovi delle strategie salvate.
# Prima configura token e ID nella pagina Strategie dell'app.
set -e
cd "$(dirname "$0")"
PROJ="$(pwd)"
LABEL="it.sportstradinglab.avvisi"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [ "$(uname -s)" != "Darwin" ]; then echo "Questo script serve solo su Mac."; exit 1; fi
if [ "$1" = "off" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Avvisi automatici disattivati."
  exit 0
fi
if [ ! -x .venv/bin/python ]; then echo "Ambiente non trovato: esegui prima ./start_app.sh"; exit 1; fi
mkdir -p "$HOME/Library/LaunchAgents" data
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PROJ/.venv/bin/python</string><string>$PROJ/src/notify.py</string></array>
  <key>WorkingDirectory</key><string>$PROJ</string>
  <key>StartInterval</key><integer>10800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$PROJ/data/avvisi.log</string>
  <key>StandardErrorPath</key><string>$PROJ/data/avvisi.log</string>
</dict></plist>
PLIST
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Avvisi automatici attivati: controllo ogni 3 ore. Esito dell'ultimo controllo in data/avvisi.log"
echo "Per disattivarli: bash attiva_avvisi_mac.sh off"
