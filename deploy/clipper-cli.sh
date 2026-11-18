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
  keys)     ${EDITOR:-nano} .env && dc up -d ;;
  *) cat <<HELP
clipper status     is it running + dashboard address
clipper logs       watch what the bot is doing (Ctrl+C to stop watching)
clipper doctor     what's connected and what's missing
clipper check      who on your list is live on GTA 6 right now
clipper password   show the dashboard password
clipper restart    restart the bot
clipper update     get the newest version now (it also updates itself nightly)
clipper settings   edit config.yaml (same as the dashboard's Settings page)
clipper keys       edit .env (keys can also be pasted on the dashboard's Setup page)
HELP
  ;;
esac
