#!/usr/bin/env bash
# GTA 6 Clipper installer for an Ubuntu server (made for Oracle Cloud's free ARM servers,
# works on any Ubuntu/Debian VPS). Safe to run again: it updates instead of reinstalling.
#
#   curl -fsSL https://raw.githubusercontent.com/lafferty767-lang/gta6-clipper/main/deploy/install.sh | sudo bash
#
# For a private repo, pass a read-only GitHub token:  ... | sudo GH_TOKEN=github_pat_xxx bash
set -euo pipefail
export HOME="${HOME:-/root}"   # cloud-init may run us without HOME

# Settings saved by the Oracle setup's first-boot script, so running this again by hand uses the
# same repo, token and password. Values given on the command line still win.
if [ -r /etc/gta6-clipper/install.env ]; then
  for k in REPO BRANCH GH_TOKEN DASHBOARD_PASSWORD; do
    [ -n "${!k:-}" ] && continue
    v=$(sed -n "s/^$k='\([^']*\)'\$/\1/p" /etc/gta6-clipper/install.env | tail -n 1)
    [ -n "$v" ] && printf -v "$k" '%s' "$v"
  done
fi

REPO="${REPO:-lafferty767-lang/gta6-clipper}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/gta6-clipper}"
GH_TOKEN="${GH_TOKEN:-}"

# Colours only on a terminal. Under the Oracle first-boot script (GTA6_CONSOLE=1) each step also
# goes to the serial console, where the Cloud Shell script reads the progress.
say() {
  if [ -t 1 ]; then printf '\n\033[1;35m==>\033[0m \033[1m%s\033[0m\n' "$*"; else printf '\n==> %s\n' "$*"; fi
  if [ -n "${GTA6_CONSOLE:-}" ]; then echo "GTA6-CLIPPER: $*" > /dev/console 2>/dev/null || true; fi
}
warn() { printf '!! %s\n' "$*"; }
die()  { printf 'xx %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run this with sudo (… | sudo bash)."
command -v apt-get >/dev/null || die "This installer supports Ubuntu/Debian. Pick the Canonical Ubuntu image."

# The normal (non-root) login user: whoever ran sudo, or 'ubuntu' when run by cloud-init.
APP_USER="${SUDO_USER:-}"
if [ -z "$APP_USER" ] || [ "$APP_USER" = root ]; then
  if id ubuntu >/dev/null 2>&1; then APP_USER=ubuntu; else APP_USER=root; fi
fi

say "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
# On a server's first boot, automatic updates can hold the package lock for a few minutes: retry.
retry() { local _; for _ in 1 2 3 4 5 6 7 8 9 10; do "$@" && return 0; sleep 20; done; return 1; }
apt_get() { apt-get -o DPkg::Lock::Timeout=120 "$@"; }
retry apt_get update -qq || die "Couldn't update the package list (apt-get update)."
retry apt_get install -y -qq git curl ca-certificates openssl >/dev/null || die "Couldn't install git and curl."

# Small servers (e.g. 1 GB) need swap to build the image and run the captions model.
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$mem_mb" -lt 4000 ] && ! swapon --show | grep -q .; then
  say "Adding 4 GB of swap (this server has ${mem_mb} MB of memory)"
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# --- firewall: Oracle's Ubuntu images only let SSH in (iptables rules, not ufw) -----------
say "Opening ports 80 and 443 on this server's firewall"
if command -v iptables >/dev/null; then
  for port in 80 443; do
    iptables -C INPUT -p tcp -m state --state NEW -m tcp --dport "$port" -j ACCEPT 2>/dev/null \
      || iptables -I INPUT 1 -p tcp -m state --state NEW -m tcp --dport "$port" -j ACCEPT
  done
fi
if [ -f /etc/iptables/rules.v4 ]; then   # make it survive reboots: insert before the first INPUT rule
  for port in 80 443; do
    rule="-A INPUT -p tcp -m state --state NEW -m tcp --dport $port -j ACCEPT"
    grep -qF -- "$rule" /etc/iptables/rules.v4 || sed -i "0,/^-A INPUT/s//${rule}\n-A INPUT/" /etc/iptables/rules.v4
  done
fi
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then   # other VPS providers
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
fi

if ! command -v docker >/dev/null; then
  say "Installing Docker"
  get_docker() { curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && sh /tmp/get-docker.sh >/dev/null; }
  retry get_docker || die "Couldn't install Docker."
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker compose version >/dev/null 2>&1 || retry apt_get install -y -qq docker-compose-plugin >/dev/null
[ "$APP_USER" != root ] && usermod -aG docker "$APP_USER" || true

say "Downloading the bot to $APP_DIR"
if [ -n "$GH_TOKEN" ]; then
  url="https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git"
else
  url="https://github.com/${REPO}.git"
fi
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
if [ -d "$APP_DIR/.git" ]; then
  old_url=$(git -C "$APP_DIR" remote get-url origin 2>/dev/null || true)
  # No token given this time: keep one already stored for this repo (private repos need it).
  if [ -z "$GH_TOKEN" ] && [[ "$old_url" == "https://x-access-token:"*"@github.com/${REPO}.git" ]]; then
    url="$old_url"
  fi
  git -C "$APP_DIR" remote set-url origin "$url"
  git -C "$APP_DIR" fetch -q origin "$BRANCH" \
    || die "Couldn't download the latest code from github.com/$REPO. If the repo is private, run again with GH_TOKEN=<read-only token>."
  git -C "$APP_DIR" checkout -q "$BRANCH"
  git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$url" "$APP_DIR" \
    || die "Couldn't download the code. If the repo is private, run again with GH_TOKEN=<read-only token>."
fi
cd "$APP_DIR"
mkdir -p data
[ -f config.yaml ] || cp config.example.yaml config.yaml

# --- .env: keep anything already there, fill in what's missing -----------------------
touch .env && chmod 600 .env
put_env() { sed -i "/^$1=/d" .env; printf '%s=%s\n' "$1" "$2" >> .env; }
get_env() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true; }
set_env() { [ -n "$(get_env "$1")" ] || put_env "$1" "$2"; }   # only if missing or empty
set_env DASHBOARD_USER admin
set_env DASHBOARD_PASSWORD "${DASHBOARD_PASSWORD:-$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)}"

# Web address: <ip-with-dashes>.sslip.io, plus <ip>.nip.io as a backup (a separate certificate
# quota, for weeks when sslip.io's free certificates run out). Addresses made from an old IP
# are updated; a custom DOMAIN you set yourself is kept.
public_ip=$(curl -fsS --max-time 10 https://api.ipify.org || curl -fsS --max-time 10 https://ifconfig.me || true)
is_auto() { [[ "$1" =~ ^(https://)?[0-9]+-[0-9]+-[0-9]+-[0-9]+\.(sslip|nip)\.io$ ]]; }
if [[ "$public_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  dashed="${public_ip//./-}"
  domain="${DOMAIN:-$(get_env DOMAIN)}"
  { [ -z "$domain" ] || is_auto "$domain"; } && domain="$dashed.sslip.io"
  put_env DOMAIN "$domain"
  put_env DOMAIN_ALT "${DOMAIN_ALT-$dashed.nip.io}"   # DOMAIN_ALT= (empty) turns the backup off
  pub=$(get_env PUBLIC_BASE_URL)
  { [ -z "$pub" ] || is_auto "$pub"; } && put_env PUBLIC_BASE_URL "https://$domain"
else
  warn "Couldn't detect this server's public IP; set DOMAIN and PUBLIC_BASE_URL in $APP_DIR/.env yourself."
fi
for k in TWITCH_CLIENT_ID TWITCH_CLIENT_SECRET ANTHROPIC_API_KEY YOUTUBE_CLIENT_ID YOUTUBE_CLIENT_SECRET \
         YOUTUBE_REFRESH_TOKEN TIKTOK_CLIENT_KEY TIKTOK_CLIENT_SECRET TIKTOK_REFRESH_TOKEN \
         METRICOOL_USER_TOKEN METRICOOL_USER_ID METRICOOL_BLOG_ID; do
  grep -q "^$k=" .env || echo "$k=" >> .env
done
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
[ "$APP_USER" != root ] && sudo -u "$APP_USER" git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

# --- helper command + nightly auto-update --------------------------------------------
install -m 755 deploy/clipper-cli.sh /usr/local/bin/clipper
cat > /etc/cron.d/gta6-clipper <<CRON
# Pull the latest version of the bot every night and rebuild if it changed.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
23 4 * * * root $APP_DIR/deploy/update.sh >> /var/log/gta6-clipper-update.log 2>&1
CRON

say "Building and starting the bot (the first build takes 5-10 minutes)"
caddy_running=$(docker compose ps -q caddy 2>/dev/null || true)
docker compose up -d --build
# On a re-run, restart Caddy so it reads the current Caddyfile (its single-file mount keeps the old one).
[ -z "$caddy_running" ] || docker compose up -d --force-recreate caddy
git rev-parse HEAD > .git/deployed-rev   # what update.sh compares against

say "Waiting for the dashboard to come up"
for _ in $(seq 1 60); do
  curl -fsS --max-time 3 http://127.0.0.1:8080/healthz >/dev/null 2>&1 && break
  sleep 5
done
curl -fsS --max-time 3 http://127.0.0.1:8080/healthz >/dev/null 2>&1 \
  || warn "The bot isn't answering yet. Check:  clipper logs"

# Download the captions model now so the first clip isn't delayed (runs in the background).
(docker compose exec -T clipper python -m clipper warmup >/dev/null 2>&1 &) || true

pw=$(get_env DASHBOARD_PASSWORD)
url=$(get_env PUBLIC_BASE_URL)
alt=$(get_env DOMAIN_ALT)
cat > "$APP_DIR/WELCOME.txt" <<TXT
GTA 6 Clipper is installed.

Dashboard:  ${url:-http://<server-ip>  (see DOMAIN in .env)}
${alt:+Backup:     https://$alt  (use it if the main address shows a certificate error)}
Username:   admin
Password:   ${pw}

Open the dashboard, go to the Setup tab, and paste your keys / press the Connect buttons.
Useful commands on this server:  clipper status | clipper logs | clipper doctor | clipper update
TXT
chown "$APP_USER":"$APP_USER" "$APP_DIR/WELCOME.txt"

say "Done!"
cat "$APP_DIR/WELCOME.txt"
echo
echo "(If the https address doesn't load yet, give it a minute to get its certificate. It needs"
echo " ports 80 and 443 open in Oracle's security list, which the Oracle setup script does for you.)"
