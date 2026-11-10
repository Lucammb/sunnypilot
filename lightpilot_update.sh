#!/usr/bin/env bash
# lightpilot updater. Two parts, because the Konik is only online while the car is on:
#   boot (called from continue.sh, before launch): apply an update that was already downloaded (no network needed),
#       try once, quickly, to find a new one, then start the background downloader and return at once;
#   --loop (background, one instance): whenever there is network, download the commit named in STABLE, without applying it.
# Applying only ever happens at boot, never while driving. Same commit as now, or no news: nothing happens.
REPO="${LP_REPO:-https://github.com/Lucammb/sunnypilot}"
STABLE_URL="${LP_STABLE_URL:-https://raw.githubusercontent.com/Lucammb/sunnypilot/lightpilot/STABLE}"
WORK="${LP_WORK:-/data/openpilot}"
LOG="${LP_LOG:-/data/lightpilot/update.log}"
DIR="$(dirname "$LOG")"
PENDING="$DIR/pending_sha"
FAST="${LP_FAST:-30}"    # seconds between tries while offline
SLOW="${LP_SLOW:-1800}"  # seconds between checks once online
mkdir -p "$DIR"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
cd "$WORK" || exit 0
HOLD="$DIR/hold"  # while this file exists the code is left alone (to try a version by hand): touch /data/lightpilot/hold

read_stable() {  # prints the sha of STABLE, fails if offline or garbage
  local sha
  sha=$(curl -fsS --max-time "${1:-10}" "$STABLE_URL" 2>/dev/null | tr -d '[:space:]')
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] && echo "$sha"
}
have() { git cat-file -e "$1^{commit}" 2>/dev/null; }

download() {  # $1 = sha; fetch it (low priority, no gc) and remember it as pending. Return 0 when it is available locally.
  local sha="$1" low=""
  have "$sha" && { echo "$sha" > "$PENDING"; return 0; }
  command -v nice >/dev/null && low="nice -n 19"
  command -v ionice >/dev/null && low="$low ionice -c3"
  timeout "${LP_FETCH_TIMEOUT:-900}" $low git -c gc.auto=0 fetch --depth 1 "$REPO" "$sha" >> "$LOG" 2>&1 || { log "download failed"; return 1; }
  [ "$(git rev-parse FETCH_HEAD)" = "$sha" ] || { log "fetched commit is not ${sha:0:9}, ignoring"; return 1; }
  echo "$sha" > "$PENDING"; log "downloaded ${sha:0:9}, applied at next boot"
}

apply() {  # $1 = sha, present locally
  local sha="$1" current
  current=$(git rev-parse HEAD 2>/dev/null)
  rm -f "$PENDING"
  [ "$current" = "$sha" ] && { log "already on ${sha:0:9}"; return 0; }
  echo "$current" > "$DIR/previous_sha"
  git -c advice.detachedHead=false checkout -f -B lightpilot "$sha" >> "$LOG" 2>&1 && log "updated ${current:0:9} -> ${sha:0:9}" || log "checkout failed, staying on ${current:0:9}"
}

check_once() {  # online check; 0 = reached the server
  local sha current
  [ -e "$HOLD" ] && return 0
  sha=$(read_stable "${1:-10}") || return 1
  current=$(git rev-parse HEAD 2>/dev/null)
  if [ "$sha" = "$current" ]; then rm -f "$PENDING"; return 0; fi
  download "$sha"; return 0
}

if [ "$1" = "--loop" ]; then
  exec 9>"$DIR/loop.lock"
  command -v flock >/dev/null && { flock -n 9 || exit 0; }
  while true; do
    if check_once 10; then sleep "$SLOW"; else sleep "$FAST"; fi
  done
fi

# boot
[ -e "$HOLD" ] && { log "hold file present, not updating"; exit 0; }
pending=$(cat "$PENDING" 2>/dev/null | tr -d '[:space:]')
if [[ "$pending" =~ ^[0-9a-f]{40}$ ]] && have "$pending"; then
  apply "$pending"
else
  rm -f "$PENDING"
  sha=$(read_stable 4) && { download "$sha" && apply "$sha"; } || log "offline at boot, will download while driving"
fi
# keep this script itself current: the copy in the repo replaces the one running from /data/lightpilot (used from the next boot)
SELF="${LP_SELF:-/data/lightpilot/update.sh}"
[ "$0" = "$SELF" ] && [ -f "$WORK/lightpilot_update.sh" ] && ! cmp -s "$WORK/lightpilot_update.sh" "$SELF" && cp "$WORK/lightpilot_update.sh" "$SELF.new" && chmod +x "$SELF.new" && mv "$SELF.new" "$SELF" && log "updater script refreshed"
[ -n "$LP_NO_LOOP" ] || ( setsid nohup "$0" --loop >/dev/null 2>&1 & )
exit 0
