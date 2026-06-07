# setup-runner-watcher.ps1
# Run ONCE as Administrator to register the watcher as a Windows startup task.
# After registration the watcher starts automatically on every login/boot.

$taskName   = "PA-RunnerRestartWatcher"
$scriptPath = "D:\Claude\pa\scripts\watch-runner-restart.ps1"

# Remove old task if it exists
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action  = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 99 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $taskName `
    -Action   $action `
    -Trigger  $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Description "Watches for Docker-triggered runner restart requests" | Out-Null

Start-ScheduledTask -TaskName $taskName
Write-Host "Task '$taskName' registered and started."
