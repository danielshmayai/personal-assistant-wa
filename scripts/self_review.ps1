# PA Nightly Self-Review
# Calls the self-review endpoint on the local PA backend.
# Scheduled to run at 3:00 AM daily via Windows Task Scheduler (task: PA-SelfReview).

$url   = "http://localhost:8000/admin/self-review?hours=24"
$token = (Get-Content "$PSScriptRoot\..\.env" | Select-String "^TEST_TOKEN=").ToString().Split("=",2)[1].Trim()

try {
    $response = Invoke-RestMethod -Uri $url -Method Post -Headers @{ "X-Test-Token" = $token }
    Write-Host "[PA Self-Review] $(Get-Date -Format 'yyyy-MM-dd HH:mm') - $($response.result)"
} catch {
    Write-Host "[PA Self-Review] $(Get-Date -Format 'yyyy-MM-dd HH:mm') - Backend unreachable: $_"
}
