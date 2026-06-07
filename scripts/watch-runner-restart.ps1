# watch-runner-restart.ps1
# Polls for a trigger file written by the Docker backend and restarts the GitHub Actions runner.
# Sends a WhatsApp confirmation when done. Registered by setup-runner-watcher.ps1.

$triggerFile = "D:\Claude\pa\restart-triggers\runner.trigger"
$taskName    = "GitHubActionsRunner-PA"
$logFile     = "D:\Claude\pa\restart-triggers\watcher.log"
$envFile     = "D:\Claude\pa\.env"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
}

function Read-Env {
    $vars = @{}
    if (-not (Test-Path $envFile)) { return $vars }
    Get-Content $envFile | ForEach-Object {
        if ($_ -match "^([^#=\s][^=]*)=(.*)$") {
            $vars[$matches[1].Trim()] = $matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $vars
}

function Send-WhatsApp($text) {
    try {
        $env = Read-Env
        $chatId  = $env["MY_WHATSAPP_ID"]
        $apiKey  = $env["WAHA_API_KEY"]
        if (-not $chatId) { return }
        $body = @{ chatId = $chatId; text = $text; session = "default" } | ConvertTo-Json
        $headers = @{ "Content-Type" = "application/json" }
        if ($apiKey) { $headers["X-Api-Key"] = $apiKey }
        Invoke-RestMethod -Uri "http://localhost:3000/api/sendText" -Method Post -Body $body -Headers $headers -ErrorAction Stop | Out-Null
    } catch {
        Log "WhatsApp notify failed: $_"
    }
}

Log "Runner restart watcher started (polling every 10s)"

while ($true) {
    if (Test-Path $triggerFile) {
        Remove-Item $triggerFile -Force -ErrorAction SilentlyContinue
        Log "Trigger detected -- restarting $taskName ..."
        Stop-ScheduledTask  -TaskName $taskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 5
        Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Log "Runner restarted."
        Send-WhatsApp "[ *danidin* ] Runner restarted successfully"
    }
    Start-Sleep -Seconds 10
}
