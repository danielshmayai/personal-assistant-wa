# Running the new assistant on a cloud server (free)

This puts **only the new assistant** (the multi-tenant web app) on an
external server, fully independent of the local PC. The old WhatsApp assistant
is untouched.

| Piece | What it is |
|---|---|
| Server | Oracle Cloud **Always Free** ARM VM (Ubuntu) |
| Public HTTPS | **Tailscale Funnel**: stable `https://pa-cloud.<tailnet>.ts.net`, no domain, no open ports |
| Images | Built by GitHub (`build-cloud-images.yml`) → `ghcr.io/danielshmayai/pa-cloud-*` |
| Updates | The server pulls new images by itself every 5 minutes (`update.sh`) |

It starts **clean**: new database, new vaults. Nothing is copied from the PC.

---

## Setup: 4 steps, about 15 minutes

### 1. Tailscale auth key
[Tailscale admin](https://login.tailscale.com/admin) → **Settings → Keys →
Generate auth key**. Leave *ephemeral* **off**. Copy the key (`tskey-auth-…`).

Also check the **DNS** tab: *MagicDNS* and *HTTPS Certificates* must be on.

### 2. Create the server (Oracle Cloud)
1. Sign up at <https://www.oracle.com/cloud/free/>. A card is requested for
   identity verification; Always Free resources are not charged.
   **Pick your home region carefully**: free ARM capacity exists only there.
2. *Compute → Instances → Create instance*:
   - Image: **Canonical Ubuntu 24.04**
   - Shape: **Ampere → VM.Standard.A1.Flex**, 2 OCPU / 12 GB
   - SSH keys: *Generate a key pair* → **download the private key**
3. *"Out of capacity"* is common and temporary. Retry later or pick another
   availability domain.

### 3. Run one command on the server
Open **Cloud Shell** (top-right in the Oracle console, runs in the browser),
upload the private key, and connect:
```bash
chmod 600 <key-file>
ssh -i <key-file> ubuntu@<server-public-ip>
```
Then:
```bash
curl -fsSL https://raw.githubusercontent.com/danielshmayai/personal-assistant-wa/main/deploy/cloud/bootstrap.sh | bash
```
It asks for your Google email, the Google OAuth client ID + secret (the same
client you already use), and the Tailscale key. Everything else is automatic.
At the end it prints your URL and two redirect URIs.

### 4. Two clicks in the browser
- **Google Cloud Console** → Credentials → your OAuth client →
  *Authorized redirect URIs* → add the two URIs the script printed.
- **Tailscale admin** → Machines → `pa-cloud` → ⋯ → **Disable key expiry**
  (otherwise the site goes offline in about 180 days).

Open the URL and sign in with your email. New users who sign up wait as
**pending** until you approve them in the **Admin** tab.

**Back up `/opt/pa/.env`** (a password manager is ideal). It holds the keys
that decrypt every user's saved API keys and Google tokens.

---

## Retire the local copy
Once the cloud version works:
1. Repository variable `LOCAL_PRODUCT_DEPLOY` = `false` (the PC stops
   redeploying it).
2. On the PC: `docker compose -f docker-compose.product.yml -p pa-product down`

## Differences from the local version
- **You're a regular user who is also admin** (`OWNER_LEGACY_SCOPE=0`): your
  own keys, vault and reminders. Memory is **not** shared with WhatsApp
  danidin.
- **Generic assistant prompt.** Owner-only personal instructions don't apply;
  tell the assistant what matters to you and it keeps it in memory.
- **No Ollama fallback** and **no WhatsApp** (approve users in the Admin tab).

## Operations (on the server)
```bash
cd /opt/pa
C="docker compose -f docker-compose.cloud.yml -p pa-cloud"
$C ps                                   # status
docker logs pa-cloud-api --tail 100     # app logs
journalctl -t pa-cloud-update           # what the auto-updater did
./deploy/cloud/update.sh                # update now instead of waiting

# Roll back: pin a commit's images, then update
echo "PA_IMAGE_TAG=<commit-sha>" >> .env && ./deploy/cloud/update.sh

# Database backup (copy the file off the server)
docker exec pa-cloud-db pg_dump -U pa pa | gzip > ~/pa-$(date +%F).sql.gz
```

If GitHub reports the images as private (a warning in the build workflow),
make each one public once: GitHub → your profile → **Packages** →
`pa-cloud-api` / `pa-cloud-gateway` → Package settings → Change visibility →
Public. The source code is already public, so the images reveal nothing new.
Until then, bootstrap still works (it builds from source), but automatic
updates can't install (`journalctl -t pa-cloud-update` shows the pull error).

Oracle may reclaim Always Free VMs that stay almost completely idle; normal use
avoids that.
