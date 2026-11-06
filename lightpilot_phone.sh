#!/bin/bash
# Link this device to the phone app server. Run once over SSH:
#   bash /data/openpilot/lightpilot_phone.sh https://lucabargigia.it/nav/api.php DEVICE_TOKEN
set -e
[ -n "$1" ] && [ -n "$2" ] || { echo "usage: $0 <api url> <device token>"; exit 1; }
mkdir -p /data/lightpilot
printf '{"url": "%s", "token": "%s"}\n' "$1" "$2" > /data/lightpilot/phone.json
chmod 600 /data/lightpilot/phone.json
echo "saved. test the server:"
curl -s -m 10 -X POST -H "X-Token: $2" -H 'Content-Type: application/json' -d '{}' "$1?a=sync" && echo
echo "the phone_link process picks it up by itself (after the next update/reboot it is started by the manager)."
