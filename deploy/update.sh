#!/usr/bin/env bash
# Pulls the newest version of the bot and rebuilds it if anything changed.
# Runs nightly from /etc/cron.d/gta6-clipper; run it by hand with:  clipper update
set -euo pipefail
cd "$(dirname "$0")/.."
before=$(git rev-parse HEAD)
git fetch -q origin
branch=$(git rev-parse --abbrev-ref HEAD)
git reset -q --hard "origin/$branch"
after=$(git rev-parse HEAD)
if [ "$before" = "$after" ] && [ "${1:-}" != "--force" ]; then
  echo "$(date -Is) already up to date ($after)"
  exit 0
fi
echo "$(date -Is) updating $before -> $after"
install -m 755 deploy/clipper-cli.sh /usr/local/bin/clipper 2>/dev/null || true
docker compose up -d --build
docker image prune -f >/dev/null 2>&1 || true
echo "$(date -Is) update done"
