#!/usr/bin/env bash
# Puts the NEW assistant (multi-tenant web app) on a fresh Ubuntu server.
# Run it on the server — one command:
#
#   curl -fsSL https://raw.githubusercontent.com/danielshmayai/personal-assistant-wa/main/deploy/cloud/bootstrap.sh | bash
#
# It asks four questions, then: installs Docker, clones the (public) repo to
# /opt/pa, generates every secret, joins Tailscale and works out the public
# URL by itself, starts the stack from the prebuilt images, and installs the
# auto-updater. At the end it prints the two Google redirect URIs to add.
#
# Re-running is safe: an existing /opt/pa/.env is reused, never regenerated.
# Answers can also be preset as env vars (OWNER_EMAIL, GOOGLE_CLIENT_ID,
# GOOGLE_CLIENT_SECRET, TS_AUTHKEY) for a non-interactive run.
set -euo pipefail

REPO_URL=${REPO_URL:-https://github.com/danielshmayai/personal-assistant-wa.git}
APP_DIR=${APP_DIR:-/opt/pa}
# Not every shell exports USER (e.g. some web consoles, sudo -i); under set -u
# an unset USER would abort the script.
USER=${USER:-$(id -un)}
# The user is only added to the docker group below, which doesn't apply to
# this already-running shell — so use sudo for docker throughout.
COMPOSE=(sudo docker compose -f docker-compose.cloud.yml -p pa-cloud)

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

ask() {  # ask VAR "Prompt" [secret]
  local var=$1 prompt=$2 secret=${3:-} val=${!1:-}
  while [ -z "$val" ]; do
    if [ -n "$secret" ]; then read -rsp "$prompt: " val </dev/tty; echo >/dev/tty
    else read -rp "$prompt: " val </dev/tty; fi
  done
  printf -v "$var" '%s' "$val"
}

set_env() {  # set_env KEY VALUE — replace or append a line in .env
  local key=$1 val=$2 f="$APP_DIR/.env"
  if grep -q "^$key=" "$f"; then
    # Values are hex/base64url/URLs — never contain '|'.
    sed -i "s|^$key=.*|$key=$val|" "$f"
  else
    echo "$key=$val" >> "$f"
  fi
}

main() {
# ── 1. Packages ─────────────────────────────────────────────────────────────
say "Installing Docker, git, openssl"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo apt-get update -y -qq
sudo apt-get install -y -qq git openssl python3 >/dev/null
sudo usermod -aG docker "$USER" || true
sudo systemctl enable --now docker

# Small VMs (e.g. 1 GB) can't run Postgres + the API without swap.
MEM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [ "$MEM_KB" -lt 4000000 ] && [ ! -f /swapfile ]; then
  say "Low RAM — adding a 4 GB swap file"
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# ── 2. Code ─────────────────────────────────────────────────────────────────
say "Fetching the app into $APP_DIR"
sudo mkdir -p "$APP_DIR" && sudo chown "$USER":"$USER" "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch -q origin main && git -C "$APP_DIR" reset -q --hard origin/main
else
  git clone -q --depth 1 --branch main "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

# ── 3. Settings + secrets ───────────────────────────────────────────────────
if [ ! -f .env ]; then
  say "A few questions (asked once)"
  ask OWNER_EMAIL          "Your Google email (becomes the admin)"
  ask GOOGLE_CLIENT_ID     "Google OAuth client ID"
  ask GOOGLE_CLIENT_SECRET "Google OAuth client secret" secret
  ask TS_AUTHKEY           "Tailscale auth key (tskey-auth-...)" secret

  gen_hex()    { openssl rand -hex 32; }
  gen_fernet() { openssl rand -base64 32 | tr '+/' '-_'; }  # Fernet key format
  # Web Push (VAPID) pair in app/push_notifications.py's format: "EC PRIVATE
  # KEY" PEM with literal \n + base64url of the 65-byte uncompressed point.
  vt=$(mktemp)
  openssl ecparam -name prime256v1 -genkey -noout -out "$vt" 2>/dev/null
  VAPID_PRIV=$(awk 'BEGIN{ORS="\\n"} {print}' "$vt" | sed 's/\\n$//')
  VAPID_PUB=$(openssl ec -in "$vt" -pubout -outform DER 2>/dev/null | tail -c 65 | base64 -w0 | tr '+/' '-_' | tr -d '=')
  rm -f "$vt"

  umask 077
  cat > .env <<EOF
OWNER_EMAIL=$OWNER_EMAIL
GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET=$GOOGLE_CLIENT_SECRET
TS_AUTHKEY=$TS_AUTHKEY
TS_HOSTNAME=pa-cloud
# Filled in automatically once Tailscale is up.
PRODUCT_BASE_URL=https://pending.invalid

POSTGRES_PASSWORD=$(gen_hex)
SESSION_SECRET=$(gen_hex)
SECRETS_MASTER_KEY=$(gen_fernet)
DB_ENCRYPTION_KEY=$(gen_fernet)
VAPID_PRIVATE_KEY=$VAPID_PRIV
VAPID_PUBLIC_KEY=$VAPID_PUB

USER_TIMEZONE=Asia/Jerusalem
LOG_FORMAT=json
EOF
  umask 022
else
  say "Reusing existing $APP_DIR/.env"
fi

# ── 4. Tailscale → public URL ───────────────────────────────────────────────
say "Joining Tailscale"
"${COMPOSE[@]}" up -d tailscale >/dev/null
DNS=""
for _ in $(seq 1 45); do
  DNS=$(sudo docker exec pa-cloud-ts tailscale status --json 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)
  [ -n "$DNS" ] && break
  sleep 2
done
if [ -z "$DNS" ]; then
  sudo docker logs pa-cloud-ts --tail 30 || true
  fail "Tailscale did not come up. Check the auth key (it may be expired or already used) and that MagicDNS is on, then re-run this script."
fi
URL="https://$DNS"
set_env PRODUCT_BASE_URL "$URL"
# The node identity is now persisted in the ts-state volume; the auth key is
# no longer needed, so don't leave it lying around.
set_env TS_AUTHKEY ""
echo "Public URL: $URL"

# ── 5. Start the app ────────────────────────────────────────────────────────
say "Starting the app"
if ! "${COMPOSE[@]}" pull -q; then
  echo "Prebuilt images unavailable — building from source instead (takes a few minutes)."
  "${COMPOSE[@]}" build
fi
"${COMPOSE[@]}" up -d --no-build --remove-orphans

for i in $(seq 1 60); do
  s=$(sudo docker inspect -f '{{.State.Health.Status}}' pa-cloud-api 2>/dev/null || echo starting)
  [ "$s" = healthy ] && break
  [ "$i" = 60 ] && { sudo docker logs pa-cloud-api --tail 60; fail "The app did not become healthy."; }
  sleep 5
done

# ── 6. Auto-updates ─────────────────────────────────────────────────────────
chmod +x deploy/cloud/update.sh
echo "*/5 * * * * $USER $APP_DIR/deploy/cloud/update.sh >/dev/null 2>&1" \
  | sudo tee /etc/cron.d/pa-cloud-update >/dev/null

# ── 7. Is it reachable from the internet? ───────────────────────────────────
say "Checking $URL"
PUBLIC_OK=""
for _ in $(seq 1 24); do
  curl -fsS --max-time 10 "$URL/health" >/dev/null 2>&1 && { PUBLIC_OK=1; break; }
  sleep 5
done

if [ -n "$PUBLIC_OK" ]; then
  REACH=" ✔ Reachable from the internet."
else
  REACH=" ⚠ Not reachable from the internet yet. In the Tailscale admin console:
   DNS → enable \"HTTPS Certificates\", and make sure Funnel is allowed
   (Access controls). It can take a minute after enabling."
fi
OWNER=$(grep '^OWNER_EMAIL=' .env | cut -d= -f2)

cat <<EOF

────────────────────────────────────────────────────────────────────────
 The new assistant is running.   $URL
────────────────────────────────────────────────────────────────────────
$REACH

 Two things left, both in a browser:

 1. Google Cloud Console → APIs & Services → Credentials → your OAuth
    client → Authorized redirect URIs → add BOTH:
      $URL/auth/callback
      $URL/auth/google/callback

 2. Tailscale admin → Machines → pa-cloud → ⋯ → Disable key expiry
    (otherwise the site goes offline in ~180 days).

 Then open $URL and sign in with $OWNER.

 Updates install themselves (checked every 5 minutes).
 BACK UP $APP_DIR/.env (password manager). It holds the keys that decrypt
 every user's saved API keys and Google tokens.
────────────────────────────────────────────────────────────────────────
EOF
}

# Called only after the whole file has been read — required for `curl | bash`.
main "$@"
