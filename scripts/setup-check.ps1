# setup-check.ps1 — Validate all prerequisites before first run.
# Run from project root: .\scripts\setup-check.ps1

$ErrorActionPreference = "SilentlyContinue"
$pass = 0
$fail = 0

function Ok($msg)   { Write-Host "  [OK]  $msg" -ForegroundColor Green;  $script:pass++ }
function Fail($msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red;   $script:fail++ }
function Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Header($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

Write-Host "`n==========================================" -ForegroundColor Cyan
Write-Host "  Personal Assistant — Prerequisites Check" -ForegroundColor Cyan
Write-Host "==========================================`n" -ForegroundColor Cyan

# ── WSL2 ──────────────────────────────────────────────────────────────────────
Header "WSL2"
$wslOutput = wsl --version 2>&1
if ($LASTEXITCODE -eq 0 -and $wslOutput -match "WSL version") {
    $versionLine = ($wslOutput | Select-String "WSL version").ToString().Trim()
    Ok $versionLine
} else {
    Fail "WSL2 not found. Run in PowerShell (Admin): wsl --install"
}

# ── Docker Desktop ─────────────────────────────────────────────────────────────
Header "Docker Desktop"
$dockerVersion = docker version --format "{{.Server.Version}}" 2>&1
if ($LASTEXITCODE -eq 0) {
    Ok "Docker Engine $dockerVersion is running"
} else {
    Fail "Docker is not running. Start Docker Desktop and try again."
}

$dockerInfo = docker info 2>&1
if ($dockerInfo -match "WSL") {
    Ok "Docker is using the WSL2 backend"
} else {
    Warn "Could not confirm WSL2 backend. Check Docker Desktop > Settings > General."
}

# ── NVIDIA Driver ─────────────────────────────────────────────────────────────
Header "NVIDIA GPU Driver"
$smi = nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>&1
if ($LASTEXITCODE -eq 0) {
    Ok "GPU detected: $($smi.Trim())"
} else {
    Fail "nvidia-smi not found. Install the NVIDIA Game Ready or Studio driver."
}

# ── Docker GPU Passthrough ────────────────────────────────────────────────────
Header "Docker GPU Passthrough"
Write-Host "  Checking (runs a tiny GPU container, may take 30s on first run)..." -ForegroundColor DarkGray
$gpuTest = docker run --rm --gpus all --entrypoint nvidia-smi ollama/ollama --query-gpu=name --format=csv,noheader 2>&1
if ($LASTEXITCODE -eq 0) {
    Ok "GPU visible inside Docker: $($gpuTest.Trim())"
} else {
    Fail "GPU not visible inside Docker. Check NVIDIA Container Toolkit support in Docker Desktop."
    Warn "Docker Desktop 4.27+ supports GPU natively. Update Docker Desktop if needed."
}

# ── Power Plan ────────────────────────────────────────────────────────────────
Header "Power Plan"
$activePlan = powercfg /getactivescheme 2>&1
if ($activePlan -match "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c") {
    Ok "High Performance power plan is active"
} elseif ($activePlan -match "e9a42b02-d5df-448d-aa00-03f14749eb61") {
    Ok "Ultimate Performance power plan is active"
} else {
    Warn "Not on High Performance plan. Run: powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
}

$sleepTimeout = powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 2>&1
if ($sleepTimeout -match "AC Power Setting Index: 0x00000000") {
    Ok "Sleep on AC power is disabled"
} else {
    Warn "Sleep may be enabled. Run: powercfg /change standby-timeout-ac 0"
}

# ── .env File ─────────────────────────────────────────────────────────────────
Header ".env File"
$envPath = Join-Path $PSScriptRoot ".." ".env"
if (Test-Path $envPath) {
    Ok ".env file exists"
    $envContent = Get-Content $envPath -Raw
    if ($envContent -match "POSTGRES_PASSWORD=(?!changeme)\S+") {
        Ok "POSTGRES_PASSWORD is set"
    } else {
        Fail "POSTGRES_PASSWORD is missing or still set to 'changeme'"
    }
    if ($envContent -match "TUNNEL_TOKEN=\S+") {
        Ok "TUNNEL_TOKEN is set (production ready)"
    } else {
        Warn "TUNNEL_TOKEN not set — required for production, not for local dev"
    }
    if ($envContent -match "MY_WHATSAPP_ID=\S+@c\.us") {
        Ok "MY_WHATSAPP_ID is set"
    } else {
        Warn "MY_WHATSAPP_ID not set — required for production, not for local dev"
    }
} else {
    Fail ".env not found. Run: .\scripts\setup-env.ps1"
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "`n==========================================" -ForegroundColor Cyan
if ($fail -eq 0) {
    Write-Host "  All checks passed ($pass OK, 0 failures)" -ForegroundColor Green
    Write-Host "  Ready to run: .\scripts\start-dev.ps1" -ForegroundColor Green
} else {
    Write-Host "  $fail check(s) failed, $pass passed." -ForegroundColor Red
    Write-Host "  Fix the FAIL items above before continuing." -ForegroundColor Red
}
Write-Host "==========================================`n" -ForegroundColor Cyan
