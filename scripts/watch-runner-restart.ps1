# watch-runner-restart.ps1
# Polls for a trigger file written by the Docker backend and restarts the GitHub Actions runner.
# Registered as a Windows startup task by setup-runner-watcher.ps1.

$triggerFile = "D:\Claude\pa\restart-triggers\runner.trigger"
$taskName    = "GitHubActionsRunner-PA"
$logFile     = "D:\Claude\pa\restart-triggers\watcher.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
}

Log "Runner restart watcher started (polling every 10s for $triggerFile)"

while ($true) {
    if (Test-Path $triggerFile) {
        Remove-Item $triggerFile -Force -ErrorAction SilentlyContinue
        Log "Trigger detected — restarting $taskName ..."
        Stop-ScheduledTask  -TaskName $taskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 5
        Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Log "Runner restarted."
    }
    Start-Sleep -Seconds 10
}
