# Personal Assistant — Setup & Deployment Guide

---

## Part 1: Local Testing (no WhatsApp, no tunnel)

### Prerequisites (one-time)

**Step 1 — Verify WSL2**
```powershell
# In PowerShell (Admin)
wsl --version
# Should show "WSL version: 2.x". If not installed:
wsl --install
```

**Step 2 — Verify Docker Desktop**
- Open Docker Desktop → Settings → General → confirm **"Use the WSL 2 based engine"** is ON
- Settings → Resources → WSL Integration → enable your default distro
- Test in terminal:
```bash
docker run hello-world
```

**Step 3 — Verify GPU access**
```bash
# Check driver (should show GTX 1660 with ~4GB)
nvidia-smi

# Check Docker GPU passthrough
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
# Should show the same GPU inside the container
```
> If the Docker GPU test fails, update your NVIDIA driver (Game Ready or Studio driver from nvidia.com).

**Step 4 — Set power plan** (prevents GPU throttling + sleep)
```powershell
# PowerShell (Admin)
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
```

---

### Start the Dev Stack

**Step 5 — Create your `.env`**
```bash
cd D:/Claude/pa
cp .env.example .env
```
Open `.env` and set only this for now:
```
POSTGRES_PASSWORD=localdev123
```
> WAHA, Cloudflare, and WhatsApp ID are NOT needed for local testing.

**Step 6 — Start containers** (postgres + ollama + backend only)
```bash
docker compose up -d --build
```
Watch startup:
```bash
docker compose logs -f
# Wait until you see pa-backend become healthy
docker compose ps
```

**Step 7 — Pull the model** (~2.5GB download, one-time only)
```bash
docker exec pa-ollama ollama pull gemma3:4b-it-qat
```
Verify when done:
```bash
docker exec pa-ollama ollama list
```

**Step 8 — Test health**
```bash
curl http://localhost:8000/health
```
Expected response:
```json
{"status":"ok","checks":{"ollama":"ok","waha":"unreachable","postgres":"configured"}}
```
> `waha: unreachable` is normal here — it's not running in dev mode.

**Step 9 — Test the LLM pipeline**
```bash
# General reminder
curl -X POST http://localhost:8000/test \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"Remind me to buy groceries tomorrow\"}"

# Development task
curl -X POST http://localhost:8000/test \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"The login page throws a 500 error when I click submit with an empty email field\"}"

# Financial log
curl -X POST http://localhost:8000/test \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"I paid 350 shekels for the AWS bill today\"}"
```
> First request will be slow (~15-30s) as the model loads into VRAM. Subsequent requests are fast.

**Step 10 — Monitor VRAM**
```bash
nvidia-smi
# Expect ~2.5-3GB used when model is loaded. Should NOT exceed 3.5GB.
```

**Step 11 — Interactive API docs** (optional)

Open in browser: `http://localhost:8000/docs`
Use the Swagger UI to test `/health` and `/test` interactively.

**Stop the stack:**
```bash
docker compose down        # Stop containers, keep data
docker compose down -v     # Stop containers + wipe all data (fresh start)
```

---

## Part 2: Production Deployment (WhatsApp + Cloudflare Tunnel)

> Complete Part 1 successfully before proceeding.

### Cloudflare Tunnel Setup

**Step 12 — Create the tunnel**
1. Go to [one.dash.cloudflare.com](https://one.dash.cloudflare.com)
2. **Zero Trust → Networks → Tunnels → Create a tunnel**
3. Name it `pa-tunnel`, click Save
4. Select **Docker** as the connector — **do not run the shown command**, just copy the token
5. Add a **Public Hostname**:
   - Subdomain: `pa` (or any name you like)
   - Domain: select one of your Cloudflare-managed domains
   - Service Type: `HTTP`
   - URL: `backend:8000`
6. Click Save

---

### Configure `.env` for Production

**Step 13 — Fill in all production values**

Open `D:\Claude\pa\.env` and fill in everything:
```env
# PostgreSQL
POSTGRES_USER=pa
POSTGRES_PASSWORD=<strong-random-password>
POSTGRES_DB=pa

# WAHA
WAHA_DASHBOARD_USERNAME=admin
WAHA_DASHBOARD_PASSWORD=<strong-random-password>
WAHA_API_KEY=

# Your WhatsApp ID
# Format: country code + number (no + or spaces) + @c.us
# Example for Israeli number 050-123-4567: 972501234567@c.us
MY_WHATSAPP_ID=<your-number>@c.us

# Cloudflare Tunnel — paste the token from Step 12
TUNNEL_TOKEN=eyJhIjoiNjk2ZD...
```

---

### Start the Full Production Stack

**Step 14 — Launch all 5 services**
```bash
docker compose --profile prod up -d --build
```

**Step 15 — Verify all containers are healthy**
```bash
docker compose --profile prod ps
```
All 5 services should show `healthy` or `running`:
- `pa-postgres`
- `pa-ollama`
- `pa-backend`
- `pa-waha`
- `pa-cloudflared`

**Step 16 — Pull the model** (if not already done in Part 1)
```bash
docker exec pa-ollama ollama pull gemma3:4b-it-qat
```

---

### Connect WhatsApp

**Step 17 — Open WAHA dashboard**

Go to: `http://localhost:3000`
Login: `admin` / your `WAHA_DASHBOARD_PASSWORD`

**Step 18 — Start a session and scan QR**
1. Click **Start New Session** → name it `default` → Start
2. A QR code appears
3. On your phone: WhatsApp → **Linked Devices → Link a Device**
4. Scan the QR code
5. Wait for status to show **WORKING**

---

### Set the Webhook

**Step 19 — Tell WAHA to forward messages to your backend**
```bash
curl -X PUT "http://localhost:3000/api/sessions/default" \
  -H "Content-Type: application/json" \
  -d "{\"config\": {\"webhooks\": [{\"url\": \"http://backend:8000/webhook/waha\", \"events\": [\"message\"]}]}}"
```

---

### End-to-End Test

**Step 20 — Test from WhatsApp**

1. Open **"Message Yourself"** in WhatsApp (search your own name)
2. Type: `What can you help me with?`
3. You should get a reply within 15-30 seconds

**Step 21 — Test group trigger**
1. In any WhatsApp group, type: `@bot What time is it in Tokyo?`
2. Bot should respond
3. A message without `@bot` prefix → silently ignored

**Step 22 — Test bug report formatting**

Send yourself:
```
The checkout page shows a blank screen after payment. It should redirect to the confirmation page. Steps: 1. go to cart 2. click pay 3. enter card details 4. click confirm.
```
Expected: response formatted as the Octane bug template.

---

## Part 3: Survive Windows Reboots

**Step 23 — Auto-start Docker Desktop on login**

Docker Desktop → Settings → General → **"Start Docker Desktop when you sign in"** → ON

> All containers have `restart: always` — they come back automatically when Docker starts.

**Step 24 — Auto-login Windows** (optional, for unattended restarts)
```powershell
# PowerShell (Admin)
netplwiz
# Uncheck "Users must enter a user name and password" → Apply → enter your password
```

**Step 25 — Limit forced Windows Update restarts** (optional)
```powershell
# PowerShell (Admin)
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" -Name "ActiveHoursStart" -Value 0
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" -Name "ActiveHoursEnd" -Value 23
```

**Step 26 — Verify recovery after reboot**
1. Restart your PC
2. Wait ~2-3 min for Windows + Docker to fully start
3. Open terminal:
```bash
docker compose --profile prod ps
```
4. All 5 services should be healthy
5. Send yourself a WhatsApp message — bot should respond

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start dev (no WhatsApp) | `docker compose up -d --build` |
| Start production (all) | `docker compose --profile prod up -d --build` |
| Stop dev | `docker compose down` |
| Stop production | `docker compose --profile prod down` |
| View all logs | `docker compose --profile prod logs -f` |
| View single service log | `docker compose logs -f backend` |
| Rebuild backend only | `docker compose up -d --build backend` |
| Check container status | `docker compose --profile prod ps` |
| Pull model | `docker exec pa-ollama ollama pull gemma3:4b-it-qat` |
| Check VRAM usage | `nvidia-smi` |
| Test pipeline (dev) | `curl -X POST http://localhost:8000/test -H "Content-Type: application/json" -d "{\"text\": \"hello\"}"` |
| Open API docs | `http://localhost:8000/docs` |
| Open WAHA dashboard | `http://localhost:3000` |

---

## Routing Rules (How Messages Are Handled)

| Source | Condition | Action |
|--------|-----------|--------|
| **Self-chat** ("Message Yourself") | Any message | → LangGraph pipeline → reply |
| **Group chat** | Message starts with `@bot` or `!bot` | → LangGraph pipeline → reply |
| **Group chat** | No trigger prefix | Ignored (no reply) |
| **DM from another user** | Any message | Ignored (no reply) |
