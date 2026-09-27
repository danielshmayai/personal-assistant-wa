#!/usr/bin/env bash
# One-time preparation of a fresh Ubuntu server for the cloud stack.
# Run on the server as the default user (e.g. "ubuntu"):
#
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/deploy/cloud/bootstrap.sh | bash
#   (or copy this file over and: bash bootstrap.sh)
#
# It installs Docker + rsync, creates /opt/pa with a .env holding freshly
# generated secrets, adds swap on small machines, and prints the values you
# need for GitHub. It does NOT need access to the repository: the GitHub
# Actions workflow (deploy-cloud.yml) pushes the code here on every deploy.
set -euo pipefail

APP_DIR=/opt/pa

echo "==> Installing Docker, rsync, openssl"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo apt-get update -y
sudo apt-get install -y rsync openssl
sudo usermod -aG docker "$USER" || true
sudo systemctl enable --now docker

# Small VMs (e.g. 1 GB) can't build/run Postgres + the API without swap.
MEM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [ "$MEM_KB" -lt 4000000 ] && [ ! -f /swapfile ]; then
  echo "==> Low RAM detected — creating a 4 GB swap file"
  sudo fallocate -l 4G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Preparing $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown "$USER":"$USER" "$APP_DIR"

if [ ! -f "$APP_DIR/.env" ]; then
  gen_hex()    { openssl rand -hex 32; }
  # Fernet key = URL-safe base64 of 32 random bytes (with padding).
  gen_fernet() { openssl rand -base64 32 | tr '+/' '-_'; }
  # VAPID (Web Push) pair in the exact format app/push_notifications.py uses:
  # private = "EC PRIVATE KEY" PEM with literal \n, public = base64url of the
  # 65-byte uncompressed P-256 point.
  VAPID_TMP=$(mktemp)
  openssl ecparam -name prime256v1 -genkey -noout -out "$VAPID_TMP" 2>/dev/null
  VAPID_PRIV=$(awk 'BEGIN{ORS="\\n"} {print}' "$VAPID_TMP" | sed 's/\\n$//')
  VAPID_PUB=$(openssl ec -in "$VAPID_TMP" -pubout -outform DER 2>/dev/null | tail -c 65 | base64 -w0 | tr '+/' '-_' | tr -d '=')
  rm -f "$VAPID_TMP"
  cat > "$APP_DIR/.env" <<EOF
POSTGRES_PASSWORD=$(gen_hex)
SESSION_SECRET=$(gen_hex)
SECRETS_MASTER_KEY=$(gen_fernet)
DB_ENCRYPTION_KEY=$(gen_fernet)
VAPID_PRIVATE_KEY=$VAPID_PRIV
VAPID_PUBLIC_KEY=$VAPID_PUB

OWNER_EMAIL=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
TS_AUTHKEY=
TS_HOSTNAME=pa-cloud
PRODUCT_BASE_URL=

USER_TIMEZONE=Asia/Jerusalem
LOG_FORMAT=json
EOF
  chmod 600 "$APP_DIR/.env"
  echo "==> Created $APP_DIR/.env with generated secrets."
else
  echo "==> $APP_DIR/.env already exists — left untouched."
fi

# Dedicated keypair for GitHub Actions → this server (deploys only).
DEPLOY_KEY="$HOME/.ssh/gh_deploy"
if [ ! -f "$DEPLOY_KEY" ]; then
  echo "==> Generating a deploy SSH key for GitHub Actions"
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  ssh-keygen -t ed25519 -N "" -C "github-actions-deploy" -f "$DEPLOY_KEY" >/dev/null
  cat "$DEPLOY_KEY.pub" >> "$HOME/.ssh/authorized_keys"
  chmod 600 "$HOME/.ssh/authorized_keys"
fi

PUBLIC_IP=$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
HOSTKEY=$(awk '{print $1" "$2}' /etc/ssh/ssh_host_ed25519_key.pub)

cat <<EOF

────────────────────────────────────────────────────────────────────────
Done. Next steps:

1. Fill in the empty values:   nano $APP_DIR/.env
   (OWNER_EMAIL, GOOGLE_CLIENT_ID/SECRET, TS_AUTHKEY, PRODUCT_BASE_URL)

2. Add these GitHub repository secrets (Settings → Secrets → Actions):
   CLOUD_HOST            = $PUBLIC_IP
   CLOUD_USER            = $USER
   CLOUD_SSH_KNOWN_HOSTS = $PUBLIC_IP $HOSTKEY
   CLOUD_SSH_KEY         = the full output of:  cat $DEPLOY_KEY

   And the repository variable (Settings → Variables → Actions):
   CLOUD_DEPLOY_ENABLED  = true

3. Run the "Deploy new assistant to cloud" workflow once from the Actions tab.

IMPORTANT: back up $APP_DIR/.env somewhere safe (a password manager is ideal).
SECRETS_MASTER_KEY decrypts every user's saved API keys and DB_ENCRYPTION_KEY
their Google tokens — lose them and that data is unrecoverable.
────────────────────────────────────────────────────────────────────────
EOF
