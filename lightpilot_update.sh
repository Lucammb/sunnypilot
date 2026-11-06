#!/usr/bin/env bash
# lightpilot: at boot, move /data/openpilot to the commit named in the STABLE file of the fork, and nothing else.
# Offline, or already on that commit: does nothing. Never runs while driving (it is called from continue.sh, before launch).
REPO="${LP_REPO:-https://github.com/Lucammb/sunnypilot}"
STABLE_URL="${LP_STABLE_URL:-https://raw.githubusercontent.com/Lucammb/sunnypilot/lightpilot/STABLE}"
WORK="${LP_WORK:-/data/openpilot}"
LOG="${LP_LOG:-/data/lightpilot/update.log}"
mkdir -p "$(dirname "$LOG")"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

cd "$WORK" || exit 0
sha=""
for _ in 1 2 3; do
  sha=$(curl -fsS --max-time 10 "$STABLE_URL" 2>/dev/null | tr -d '[:space:]')
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] && break
  sha=""; sleep 5
done
[ -n "$sha" ] || { log "no STABLE reachable, staying put"; exit 0; }
current=$(git rev-parse HEAD 2>/dev/null)
[ "$current" = "$sha" ] && { log "already on ${sha:0:9}"; exit 0; }
timeout 120 git fetch --depth 1 "$REPO" "$sha" >> "$LOG" 2>&1 || { log "fetch failed, staying on ${current:0:9}"; exit 0; }
[ "$(git rev-parse FETCH_HEAD)" = "$sha" ] || { log "fetched commit is not $sha, staying put"; exit 0; }
echo "$current" > "$(dirname "$LOG")/previous_sha"
git -c advice.detachedHead=false checkout -f -B lightpilot "$sha" >> "$LOG" 2>&1 && log "updated ${current:0:9} -> ${sha:0:9}" || log "checkout failed"
exit 0
