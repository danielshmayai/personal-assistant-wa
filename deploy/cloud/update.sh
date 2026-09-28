#!/usr/bin/env bash
# Keeps the cloud stack on the latest published images. bootstrap.sh installs
# it as a cron job (every 5 minutes); safe to run by hand at any time.
#
# It syncs the (public) repo for compose/config changes, pulls the images that
# build-cloud-images.yml published, and recreates only the containers whose
# image or config actually changed. Nothing happens when there is nothing new.
# Output goes to the system journal:  journalctl -t pa-cloud-update
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/pa}
COMPOSE=(docker compose -f docker-compose.cloud.yml -p pa-cloud)

# Never overlap with a previous run (a slow pull can outlast 5 minutes).
exec 9>/tmp/pa-cloud-update.lock
flock -n 9 || exit 0

log() { logger -t pa-cloud-update -- "$*"; echo "$*"; }

cd "$APP_DIR"

# .env is git-ignored, so a hard reset never touches it.
before=$(git rev-parse HEAD)
git fetch -q origin main
git reset -q --hard origin/main
after=$(git rev-parse HEAD)
[ "$before" = "$after" ] || log "config updated ${before:0:7} -> ${after:0:7}"

if ! out=$("${COMPOSE[@]}" pull -q 2>&1); then
  log "image pull failed (still running the current version): $out"
  exit 1
fi

changes=$("${COMPOSE[@]}" up -d --no-build --remove-orphans 2>&1 | grep -E 'Recreate|Created|Started' || true)
if [ -n "$changes" ]; then
  log "deployed new version: $(echo "$changes" | tr '\n' ' ')"
  docker image prune -f >/dev/null
fi
