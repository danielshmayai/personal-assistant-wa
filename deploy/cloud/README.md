# Running the new assistant on a cloud server (free)

This deploys **only the new assistant** (the multi-tenant web app) to an
external server, fully independent of the local PC. The old WhatsApp assistant
is untouched and keeps running where it is.

| Piece | What it is |
|---|---|
| Server | Oracle Cloud **Always Free** ARM VM (Ubuntu) |
| Public HTTPS | **Tailscale Funnel** — stable `https://pa-cloud.<tailnet>.ts.net`, no domain, no open ports |
| Stack | `docker-compose.cloud.yml`: own Postgres (pgvector) + API + nginx gateway + Tailscale sidecar |
| Deploys | `.github/workflows/deploy-cloud.yml` on GitHub-hosted runners, over SSH |

It starts **clean**: new database, new vaults. Nothing is copied from the PC.

---

## 1. Create the server (Oracle Cloud)

1. Sign up at <https://www.oracle.com/cloud/free/>. A card is requested for
   identity verification; Always Free resources are not charged.
   **Choose your home region carefully**: Always Free ARM capacity exists
   only there, and it can't be changed later.
2. *Compute → Instances → Create instance*:
   - Image: **Canonical Ubuntu 24.04** (the aarch64 build)
   - Shape: **Ampere → VM.Standard.A1.Flex**, 2 OCPU / 12 GB RAM is plenty
     (up to 4 / 24 GB is free)
   - SSH keys: *Generate a key pair* and **download the private key**
3. If you get *"Out of capacity"*, retry later or pick another
   availability domain. This is common and temporary.

> You don't need your PC to log in: the **Cloud Shell** button (top-right
> of the Oracle console) opens a browser terminal. Upload the private key
> there and run `ssh -i <key> ubuntu@<public-ip>`.

## 2. Prepare the server (one command)

On the server, create the bootstrap script. Open `deploy/cloud/bootstrap.sh`
on GitHub, copy it, then:

```bash
nano bootstrap.sh      # paste, Ctrl+O, Enter, Ctrl+X
bash bootstrap.sh
```

It installs Docker, creates `/opt/pa/.env` with all secrets already
generated, creates a deploy key for GitHub, and prints the exact values
needed in step 5. **Log out and back in once afterwards**, so your user can
run `docker`.

## 3. Tailscale (public HTTPS address)

In the [Tailscale admin console](https://login.tailscale.com/admin):

1. **DNS** tab → make sure *MagicDNS* and *HTTPS Certificates* are enabled.
   Note your tailnet name (e.g. `tail557da6.ts.net`).
2. **Access controls** → make sure Funnel is allowed. The policy must contain
   (newer tailnets have it by default):
   ```json
   "nodeAttrs": [{ "target": ["autogroup:member"], "attr": ["funnel"] }]
   ```
3. **Settings → Keys → Generate auth key**, *not* ephemeral. Copy it.
4. After the first deploy, open **Machines → pa-cloud → ⋯ → Disable key
   expiry**. Otherwise the node key expires after ~180 days and the site
   goes offline.

Your public URL will be `https://pa-cloud.<tailnet>.ts.net`.

## 4. Google OAuth

In [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials),
open the OAuth client and add **Authorized redirect URIs**:

```
https://pa-cloud.<tailnet>.ts.net/auth/callback
https://pa-cloud.<tailnet>.ts.net/auth/google/callback
```

## 5. Fill in the server's `.env`

```bash
nano /opt/pa/.env
```

| Key | Value |
|---|---|
| `OWNER_EMAIL` | your Google account; its first login becomes the admin |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | from step 4 |
| `TS_AUTHKEY` | from step 3 |
| `PRODUCT_BASE_URL` | `https://pa-cloud.<tailnet>.ts.net` (no trailing slash) |

**Back up this file** (a password manager is ideal). `SECRETS_MASTER_KEY`
and `DB_ENCRYPTION_KEY` decrypt every user's saved keys and Google tokens.

## 6. Connect GitHub → server

Repository **Settings → Secrets and variables → Actions**:

- **Secrets:** `CLOUD_HOST`, `CLOUD_USER`, `CLOUD_SSH_KNOWN_HOSTS` (all
  printed by bootstrap) and `CLOUD_SSH_KEY` (output of `cat ~/.ssh/gh_deploy`
  on the server).
- **Variables:** `CLOUD_DEPLOY_ENABLED` = `true`, and optionally
  `CLOUD_PUBLIC_URL` = your URL (enables an end-to-end check after each
  deploy).

## 7. First deploy

**Actions → "Deploy new assistant to cloud" → Run workflow.** The first
build takes a few minutes. After that, every push to `main` that touches the
new assistant redeploys automatically.

Open your URL, sign in with `OWNER_EMAIL`, and complete onboarding (Gemini
key etc.). New users who sign up wait as **pending** until you approve them
in the **Admin** tab. There is no WhatsApp notification in the cloud.

## 8. Retire the local copy

Once the cloud version works:

1. Repository variable `LOCAL_PRODUCT_DEPLOY` = `false` (stops the PC from
   redeploying it).
2. On the PC: `docker compose -f docker-compose.product.yml -p pa-product down`

The old WhatsApp assistant (`docker compose` project `pa`) is not affected.

---

## Differences from the local version

- **The owner is a regular scope** (`OWNER_LEGACY_SCOPE=0`): you enter your
  own keys in onboarding/Settings, and you get your own vault and reminders.
  Memory is **not** shared with WhatsApp danidin.
- **Generic assistant prompt**: the owner-only personal instructions
  (health/training constraints) belong to the legacy scope and don't apply.
  Tell the assistant what matters to you; it keeps it in memory.
- **No Ollama fallback**: if a user's Gemini key stops working entirely,
  they get an error instead of a local model.
- **No WhatsApp**: approvals happen in the Admin tab.

## Day-2 operations (on the server)

```bash
cd /opt/pa
docker compose -f docker-compose.cloud.yml -p pa-cloud ps          # status
docker logs pa-cloud-api --tail 100                                # API logs
docker logs pa-cloud-ts --tail 50                                  # Tailscale / Funnel

# Database backup (run regularly; copy the file off the server)
docker exec pa-cloud-db pg_dump -U pa pa | gzip > ~/pa-$(date +%F).sql.gz
```

Oracle may reclaim Always Free VMs that stay almost entirely idle. Normal use
avoids this, and so do regular backups.
