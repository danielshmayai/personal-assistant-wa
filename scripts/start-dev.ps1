# start-dev.ps1 — One-command local dev startup.
# Starts postgres + ollama + backend, pulls model if needed, opens API docs.
# Run from project root: .\scripts\start-dev.ps1

$ErrorActionPreference = "SilentlyContinue"

$projectRoot  = Join-Path $PSScriptRoot ".."
$envPath      = Join-Path $projectRoot ".env"
$backendUrl   = "http://localhost:8000"
$ollamaUrl    = "http://localhost:11434"
$model        = "gemma3:4b-it-qat"
$maxWaitSecs  = 120

function Header($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)     { Write-Host "  [OK]  $msg" -ForegroundColor Green }
function Fail($msg)   { Write-Host "  [FAIL] $msg" -ForegroundColor Red }
function Info($msg)   { Write-Host "    $msg" -ForegroundColor DarkGray }
function Step($msg)   { Write-Host "  >> $msg" -ForegroundColor White }

Write-Host "`n=====================================" -ForegroundColor Cyan
Write-Host "  Personal Assistant — Dev Startup   " -ForegroundColor Cyan
Write-Host "=====================================`n" -ForegroundColor Cyan

# ── Check .env ────────────────────────────────────────────────────────────────
Header "Environment"
if (-not (Test-Path $envPath)) {
    Fail ".env not found. Run first: .\scripts\setup-env.ps1"
    exit 1
}
$envContent = Get-Content $envPath -Raw
if ($envContent -notmatch "POSTGRES_PASSWORD=(?!changeme|^$)\S+") {
    Fail "POSTGRES_PASSWORD is not set in .env. Run: .\scripts\setup-env.ps1"
    exit 1
}
Ok ".env is present and has POSTGRES_PASSWORD set"

# ── Check Docker ──────────────────────────────────────────────────────────────
Header "Docker"
docker info > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail "Docker is not running. Start Docker Desktop and try again."
    exit 1
}
Ok "Docker is running"

# ── Start containers ──────────────────────────────────────────────────────────
Header "Starting containers (postgres + ollama + backend)"
Set-Location $projectRoot
docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Fail "docker compose up failed. Check logs: docker compose logs"
    exit 1
}
Ok "Containers started"

# ── Wait for backend health ───────────────────────────────────────────────────
Header "Waiting for backend to become healthy"
Step "This may take up to $maxWaitSecs seconds (Ollama starts slowly)..."
$elapsed = 0
$healthy = $false
while ($elapsed -lt $maxWaitSecs) {
    Start-Sleep -Seconds 5
    $elapsed += 5
    try {
        $resp = Invoke-RestMethod -Uri "$backendUrl/health" -TimeoutSec 3
        if ($resp.checks.ollama -eq "ok") {
            $healthy = $true
            break
        }
        Write-Host "    [$elapsed`s] ollama=$($resp.checks.ollama) postgres=$($resp.checks.postgres)" -ForegroundColor DarkGray
    } catch {
        Write-Host "    [$elapsed`s] backend not yet responding..." -ForegroundColor DarkGray
    }
}

if (-not $healthy) {
    Fail "Backend did not become healthy in $maxWaitSecs seconds."
    Info "Check logs: docker compose logs -f"
    exit 1
}
Ok "Backend is healthy"

# ── Pull model if missing ─────────────────────────────────────────────────────
Header "Checking model: $model"
$modelList = docker exec pa-ollama ollama list 2>&1
if ($modelList -match [regex]::Escape($model.Split(":")[0])) {
    Ok "Model already present — skipping download"
} else {
    Step "Pulling $model (~2.5GB, this may take several minutes)..."
    docker exec pa-ollama ollama pull $model
    if ($LASTEXITCODE -ne 0) {
        Fail "Model pull failed. Check your internet connection and try again."
        exit 1
    }
    Ok "Model pulled successfully"
}

# ── Smoke test ────────────────────────────────────────────────────────────────
Header "Smoke test (quick LLM call)"
Step "Sending test message to pipeline..."
try {
    $body = '{"text":"Say hi in one sentence"}'
    $resp = Invoke-RestMethod -Uri "$backendUrl/test" -Method POST `
            -ContentType "application/json" -Body $body -TimeoutSec 60
    Ok "Pipeline responded: $($resp.reply.Substring(0, [Math]::Min(80, $resp.reply.Length)))..."
} catch {
    Fail "Smoke test failed: $_"
    Info "The stack is still running — model may need more time to load."
}

# ── VRAM check ────────────────────────────────────────────────────────────────
Header "VRAM Usage"
$smi = nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>&1
if ($LASTEXITCODE -eq 0) {
    Ok "VRAM: $($smi.Trim())"
} else {
    Info "Could not read VRAM (nvidia-smi not found)."
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host "`n=====================================" -ForegroundColor Green
Write-Host "  Dev stack is running!" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Green
Write-Host "  API docs : $backendUrl/docs" -ForegroundColor White
Write-Host "  Health   : $backendUrl/health" -ForegroundColor White
Write-Host "  Test     : POST $backendUrl/test  { `"text`": `"...`" }" -ForegroundColor White
Write-Host "  Stop     : docker compose down`n" -ForegroundColor White

# Open API docs in default browser
Start-Process "$backendUrl/docs"
