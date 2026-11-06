#!/usr/bin/env bash
# lightpilot: install the boot-time updater (once). Safe to run again.
set -e
mkdir -p /data/lightpilot
curl -fsS https://raw.githubusercontent.com/Lucammb/sunnypilot/lightpilot/lightpilot_update.sh -o /data/lightpilot/update.sh
chmod +x /data/lightpilot/update.sh
if [ ! -f /data/continue.sh ]; then echo "ERRORE: /data/continue.sh non esiste, non tocco nulla"; exit 1; fi
if grep -q lightpilot/update.sh /data/continue.sh; then
  echo "continue.sh gia' configurato"
else
  cp /data/continue.sh /data/lightpilot/continue.sh.bak
  { head -n 1 /data/continue.sh; echo '/data/lightpilot/update.sh  # lightpilot'; tail -n +2 /data/continue.sh; } > /data/continue.sh.new
  cat /data/continue.sh.new > /data/continue.sh && rm /data/continue.sh.new
  echo "continue.sh aggiornato (copia in /data/lightpilot/continue.sh.bak)"
fi
/data/lightpilot/update.sh
tail -n 3 /data/lightpilot/update.log
