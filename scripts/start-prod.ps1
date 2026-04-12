# start-prod.ps1 - One-command production startup.
# Starts all 5 services, registers the WAHA webhook automatically.
# Run from project root: .\scripts\start-prod.ps1

$ErrorActionPreference = "SilentlyContinue"

$projectRoot   = Join-Path $PSScriptRoot ".."
$envPath       = Join-Path $projectRoot ".env"
$backendUrl    = "http://localhost:8000"
$wahaUrl       = "http://localhost:3000"
$model         = "gemma3:4b-it-qat"
$wahaSession   = "default"
$maxWaitSecs   = 180

function Header($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)     { Write-Host "  [OK]  $msg" -ForegroundColor Green }
function Fail($msg)   { Write-Host "  [FAIL] $msg" -ForegroundColor Red }
function Warn($msg)   { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Info($msg)   { Write-Host "    $msg" -ForegroundColor DarkGray }
function Step($msg)   { Write-Host "  >> $msg" -ForegroundColor White }

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Personal Assistant - Production Start" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# ── Validate .env completeness ────────────────────────────────────────────────
Header "Validating .env for production"
if (-not (Test-Path $envPath)) {
    Fail ".env not found. Run: .\scripts\setup-env.ps1"
    exit 1
}
$envVars = @{}
Get-Content $envPath | Where-Object { $_ -match "^\s*\w+=.+" } | ForEach-Object {
    $parts = $_ -split "=", 2
    $envVars[$parts[0].Trim()] = $parts[1].Trim()
}

$required = @{
    "POSTGRES_PASSWORD"       = "Database password"
    "WAHA_DASHBOARD_PASSWORD" = "WAHA dashboard password"
    "MY_WHATSAPP_ID"          = "Your WhatsApp ID"
    "TUNNEL_TOKEN"            = "Cloudflare tunnel token"
}
$missingCount = 0
foreach ($key in $required.Keys) {
    $val = $envVars[$key]
    if (-not $val -or $val -eq "changeme") {
        Fail "$key is not set - $($required[$key])"
        $missingCount++
    } else {
        Ok "$key is set"
    }
}
if ($missingCount -gt 0) {
    Write-Host "`n  Fix the missing values in .env (run .\scripts\setup-env.ps1) and retry.`n" -ForegroundColor Red
    exit 1
}

# ── Check Docker ──────────────────────────────────────────────────────────────
Header "Docker"
docker info > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail "Docker is not running. Start Docker Desktop and try again."
    exit 1
}
Ok "Docker is running"

# ── Start all 5 containers ───────────────────────────────────────────────────
Header "Starting all services (postgres + ollama + backend + waha + cloudflared)"
Set-Location $projectRoot
docker compose --profile prod up -d --build
if ($LASTEXITCODE -ne 0) {
    Fail "docker compose up failed. Check: docker compose --profile prod logs"
    exit 1
}
Ok "Containers started"

# ── Wait for backend ──────────────────────────────────────────────────────────
Header "Waiting for backend"
$elapsed = 0
$healthy = $false
while ($elapsed -lt $maxWaitSecs) {
    Start-Sleep -Seconds 5
    $elapsed += 5
    try {
        $resp = Invoke-RestMethod -Uri "$backendUrl/health" -TimeoutSec 3
        if ($resp.checks.ollama -eq "ok") { $healthy = $true; break }
        Write-Host "    [$elapsed`s] ollama=$($resp.checks.ollama)" -ForegroundColor DarkGray
    } catch {
        Write-Host "    [$elapsed`s] backend not yet responding..." -ForegroundColor DarkGray
    }
}
if (-not $healthy) {
    Fail "Backend did not become healthy in $maxWaitSecs seconds."
    Info "Run: docker compose --profile prod logs -f backend"
    exit 1
}
Ok "Backend is healthy"

# ── Pull model if missing ─────────────────────────────────────────────────────
Header "Checking model: $model"
$modelList = docker exec pa-ollama ollama list 2>&1
if ($modelList -match [regex]::Escape($model.Split(":")[0])) {
    Ok "Model already present"
} else {
    Step "Pulling $model (~2.5GB)..."
    docker exec pa-ollama ollama pull $model
    if ($LASTEXITCODE -ne 0) {
        Fail "Model pull failed."
        exit 1
    }
    Ok "Model pulled"
}

# ── Wait for WAHA ─────────────────────────────────────────────────────────────
Header "Waiting for WAHA"
$wahaUser = $envVars["WAHA_DASHBOARD_USERNAME"]
if (-not $wahaUser) { $wahaUser = "admin" }
$wahaPass = $envVars["WAHA_DASHBOARD_PASSWORD"]
$wahaCredBytes = [System.Text.Encoding]::ASCII.GetBytes("${wahaUser}:${wahaPass}")
$wahaCredB64   = [Convert]::ToBase64String($wahaCredBytes)
$wahaHeaders   = @{ Authorization = "Basic $wahaCredB64" }

$elapsed = 0
$wahaReady = $false
while ($elapsed -lt 60) {
    Start-Sleep -Seconds 5
    $elapsed += 5
    try {
        $resp = Invoke-RestMethod -Uri "$wahaUrl/api/server/status" -Headers $wahaHeaders -TimeoutSec 3
        $wahaReady = $true; break
    } catch {
        Write-Host "    [$elapsed`s] WAHA not yet responding..." -ForegroundColor DarkGray
    }
}
if (-not $wahaReady) {
    Warn "WAHA is not responding after 60 seconds. Webhook registration will be skipped."
    Warn "Check logs: docker compose logs -f waha"
} else {
    Ok "WAHA is responding"

    # ── Register webhook ──────────────────────────────────────────────────────
    Header "Registering WAHA webhook"
    Step "Checking for existing session '$wahaSession'..."
    try {
        $sessions = Invoke-RestMethod -Uri "$wahaUrl/api/sessions" -Headers $wahaHeaders -TimeoutSec 5
        $existing = $sessions | Where-Object { $_.name -eq $wahaSession }
        if (-not $existing) {
            Step "Starting new session '$wahaSession'..."
            $body = "{`"name`": `"$wahaSession`"}"
            Invoke-RestMethod -Uri "$wahaUrl/api/sessions" -Method POST -Headers $wahaHeaders `
                -ContentType "application/json" -Body $body -TimeoutSec 10 > $null
            Ok "Session '$wahaSession' created"
        } else {
            Ok "Session '$wahaSession' already exists (status: $($existing.status))"
        }
    } catch {
        Warn "Could not check sessions: $_"
    }

    try {
        $webhookBody = @{
            config = @{
                webhooks = @(@{
                    url    = "http://backend:8000/webhook/waha"
                    events = @("message")
                })
            }
        } | ConvertTo-Json -Depth 5
        Invoke-RestMethod -Uri "$wahaUrl/api/sessions/$wahaSession" -Method PUT `
            -Headers $wahaHeaders -ContentType "application/json" -Body $webhookBody -TimeoutSec 10 > $null
        Ok "Webhook registered: http://backend:8000/webhook/waha"
    } catch {
        Warn "Could not register webhook automatically: $_"
        Info "Register manually: PUT $wahaUrl/api/sessions/$wahaSession"
    }
}

# ── Cloudflared check ─────────────────────────────────────────────────────────
Header "Cloudflare Tunnel"
$cfLogs = docker logs pa-cloudflared --tail 10 2>&1
if ($cfLogs -match "Registered tunnel connection") {
    Ok "Cloudflare tunnel is connected"
} elseif ($cfLogs -match "error") {
    Warn "Cloudflare tunnel may have an error. Check: docker logs pa-cloudflared"
} else {
    Info "Cloudflare tunnel starting (check dashboard to confirm)"
}

# ── VRAM ──────────────────────────────────────────────────────────────────────
Header "VRAM Usage"
$smi = nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>&1
if ($LASTEXITCODE -eq 0) { Ok "VRAM: $($smi.Trim())" }

# ── Container status ──────────────────────────────────────────────────────────
Header "Container Status"
docker compose --profile prod ps

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Production stack is running!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  API docs      : $backendUrl/docs" -ForegroundColor White
Write-Host "  WAHA dashboard: $wahaUrl" -ForegroundColor White
Write-Host "  Health        : $backendUrl/health" -ForegroundColor White
Write-Host ""
Write-Host "  NEXT STEPS:" -ForegroundColor Yellow
Write-Host "  1. Open $wahaUrl and scan the QR code with your phone" -ForegroundColor Yellow
Write-Host "     (WhatsApp > Settings > Linked Devices > Link a Device)" -ForegroundColor Yellow
Write-Host "  2. Confirm Cloudflare tunnel is healthy at one.dash.cloudflare.com" -ForegroundColor Yellow
Write-Host "  3. Send a message to yourself on WhatsApp to test the bot" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Stop: docker compose --profile prod down`n" -ForegroundColor White

Start-Process "$wahaUrl"
