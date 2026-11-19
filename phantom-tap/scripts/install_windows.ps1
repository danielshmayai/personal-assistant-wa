<#
.SYNOPSIS
    Run phantom-tap in the background on this Windows PC, from logon onwards.

.DESCRIPTION
    Registers a scheduled task that starts `pt daemon` when you log on, restarts it
    if it ever exits, and writes its log to data\phantom-tap.log. The phone does not
    need to be near this PC: the booking goes straight from here to the Holmes Place
    server over the internet.

    Re-run to update; run with -Uninstall to remove.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
#>
[CmdletBinding()]
param(
    [string]$RepoDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$TaskName = "phantom-tap"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task '$TaskName'."
    return
}

$pt = Join-Path $RepoDir ".venv\Scripts\pt.exe"
if (-not (Test-Path $pt)) {
    throw "pt.exe not found at $pt. In $RepoDir run first:`n  py -m venv .venv`n  .venv\Scripts\pip install -e ."
}
$config = Join-Path $RepoDir "config\booking.toml"
if (-not (Test-Path $config)) {
    throw "Missing $config. Copy config\booking.example.toml to it and edit it."
}

$log = Join-Path $RepoDir "data\phantom-tap.log"
$action = New-ScheduledTaskAction -Execute $pt `
    -Argument "-v --log-file `"$log`" daemon" -WorkingDirectory $RepoDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description "Books Holmes Place classes the moment registration opens." | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed and started '$TaskName'."
Write-Host "Log: $log"
Write-Host ""
Write-Host "A sleeping PC cannot book. phantom-tap keeps the PC awake during a race,"
Write-Host "but it cannot wake one that is already asleep. If this PC sleeps on its own,"
Write-Host "stop it sleeping while plugged in (your choice - this script does not change it):"
Write-Host "    powercfg /change standby-timeout-ac 0"
