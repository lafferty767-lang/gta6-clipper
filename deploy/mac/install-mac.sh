#!/usr/bin/env bash
# GTA 6 Clipper on your Mac. Paste this into the Terminal app (needs Docker Desktop):
#   curl -fsSL https://raw.githubusercontent.com/lafferty767-lang/gta6-clipper/main/deploy/mac/install-mac.sh | bash
# Run the same line again any time to update. Your keys, settings and clips are kept.
set -euo pipefail

REPO="${REPO:-lafferty767-lang/gta6-clipper}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-$HOME/GTA6-Clipper}"
DOCKER_URL="https://www.docker.com/products/docker-desktop/"

say()  { printf '\n\033[1;35m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }
dc()   { docker compose -f docker-compose.yml -f docker-compose.mac.yml "$@"; }

if [ "$(uname -s)" != Darwin ] && [ -z "${ALLOW_NON_MAC:-}" ]; then
  die "This installer is for a Mac. On a Linux server use deploy/install.sh instead."
fi

# --- Docker Desktop ---------------------------------------------------------------------
say "Checking Docker Desktop"
if ! docker info >/dev/null 2>&1; then
  if [ -d /Applications/Docker.app ]; then
    info "starting Docker Desktop..."
    open -a Docker
    for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 3; done
  fi
  if ! docker info >/dev/null 2>&1; then
    command -v open >/dev/null && open "$DOCKER_URL"
    die "Docker Desktop isn't installed or running yet. Download it from $DOCKER_URL
   (choose \"Apple Silicon\" or \"Intel chip\" to match your Mac), open it once and accept its
   terms, then paste this command again."
  fi
fi
info "Docker is running"

# --- download the bot (keeps .env, config.yaml and data/) ---------------------------------
say "Downloading the bot to $APP_DIR"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" | tar -xz -C "$tmp" \
  || die "Couldn't download the bot from github.com/$REPO. Check your internet connection."
mkdir -p "$APP_DIR"
cp -R "$tmp"/*/. "$APP_DIR/"
cd "$APP_DIR"
mkdir -p data
[ -f config.yaml ] || cp config.example.yaml config.yaml

# --- .env: login and empty key slots (keys are pasted on the dashboard's Setup page) -------
touch .env && chmod 600 .env
get_env() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true; }
set_env() { [ -n "$(get_env "$1")" ] || { sed -i.bak "/^$1=/d" .env && rm -f .env.bak; printf '%s=%s\n' "$1" "$2" >> .env; }; }
set_env DASHBOARD_USER admin
set_env DASHBOARD_PASSWORD "${DASHBOARD_PASSWORD:-$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)}"
for k in TWITCH_CLIENT_ID TWITCH_CLIENT_SECRET ANTHROPIC_API_KEY YOUTUBE_CLIENT_ID YOUTUBE_CLIENT_SECRET \
         YOUTUBE_REFRESH_TOKEN TIKTOK_CLIENT_KEY TIKTOK_CLIENT_SECRET TIKTOK_REFRESH_TOKEN \
         METRICOOL_USER_TOKEN METRICOOL_USER_ID METRICOOL_BLOG_ID; do
  grep -q "^$k=" .env || echo "$k=" >> .env
done

# --- start ------------------------------------------------------------------------------
say "Building and starting the bot (the first time takes 5-10 minutes)"
dc up -d --build --remove-orphans

say "Waiting for the dashboard"
for _ in $(seq 1 60); do
  curl -fsS --max-time 3 http://127.0.0.1:8080/healthz >/dev/null 2>&1 && break
  sleep 5
done
curl -fsS --max-time 3 http://127.0.0.1:8080/healthz >/dev/null 2>&1 \
  || die "The bot didn't start. See what went wrong with:  cd ~/GTA6-Clipper && docker compose logs --tail 50 clipper"

public=""
for _ in $(seq 1 30); do
  public=$(dc logs tunnel 2>/dev/null | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n 1 || true)
  [ -n "$public" ] && break
  sleep 2
done

pw=$(get_env DASHBOARD_PASSWORD)
cat > WELCOME.txt <<TXT
GTA 6 Clipper is running on this Mac.

Dashboard:  http://localhost:8080
Username:   admin
Password:   $pw
Public address (for Connect TikTok): ${public:-not ready yet, see the Setup page on the dashboard}

It runs while your Mac is awake and Docker Desktop is open.
To update or restart it, paste the install command into Terminal again.
TXT
say "Done!"
cat WELCOME.txt
echo
echo "(Saved in $APP_DIR/WELCOME.txt)"
command -v open >/dev/null && open "http://localhost:8080" || true
