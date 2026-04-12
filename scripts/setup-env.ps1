# setup-env.ps1 — Interactive wizard to create your .env file.
# Run from project root: .\scripts\setup-env.ps1

$ErrorActionPreference = "Stop"

$projectRoot = Join-Path $PSScriptRoot ".."
$envPath     = Join-Path $projectRoot ".env"
$examplePath = Join-Path $projectRoot ".env.example"

function Header($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Info($msg)   { Write-Host "    $msg" -ForegroundColor DarkGray }
function Ok($msg)     { Write-Host "  [OK]  $msg" -ForegroundColor Green }
function Warn($msg)   { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }

function Prompt-Value {
    param([string]$Label, [string]$Description, [string]$Default = "", [bool]$Secret = $false)
    Write-Host "`n  $Label" -ForegroundColor White
    if ($Description) { Info $Description }
    if ($Default) { Info "Default: $Default (press Enter to accept)" }
    if ($Secret) {
        $secure = Read-Host "  > " -AsSecureString
        $plain  = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                      [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
        if (-not $plain -and $Default) { return $Default }
        return $plain
    } else {
        $val = Read-Host "  > "
        if (-not $val -and $Default) { return $Default }
        return $val
    }
}

Write-Host "`n==========================================" -ForegroundColor Cyan
Write-Host "  Personal Assistant — .env Setup Wizard  " -ForegroundColor Cyan
Write-Host "==========================================`n" -ForegroundColor Cyan

if (Test-Path $envPath) {
    Warn ".env already exists at $envPath"
    $overwrite = Read-Host "  Overwrite it? (y/N)"
    if ($overwrite -notmatch "^[Yy]$") {
        Write-Host "  Aborted. Existing .env kept." -ForegroundColor Yellow
        exit 0
    }
}

$values = @{}

# ── PostgreSQL ────────────────────────────────────────────────────────────────
Header "PostgreSQL"
Info "Used for LangGraph state and the memory system."
$values["POSTGRES_USER"] = Prompt-Value `
    "Database username" "" "pa"
$values["POSTGRES_PASSWORD"] = Prompt-Value `
    "Database password (strong, not 'changeme')" `
    "Use a random string — e.g. openssl rand -base64 16" "" $true
$values["POSTGRES_DB"] = Prompt-Value `
    "Database name" "" "pa"

# ── WAHA ─────────────────────────────────────────────────────────────────────
Header "WAHA (WhatsApp)"
Info "Used to connect your WhatsApp number."
$values["WAHA_DASHBOARD_USERNAME"] = Prompt-Value `
    "WAHA dashboard username" "" "admin"
$values["WAHA_DASHBOARD_PASSWORD"] = Prompt-Value `
    "WAHA dashboard password" "" "" $true
$values["WAHA_API_KEY"] = ""

# ── WhatsApp ID ───────────────────────────────────────────────────────────────
Header "Your WhatsApp ID"
Info "This tells the bot which number is 'you' (the owner)."
Info "Format: country code + number + @c.us (no + or spaces)"
Info "Example: Israeli 050-123-4567 --> 972501234567@c.us"
do {
    $waId = Prompt-Value "Your WhatsApp ID" ""
    if ($waId -eq "" -or $waId -match "^\d+@c\.us$") { break }
    Write-Host "  Invalid format. Must match: 972XXXXXXXXX@c.us" -ForegroundColor Red
} while ($true)
$values["MY_WHATSAPP_ID"] = $waId

# ── Cloudflare Tunnel ─────────────────────────────────────────────────────────
Header "Cloudflare Tunnel Token"
Info "Get this from: one.dash.cloudflare.com > Zero Trust > Networks > Tunnels"
Info "Leave blank if you are only setting up for local dev right now."
$values["TUNNEL_TOKEN"] = Prompt-Value "Tunnel token (paste or leave blank)" ""

# ── Write .env ────────────────────────────────────────────────────────────────
$content = @"
# PostgreSQL
POSTGRES_USER=$($values["POSTGRES_USER"])
POSTGRES_PASSWORD=$($values["POSTGRES_PASSWORD"])
POSTGRES_DB=$($values["POSTGRES_DB"])

# WAHA
WAHA_DASHBOARD_USERNAME=$($values["WAHA_DASHBOARD_USERNAME"])
WAHA_DASHBOARD_PASSWORD=$($values["WAHA_DASHBOARD_PASSWORD"])
WAHA_API_KEY=$($values["WAHA_API_KEY"])

# Your WhatsApp ID (format: 972501234567@c.us)
MY_WHATSAPP_ID=$($values["MY_WHATSAPP_ID"])

# Cloudflare Tunnel
# Create at: https://one.dash.cloudflare.com/ -> Zero Trust -> Networks -> Tunnels
# Configure public hostname to route to http://backend:8000
TUNNEL_TOKEN=$($values["TUNNEL_TOKEN"])
"@

Set-Content -Path $envPath -Value $content -Encoding UTF8
Ok ".env written to $envPath"

# ── Validate ──────────────────────────────────────────────────────────────────
Write-Host ""
if (-not $values["POSTGRES_PASSWORD"]) {
    Warn "POSTGRES_PASSWORD is empty — you must set this before starting."
}
if (-not $values["MY_WHATSAPP_ID"]) {
    Warn "MY_WHATSAPP_ID is empty — required before going to production."
}
if (-not $values["TUNNEL_TOKEN"]) {
    Warn "TUNNEL_TOKEN is empty — required for production. Fine for local dev."
}

Write-Host "`n  Next step: .\scripts\start-dev.ps1" -ForegroundColor Green
Write-Host ""
