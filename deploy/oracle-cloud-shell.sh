#!/usr/bin/env bash
# Creates a free Oracle Cloud ARM server for GTA 6 Clipper and installs the bot on it.
#
# Run it in Oracle Cloud Shell (the  >_  icon at the top right of the Oracle Cloud console):
#   curl -fsSL https://raw.githubusercontent.com/lafferty767-lang/gta6-clipper/main/deploy/oracle-cloud-shell.sh | bash
#
# What it does, all inside your own Oracle account:
#   1. creates a network that lets SSH (22) and the web (80/443) in
#   2. launches an Always Free Ampere A1 server (2 CPUs / 12 GB, Ubuntu 24.04) in your home region,
#      retrying while Oracle says it's out of capacity
#   3. the server downloads and installs the bot by itself on first boot; this script follows its
#      progress through Oracle's API, so it works even when Cloud Shell has no internet access
# Safe to run again: it reuses what already exists and picks up where it stopped.
#
# Options (put them before "bash", e.g.  ... | OCPUS=1 MEMORY_GB=6 bash):
#   OCPUS / MEMORY_GB  server size (default 2 / 12, the Always Free limit; don't go higher)
#   BOOT_GB            disk size (default 100; Always Free includes 200 GB in total)
#   GH_TOKEN           read-only GitHub token, only needed if the repo is private
set -euo pipefail

REPO="${REPO:-lafferty767-lang/gta6-clipper}"
BRANCH="${BRANCH:-main}"
NAME="${NAME:-gta6-clipper}"
OCPUS="${OCPUS:-2}"
MEMORY_GB="${MEMORY_GB:-12}"
BOOT_GB="${BOOT_GB:-100}"
RETRY_SECONDS="${RETRY_SECONDS:-60}"
MAX_HOURS="${MAX_HOURS:-23}"         # Cloud Shell sessions end after 24 hours anyway
GH_TOKEN="${GH_TOKEN:-}"
SHAPE="VM.Standard.A1.Flex"
STATE_DIR="$HOME/.gta6-clipper"
STATE="$STATE_DIR/state"
INFO_FILE="$HOME/gta6-clipper-server.txt"
INSTANCE=""
SSH_PUB=""

say()  { printf '\n\033[1;35m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }
ok()   { [ -n "${1:-}" ] && [ "$1" != "null" ]; }
q()    { oci "$@" --raw-output 2>/dev/null || true; }   # a lookup that may come back empty

state_get() { [ -f "$STATE" ] && grep -E "^$1=" "$STATE" | tail -n 1 | cut -d= -f2- || true; }
state_set() {
  { grep -vE "^$1=" "$STATE" 2>/dev/null || true; printf '%s=%s\n' "$1" "$2"; } > "$STATE.tmp"
  chmod 600 "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
}

# Reads a failed OCI CLI call's error output and prints: capacity | throttle | limit | network | other
classify() {
  python3 - "$1" <<'PY'
import json, re, sys
text = open(sys.argv[1], errors="replace").read()
status, code, msg = 0, "", text
m = re.search(r"ServiceError:\s*(\{.*\})", text, re.S)
if m:
    try:
        j = json.loads(m.group(1))
        status, code, msg = int(j.get("status") or 0), str(j.get("code") or ""), str(j.get("message") or "")
    except ValueError:
        pass
low = f"{code} {msg}".lower()
if "capacity" in low or status >= 500:
    print("capacity")
elif status == 429 or "toomanyrequests" in low:
    print("throttle")
elif "limitexceeded" in low or "quotaexceeded" in low or ("limit" in low and "exceed" in low):
    print("limit")
elif not m and re.search(r"connect|timed out|timeout|temporar|reset by peer", low):
    print("network")
else:
    print("other")
PY
}

# wait_state SECONDS STATE... : polls the server until it is in one of STATEs; prints the last state seen
wait_state() {
  local limit=$(( $(date +%s) + $1 )) st=""
  shift
  while :; do
    st=$(q compute instance get --instance-id "$INSTANCE" --query 'data."lifecycle-state"')
    case " $* " in *" $st "*) break ;; esac
    [ "$(date +%s)" -lt "$limit" ] || break
    sleep 15
  done
  printf '%s' "$st"
}

# Prints the server's serial console (the install log is mirrored there), read through Oracle's API.
console_log() {
  local h
  h=$(q compute console-history capture --instance-id "$INSTANCE" --wait-for-state SUCCEEDED \
        --wait-for-state FAILED --max-wait-seconds 120 --query 'data.id')
  ok "$h" || return 0
  oci compute console-history get-content --instance-console-history-id "$h" --file - --length 1048576 2>/dev/null \
    | tr -d '\r' | sed 's/\x1b\[[0-9;]*m//g' || true
  oci compute console-history delete --instance-console-history-id "$h" --force >/dev/null 2>&1 || true
}

main() {
  command -v oci >/dev/null || die "The 'oci' command isn't here. Run this in Oracle Cloud Shell (the >_ icon in the Oracle console)."
  command -v python3 >/dev/null || die "python3 is missing (it's normally part of Oracle Cloud Shell)."
  export OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True
  mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR"

  # --- account and home region ---------------------------------------------------------
  local TENANCY C HOME_REGION
  TENANCY="${OCI_TENANCY:-}"
  ok "$TENANCY" || TENANCY=$(q iam availability-domain list --query 'data[0]."compartment-id"')
  [[ "$TENANCY" == ocid1.tenancy.* ]] || die "Couldn't find your Oracle account (tenancy). Are you in Oracle Cloud Shell?"
  C="${COMPARTMENT_ID:-$TENANCY}"
  HOME_REGION=$(q iam region-subscription list --tenancy-id "$TENANCY" \
                  --query 'data[?"is-home-region"]."region-name" | [0]')
  if ok "$HOME_REGION"; then
    export OCI_CLI_REGION="$HOME_REGION"   # Always Free servers can only be created in the home region
    say "Using your home region: $HOME_REGION"
  else
    warn "Couldn't look up your home region; using Cloud Shell's region."
  fi

  # --- can the server download the code? (skipped when Cloud Shell has no internet) ----
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
           "https://raw.githubusercontent.com/$REPO/$BRANCH/deploy/install.sh" 2>/dev/null || true)
  if [ "$code" = 404 ] && [ -z "$GH_TOKEN" ]; then
    die "github.com/$REPO isn't public, so the server can't download the bot. Make the repo public
   (GitHub > the repo > Settings > Danger Zone > Change visibility) and run this again."
  fi

  # --- SSH key, so you can log in to the server later if you ever need to ---------------
  # (RSA, because Cloud Shell runs in FIPS mode, which refuses ed25519 keys.) Optional: the
  # server is set up without SSH, so a failure here doesn't stop anything.
  local k
  for k in id_rsa id_ecdsa id_ed25519; do
    if [ -f "$HOME/.ssh/$k.pub" ]; then SSH_PUB="$HOME/.ssh/$k.pub"; break; fi
  done
  if [ -z "$SSH_PUB" ]; then
    mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
    if ssh-keygen -q -t rsa -b 4096 -N "" -f "$HOME/.ssh/id_rsa" -C "gta6-clipper-cloudshell" </dev/null; then
      SSH_PUB="$HOME/.ssh/id_rsa.pub"
    else
      warn "Couldn't create an SSH key; continuing without one (you won't need it for normal use)."
    fi
  fi

  # --- network: VCN + internet gateway + route + firewall rules + public subnet ----------
  say "Setting up the network"
  local VCN IGW RT SL SUBNET
  VCN=$(q network vcn list -c "$C" --display-name "$NAME-vcn" --lifecycle-state AVAILABLE --query 'data[0].id')
  if ! ok "$VCN"; then
    VCN=$(oci network vcn create -c "$C" --display-name "$NAME-vcn" --cidr-blocks '["10.0.0.0/16"]' \
            --dns-label clippervcn --wait-for-state AVAILABLE --query 'data.id' --raw-output) \
      || die "Couldn't create the network (VCN). If Oracle says you hit a limit, delete an unused VCN in the console."
    info "created network $NAME-vcn"
  else
    info "reusing network $NAME-vcn"
  fi

  IGW=$(q network internet-gateway list -c "$C" --vcn-id "$VCN" --query 'data[0].id')
  if ! ok "$IGW"; then
    IGW=$(oci network internet-gateway create -c "$C" --vcn-id "$VCN" --is-enabled true \
            --display-name "$NAME-igw" --wait-for-state AVAILABLE --query 'data.id' --raw-output)
  fi

  RT=$(oci network vcn get --vcn-id "$VCN" --query 'data."default-route-table-id"' --raw-output)
  oci network route-table update --rt-id "$RT" --force --wait-for-state AVAILABLE \
    --route-rules "[{\"destination\":\"0.0.0.0/0\",\"destinationType\":\"CIDR_BLOCK\",\"networkEntityId\":\"$IGW\"}]" \
    >/dev/null

  SL=$(oci network vcn get --vcn-id "$VCN" --query 'data."default-security-list-id"' --raw-output)
  local tcp='"source":"0.0.0.0/0","sourceType":"CIDR_BLOCK","protocol":"6","isStateless":false'
  oci network security-list update --security-list-id "$SL" --force --wait-for-state AVAILABLE \
    --ingress-security-rules "[
      {$tcp,\"tcpOptions\":{\"destinationPortRange\":{\"min\":22,\"max\":22}}},
      {$tcp,\"tcpOptions\":{\"destinationPortRange\":{\"min\":80,\"max\":80}}},
      {$tcp,\"tcpOptions\":{\"destinationPortRange\":{\"min\":443,\"max\":443}}},
      {\"source\":\"0.0.0.0/0\",\"sourceType\":\"CIDR_BLOCK\",\"protocol\":\"1\",\"isStateless\":false,\"icmpOptions\":{\"type\":3,\"code\":4}},
      {\"source\":\"10.0.0.0/16\",\"sourceType\":\"CIDR_BLOCK\",\"protocol\":\"1\",\"isStateless\":false,\"icmpOptions\":{\"type\":3}}]" \
    --egress-security-rules '[{"destination":"0.0.0.0/0","destinationType":"CIDR_BLOCK","protocol":"all","isStateless":false}]' \
    >/dev/null
  info "firewall lets in SSH (22) and the web (80, 443)"

  SUBNET=$(q network subnet list -c "$C" --vcn-id "$VCN" --display-name "$NAME-subnet" \
             --lifecycle-state AVAILABLE --query 'data[0].id')
  if ! ok "$SUBNET"; then
    SUBNET=$(oci network subnet create -c "$C" --vcn-id "$VCN" --display-name "$NAME-subnet" \
               --cidr-block 10.0.0.0/24 --dns-label clippersub --route-table-id "$RT" \
               --security-list-ids "[\"$SL\"]" --prohibit-public-ip-on-vnic false \
               --wait-for-state AVAILABLE --query 'data.id' --raw-output)
    info "created public subnet"
  fi

  # --- the server ----------------------------------------------------------------------
  local DASH_PW FRESH=0 st
  DASH_PW=$(state_get DASH_PW)
  INSTANCE=$(q compute instance list -c "$C" --display-name "$NAME" \
               --query 'data[?"lifecycle-state"!=`TERMINATED` && "lifecycle-state"!=`TERMINATING`] | [0].id')
  if ok "$INSTANCE"; then
    st=$(q compute instance get --instance-id "$INSTANCE" --query 'data."lifecycle-state"')
    say "Your server $NAME already exists ($st), reusing it"
    if [ "$st" = STOPPED ] || [ "$st" = STOPPING ]; then
      info "starting it"
      oci compute instance action --instance-id "$INSTANCE" --action START --wait-for-state RUNNING \
        --max-wait-seconds 900 >/dev/null || warn "It hasn't started yet; check Compute > Instances in the console."
    fi
  else
    if ! ok "$DASH_PW"; then
      DASH_PW=$(python3 -c 'import secrets; print(secrets.token_urlsafe(15))')
      state_set DASH_PW "$DASH_PW"
    fi
    launch_server "$C" "$SUBNET" "$DASH_PW"
    FRESH=1
  fi

  [ "$(wait_state 900 RUNNING)" = RUNNING ] || warn "The server isn't running yet; check Compute > Instances in the console."
  local IP=""
  for _ in $(seq 1 30); do
    IP=$(q compute instance list-vnics --instance-id "$INSTANCE" --query 'data[0]."public-ip"')
    ok "$IP" && break
    sleep 10
  done
  ok "$IP" || die "The server has no public IP address. Check it under Compute > Instances in the Oracle console."
  local DOMAIN="${IP//./-}.sslip.io"

  write_info() {
    {
      echo "GTA 6 Clipper server"
      echo "Dashboard: https://$DOMAIN"
      echo "Backup:    https://${IP//./-}.nip.io   (use it if the main address shows a certificate error)"
      echo "Username:  admin"
      if ok "$DASH_PW"; then echo "Password:  $DASH_PW"
      else echo "Password:  in /opt/gta6-clipper/WELCOME.txt on the server (or run 'clipper password' there)"; fi
      echo "Server IP: $IP"
      echo "SSH:       ssh ubuntu@$IP   (from Oracle Cloud Shell, when its Network is set to Public)"
    } > "$INFO_FILE"
    chmod 600 "$INFO_FILE"
  }
  write_info

  wait_for_install "$DOMAIN" "$IP" "$FRESH"

  write_info
  say "All set!"
  cat "$INFO_FILE"
  echo
  echo "Open the dashboard, log in, and go to the Setup tab to connect Twitch, YouTube, TikTok and Claude."
  echo "(This is saved in Cloud Shell. To see it again:  cat ~/$(basename "$INFO_FILE"))"
}

launch_server() {
  local C="$1" SUBNET="$2" DASH_PW="$3" IMAGE="" ver
  for ver in 24.04 22.04; do
    IMAGE=$(q compute image list -c "$C" --operating-system "Canonical Ubuntu" --operating-system-version "$ver" \
              --shape "$SHAPE" --lifecycle-state AVAILABLE --sort-by TIMECREATED --sort-order DESC --all \
              --query "data[?contains(\"display-name\", 'aarch64') && !contains(\"display-name\", 'Minimal')] | [0].id")
    if ok "$IMAGE"; then info "using Ubuntu $ver (ARM)"; break; fi
  done
  ok "$IMAGE" || die "No Ubuntu image for $SHAPE found in this region."

  # What the server runs on its first boot. Its output also goes to the serial console, which
  # this script reads through Oracle's API (no SSH or internet needed in Cloud Shell).
  local USERDATA auth=""
  USERDATA="$STATE_DIR/user-data.sh"
  [ -n "$GH_TOKEN" ] && auth="-H 'Authorization: Bearer $GH_TOKEN'"
  cat > "$USERDATA" <<EOF
#!/bin/bash
# GTA 6 Clipper first-boot install. Log: /var/log/gta6-clipper-install.log
exec > >(tee -a /var/log/gta6-clipper-install.log) 2>&1
mark() { echo "GTA6-CLIPPER: \$*"; echo "GTA6-CLIPPER: \$*" > /dev/console 2>/dev/null || true; }
mark "first boot, downloading the installer"
for i in \$(seq 1 30); do
  curl -fsSL $auth https://raw.githubusercontent.com/$REPO/$BRANCH/deploy/install.sh -o /root/gta6-install.sh && break
  mark "download failed (try \$i), retrying"; sleep 10
done
for try in 1 2 3; do
  if REPO='$REPO' BRANCH='$BRANCH' GH_TOKEN='$GH_TOKEN' DASHBOARD_PASSWORD='$DASH_PW' bash /root/gta6-install.sh; then
    mark "INSTALL-OK"; exit 0
  fi
  mark "install attempt \$try failed"; sleep 60
done
mark "INSTALL-FAILED"
EOF
  chmod 600 "$USERDATA"

  local ADS
  ADS=$(oci iam availability-domain list -c "$C" --query "join(' ', data[].name)" --raw-output) \
    || die "Couldn't list availability domains."
  say "Creating the server ($OCPUS CPUs, ${MEMORY_GB} GB memory, ${BOOT_GB} GB disk)"
  info "Free ARM servers are popular. While Oracle is out of capacity this keeps trying every ${RETRY_SECONDS}s."
  info "Keep this tab open. If it stops, run the same command again: it picks up where it left off."

  local attempt=0 rounds=0 start backoff=120 err AD kind st mins
  local ssh_args=()
  [ -n "$SSH_PUB" ] && ssh_args=(--ssh-authorized-keys-file "$SSH_PUB")
  start=$(date +%s)
  err="$STATE_DIR/launch-error.txt"
  while :; do
    for AD in $ADS; do
      attempt=$((attempt + 1))
      if INSTANCE=$(oci --no-retry compute instance launch -c "$C" --availability-domain "$AD" --display-name "$NAME" \
            --shape "$SHAPE" --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEMORY_GB}" \
            --image-id "$IMAGE" --subnet-id "$SUBNET" --assign-public-ip true \
            --boot-volume-size-in-gbs "$BOOT_GB" ${ssh_args[@]+"${ssh_args[@]}"} --user-data-file "$USERDATA" \
            --query 'data.id' --raw-output 2>"$err"); then
        info "Oracle accepted the request, waiting for the server to start"
        st=$(wait_state 1200 RUNNING TERMINATED)
        if [ "$st" = TERMINATED ]; then
          info "Oracle couldn't place it after all (no capacity); trying again"
          continue
        fi
        say "Server created"
        return 0
      fi
      kind=$(classify "$err")
      mins=$(( ($(date +%s) - start) / 60 ))
      case "$kind" in
        capacity)
          printf '    attempt %d (%s): no free capacity yet, %d min so far\n' "$attempt" "${AD##*:}" "$mins"
          backoff=120 ;;
        throttle)
          printf '    attempt %d: Oracle asked us to slow down, waiting %ds\n' "$attempt" "$backoff"
          sleep "$backoff"; backoff=$(( backoff * 2 > 600 ? 600 : backoff * 2 )) ;;
        network)
          printf '    attempt %d: connection problem, retrying\n' "$attempt"; sleep 15 ;;
        limit)
          die "Oracle says this account is over its free limit for ARM servers or disk space.
   Another ARM server may already exist (Compute > Instances), or try a smaller size:
   ... | OCPUS=1 MEMORY_GB=6 bash
   Details: $(tr '\n' ' ' < "$err" | cut -c1-400)" ;;
        *)
          die "Oracle refused to create the server: $(tr '\n' ' ' < "$err" | cut -c1-600)" ;;
      esac
    done
    rounds=$((rounds + 1))
    if [ $(( $(date +%s) - start )) -gt $(( MAX_HOURS * 3600 )) ]; then
      die "No capacity after ${MAX_HOURS} hours. Run this again later, or see SETUP.md, 'Out of capacity'."
    fi
    if [ $((rounds % 30)) -eq 0 ]; then
      warn "Still no capacity. Upgrading the Oracle account to Pay As You Go usually fixes this quickly,"
      warn "and Always Free servers stay free after upgrading. See SETUP.md, 'Out of capacity'."
    fi
    sleep "$RETRY_SECONDS"
  done
}

wait_for_install() {
  local DOMAIN="$1" IP="$2" FRESH="$3" t0 deadline seen=0 last="" log line
  say "The server is installing the bot by itself (about 10-15 minutes)"
  info "Progress is read from the server's console every 30 seconds."
  t0=$(date +%s)
  deadline=$(( t0 + 2400 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -fsS --max-time 5 "https://$DOMAIN/healthz" >/dev/null 2>&1; then
      info "the dashboard answers at https://$DOMAIN"
      return 0
    fi
    log=$(console_log)
    grep -q "GTA6-CLIPPER: " <<<"$log" && seen=1
    if grep -q "GTA6-CLIPPER: INSTALL-OK" <<<"$log"; then
      info "install finished"
      if ! ok "$DASH_PW"; then   # reusing a server from an earlier run: its password is in the log
        line=$(grep -E '^Password: ' <<<"$log" | tail -n 1 | sed 's/^Password: *//' || true)
        if ok "$line"; then DASH_PW="$line"; state_set DASH_PW "$line"; fi
      fi
      info "(if the https address doesn't load right away, give it a minute to get its certificate)"
      return 0
    fi
    if grep -q "GTA6-CLIPPER: INSTALL-FAILED" <<<"$log"; then
      warn "The install on the server failed. The end of its log:"
      grep -vE '^\s*$' <<<"$log" | tail -n 25 | sed 's/^/      /'
      warn "Copy these lines when asking for help. To retry after fixing the cause, set Cloud Shell's"
      warn "Network to Public and run:  ssh ubuntu@$IP sudo bash /root/gta6-install.sh"
      return 0
    fi
    line=$(grep -E '^==> |^GTA6-CLIPPER: ' <<<"$log" | tail -n 1 | sed 's/^==> //; s/^GTA6-CLIPPER: //' || true)
    if [ -n "$line" ] && [ "$line" != "$last" ]; then info "server: $line"; last="$line"; fi
    if [ "$seen" = 0 ] && [ $(( $(date +%s) - t0 )) -gt $(( FRESH == 1 ? 600 : 90 )) ]; then
      info "Can't see the server's progress from here, but it installs on its own."
      info "Open https://$DOMAIN in your browser; allow up to 15 minutes after the server was created."
      return 0
    fi
    sleep 30
  done
  warn "The install hasn't reported back after 40 minutes. Open https://$DOMAIN in your browser;"
  warn "if it doesn't load, run this command again to see the server's log."
}

main "$@" </dev/null
