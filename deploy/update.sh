#!/usr/bin/env bash
# Pulls the newest version of the bot and rebuilds it if anything changed since the last
# successful build. Runs nightly from /etc/cron.d/gta6-clipper; run it by hand with:  clipper update
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" != "--pulled" ]; then
  git fetch -q origin
  git reset -q --hard "origin/$(git rev-parse --abbrev-ref HEAD)"
  exec bash deploy/update.sh --pulled "$@"   # carry on with the version of this script just pulled
fi
shift
force=""
[ "${1:-}" = "--force" ] && force=1
# The last version that built and started fine (written below and by install.sh). Comparing with
# it, not with the code before this pull, means a failed build is tried again the next night.
# No record (installed by an older version): rebuild once, to be safe.
deployed=$(cat .git/deployed-rev 2>/dev/null || echo unknown)
after=$(git rev-parse HEAD)
if [ "$deployed" = "$after" ] && [ -z "$force" ]; then
  echo "$(date -Is) already up to date ($after)"
  exit 0
fi
echo "$(date -Is) updating $deployed -> $after"
install -m 755 deploy/clipper-cli.sh /usr/local/bin/clipper 2>/dev/null || true
docker compose up -d --build
# Caddy reads deploy/Caddyfile through a single-file mount, which keeps showing the old file
# after git replaces it, so restart Caddy when it changed.
changed=$(git diff --name-only "$deployed" "$after" 2>/dev/null || echo deploy/Caddyfile)
if [ -n "$force" ] || grep -qx 'deploy/Caddyfile' <<<"$changed"; then
  docker compose up -d --force-recreate caddy
fi
git rev-parse HEAD > .git/deployed-rev
docker image prune -f >/dev/null 2>&1 || true
echo "$(date -Is) update done"
