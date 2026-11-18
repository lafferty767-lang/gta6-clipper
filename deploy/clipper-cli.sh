#!/usr/bin/env bash
# Helper installed as /usr/local/bin/clipper on the server.
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/gta6-clipper}"
cd "$APP_DIR"
dc() { if docker info >/dev/null 2>&1; then docker compose "$@"; else sudo docker compose "$@"; fi; }

case "${1:-help}" in
  status)   dc ps; echo
            grep -E '^PUBLIC_BASE_URL=' .env | cut -d= -f2- | sed 's/^/Dashboard: /'
            grep -E '^DOMAIN_ALT=.' .env | cut -d= -f2- | sed 's|^|Backup:    https://|' || true ;;
  logs)     dc logs -f --tail 200 clipper ;;
  restart)  dc restart ;;
  stop)     dc stop ;;
  start)    dc up -d ;;
  update)   sudo "$APP_DIR/deploy/update.sh" --force ;;
  doctor)   dc exec clipper python -m clipper doctor ;;
  check)    dc exec clipper python -m clipper check ;;
  password) grep -E '^DASHBOARD_PASSWORD=' .env | cut -d= -f2- ;;
  settings) ${EDITOR:-nano} config.yaml ;;
  keys)     # Keys saved on the dashboard's Setup page override .env, so a key changed here is
            # removed from the dashboard's copy (data/secrets.json) and the .env value is used.
            before=$(mktemp); cp .env "$before"
            ${EDITOR:-nano} .env
            changed=$(diff <(sort "$before") <(sort .env) | sed -n 's/^> \([A-Z_][A-Z0-9_]*\)=.*/\1/p' | sort -u || true)
            rm -f "$before"
            if [ -n "$changed" ] && sudo test -f data/secrets.json; then
              # shellcheck disable=SC2086
              sudo python3 - data/secrets.json $changed <<'PY'
import json, sys
path, keys = sys.argv[1], set(sys.argv[2:])
data = json.load(open(path))
dropped = sorted(k for k in keys if k in data)
for k in dropped:
    del data[k]
with open(path, "w") as f:
    json.dump(data, f, indent=2)
if dropped:
    print("Using the new .env value for: " + ", ".join(dropped))
PY
            fi
            dc up -d ;;
  *) cat <<HELP
clipper status     is it running + dashboard address
clipper logs       watch what the bot is doing (Ctrl+C to stop watching)
clipper doctor     what's connected and what's missing
clipper check      who on your list is live on GTA 6 right now
clipper password   show the dashboard password
clipper restart    restart the bot
clipper update     get the newest version now (it also updates itself nightly)
clipper settings   edit config.yaml (same as the dashboard's Settings page)
clipper keys       edit the keys in .env (easier: paste them on the dashboard's Setup page)
HELP
  ;;
esac
