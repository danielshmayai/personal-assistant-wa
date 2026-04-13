# Deployment Setup — GitHub Actions Self-Hosted Runner

## Overview

Push to `main` → GitHub triggers the `deploy.yml` workflow → self-hosted runner on this Windows PC pulls the code and rebuilds the `backend` Docker container.

No ports exposed. No webhooks. The runner polls GitHub and executes locally.

---

## One-Time Runner Setup (Windows PC)

### 1. Create the runner on GitHub

1. Go to: `https://github.com/<your-org-or-user>/pa/settings/actions/runners`
2. Click **New self-hosted runner**
3. Select **Windows** as the OS — but we will use the **Linux/x64** token commands via Git Bash. Actually select **Windows** and follow the PowerShell commands for download, but run the `config.cmd` step from a normal PowerShell terminal (not Git Bash).

> Note: GitHub will show you a unique token (valid 1 hour). Keep the page open.

---

### 2. Download and configure the runner

Open **PowerShell as Administrator** and run the commands GitHub shows on the runner page. They look like:

```powershell
# Create a dedicated folder for the runner
mkdir C:\actions-runner; cd C:\actions-runner

# Download the runner package (URL and hash shown on GitHub)
Invoke-WebRequest -Uri https://github.com/actions/runner/releases/download/v2.x.x/actions-runner-win-x64-2.x.x.zip -OutFile actions-runner-win-x64.zip

# Validate hash (shown on GitHub page)
if ((Get-FileHash -Path actions-runner-win-x64.zip -Algorithm SHA256).Hash.ToUpper() -ne '<HASH>') { throw 'Hash mismatch' }

# Extract
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory("$PWD\actions-runner-win-x64.zip", "$PWD")
```

Then configure the runner:

```powershell
.\config.cmd --url https://github.com/<your-user>/pa --token <TOKEN_FROM_GITHUB>
```

When prompted:
- **Runner group**: press Enter (default)
- **Runner name**: e.g. `pa-server`
- **Additional labels**: press Enter (default, or add `pa-server`)
- **Work folder**: press Enter (default `_work`)

---

### 3. Install as a Windows service (so it survives reboots)

Still in PowerShell as Administrator, from `C:\actions-runner`:

```powershell
.\svc.cmd install
.\svc.cmd start
```

Verify it is running:

```powershell
.\svc.cmd status
# Or check Services: Win+R → services.msc → look for "GitHub Actions Runner (pa-server)"
```

The service runs as the current user by default. If Docker requires a specific user, open `services.msc`, find the runner service, go to **Log On**, and set it to run as your Windows user account (so it has Docker Desktop access).

---

### 4. Verify Git Bash is on PATH for the runner

The workflow uses `shell: bash`. Git Bash must be discoverable. Test in a new PowerShell session:

```powershell
bash --version
```

If that fails, add Git Bash to the system PATH:
- `C:\Program Files\Git\bin` — for `bash.exe`
- `C:\Program Files\Git\usr\bin` — for Unix tools

Set via: System Properties → Environment Variables → System variables → Path → Edit → New.

Restart the runner service after changing PATH:

```powershell
.\svc.cmd stop
.\svc.cmd start
```

---

### 5. Verify Docker is accessible from the runner

Open a new PowerShell window (not as Administrator — same account the runner service uses) and run:

```powershell
docker ps
docker compose version
```

Both must succeed. If Docker Desktop is set to require elevated privileges, check Docker Desktop → Settings → General → "Use the WSL 2 based engine" and ensure your user is in the `docker-users` group:

```powershell
net localgroup docker-users <your-username> /add
```

Log out and back in for group membership to take effect.

---

### 6. Add GitHub Actions secrets

Go to: `https://github.com/<your-user>/pa/settings/secrets/actions`

Add the following **Repository secrets** for WhatsApp notifications (all optional — the workflow degrades gracefully if missing):

| Secret name      | Value                                      |
|------------------|--------------------------------------------|
| `WAHA_BASE_URL`  | `http://localhost:3000` (or prod tunnel URL) |
| `WAHA_API_KEY`   | Value of `WAHA_API_KEY` from your `.env`   |
| `MY_WHATSAPP_ID` | Your WhatsApp ID (e.g. `15551234567@c.us`) |
| `WAHA_SESSION`   | `default` (or your session name)           |

> The `.env` file is never read by the workflow. Secrets are injected as environment variables at runtime.

---

### 7. Test the pipeline

Push any change to `main`:

```bash
git commit --allow-empty -m "Test CI deploy"
git push origin main
```

Then watch: `https://github.com/<your-user>/pa/actions`

The `Deploy to PA Server` workflow should appear, run, and complete green. You will receive a WhatsApp message if secrets are configured.

---

## Manual Deploy (without GitHub Actions)

If the runner is down or you need to deploy from the machine directly:

```bash
bash /d/Claude/pa/scripts/deploy.sh
```

Or for production (with WAHA + cloudflared):

```bash
cd /d/Claude/pa
docker compose --profile prod up --build -d
```

---

## Troubleshooting

**Runner shows "offline" in GitHub**
- Check the service: `C:\actions-runner\svc.cmd status`
- Restart: `C:\actions-runner\svc.cmd stop && C:\actions-runner\svc.cmd start`
- Check logs: `C:\actions-runner\_diag\`

**`bash: command not found` in workflow step**
- Git Bash is not on system PATH. See step 4 above.

**`docker: permission denied`**
- The runner service user is not in `docker-users` group. See step 5 above.

**Backend container unhealthy after deploy**
- The workflow prints the last 40 log lines on failure. Check the Actions run log.
- Manually: `docker logs pa-backend --tail 50`

**WhatsApp notification not arriving**
- Secrets not set, or WAHA container is not running (`--profile prod` only).
- The notification step is non-fatal — deploy still succeeds without it.
