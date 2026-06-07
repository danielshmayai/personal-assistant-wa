# restart-runner.ps1
# Manually restart the GitHub Actions self-hosted runner.
# Run from any PowerShell window — no Administrator required.

$taskName = "GitHubActionsRunner-PA"

Write-Host "Stopping $taskName..."
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

Write-Host "Starting $taskName..."
Start-ScheduledTask -TaskName $taskName

Start-Sleep -Seconds 2
$state = (Get-ScheduledTask -TaskName $taskName).State
Write-Host "Done. State: $state"
