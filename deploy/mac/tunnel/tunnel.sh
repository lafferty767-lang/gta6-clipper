#!/bin/sh
# Runs a free Cloudflare quick tunnel to the dashboard and keeps it working.
# A quick tunnel can quietly die (for example after the laptop sleeps or changes Wi-Fi): cloudflared
# keeps running and keeps reporting its *.trycloudflare.com name, but that name no longer exists.
# So every 30 s this checks the public address end to end; after 2 minutes of failures it starts a
# fresh tunnel (new name), which the bot picks up and posts for the phone link.
TARGET="${TARGET:-http://clipper:8080}"
restarts=0
while true; do
  cloudflared tunnel --no-autoupdate --metrics 0.0.0.0:2000 --url "$TARGET" &
  pid=$!
  started=$(date +%s)
  fails=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 30
    host=$(curl -fsS -m 5 http://127.0.0.1:2000/quicktunnel 2>/dev/null | sed -n 's/.*"hostname":"\([^"]*\)".*/\1/p')
    [ -n "$host" ] || continue
    [ $(( $(date +%s) - started )) -ge 90 ] || continue   # a new name takes a minute to appear in DNS
    if curl -fsS -m 15 -o /dev/null "https://$host/healthz"; then
      fails=0
      restarts=0
    else
      fails=$((fails + 1))
      echo "tunnel watchdog: https://$host isn't reachable ($fails/4)"
    fi
    if [ "$fails" -ge 4 ]; then
      echo "tunnel watchdog: starting a new tunnel"
      kill "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
      break
    fi
  done
  restarts=$((restarts + 1))
  pause=$((restarts * 30)); [ "$pause" -le 600 ] || pause=600   # back off if it keeps failing
  sleep "$pause"
done
