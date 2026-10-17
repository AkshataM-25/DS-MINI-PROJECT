param(
    [Parameter(Mandatory = $true)][string]$JobId,
    [string]$ApiUrl = "http://localhost:8000",
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $job = Invoke-RestMethod -Uri "$ApiUrl/api/jobs/$JobId"
        $system = Invoke-RestMethod -Uri "$ApiUrl/api/system"
        $eligible = $job.status -eq "processing" -and
            $system.demo_controls -and
            $system.task_consumers -ge 2 -and
            $system.queues.tasks.unacknowledged -ge 1 -and
            (@($system.workers | Where-Object { $_.healthy -and $_.current_task }).Count -ge 1)
        if ($eligible) { break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    if (-not $eligible) {
        throw "No safe kill window. Enable DEMO_CONTROLS, scale to two workers, and use a job with active unacknowledged work."
    }

    $before = [int]$system.queues.tasks.redeliveries
    $consumerCount = [int]$system.task_consumers
    $body = @{ job_id = $JobId } | ConvertTo-Json
    $kill = Invoke-RestMethod -Method Post -Uri "$ApiUrl/api/system/kill-random-worker" -ContentType "application/json" -Body $body
    Write-Host "SIMULATED control sent to $($kill.target_worker); awaiting broker redelivery and completion."

    do {
        $job = Invoke-RestMethod -Uri "$ApiUrl/api/jobs/$JobId"
        $system = Invoke-RestMethod -Uri "$ApiUrl/api/system"
        Write-Host "$($job.status): $($job.tasks_done)/$($job.total_tasks) tasks; redeliveries=$($system.queues.tasks.redeliveries)"
        if ($job.status -in @("completed", "completed_with_failures")) { break }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    $redeliveryObserved = [int]$system.queues.tasks.redeliveries -gt $before
    $consumersRecovered = [int]$system.task_consumers -ge $consumerCount
    if (-not $redeliveryObserved -or -not $consumersRecovered) {
        throw "RabbitMQ redelivery and consumer recovery were not both observed."
    }
    if ($job.status -ne "completed" -or -not $job.integrity.unique_task_ids -or -not $job.integrity.pair_count) {
        throw "The job did not finish with both integrity checks true."
    }
    $verification = Invoke-RestMethod -Method Post -Uri "$ApiUrl/api/jobs/$JobId/verify"
    if (-not $verification.passed) { throw "Baseline verification failed after worker interruption." }
    Write-Host "Verification: PASS; redeliveries before=$before, after=$($system.queues.tasks.redeliveries)."
}
finally {
    Pop-Location
}
