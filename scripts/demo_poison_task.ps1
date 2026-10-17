param(
    [string]$ApiUrl = "http://localhost:8000",
    [string]$DatasetId = "demo",
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$before = Invoke-RestMethod -Uri "$ApiUrl/api/system"
$failedBefore = [int]$before.queues.failed_tasks.ready
$body = @{
    dataset_id = $DatasetId
    threshold = 0.1
    block_size = 4
    inject_poison = $true
} | ConvertTo-Json
$job = Invoke-RestMethod -Method Post -Uri "$ApiUrl/api/jobs" -ContentType "application/json" -Body $body
Write-Host "SIMULATED poison task submitted as part of job $($job.job_id)."

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    $status = Invoke-RestMethod -Uri "$ApiUrl/api/jobs/$($job.job_id)"
    $system = Invoke-RestMethod -Uri "$ApiUrl/api/system"
    Write-Host "$($status.status): retries should end in one failed task; failed queue ready=$($system.queues.failed_tasks.ready)"
    if ($status.status -in @("completed", "completed_with_failures")) { break }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)

if ($status.status -ne "completed_with_failures" -or $status.failed_tasks -lt 1) {
    throw "Poison task did not reach completed_with_failures."
}
if (-not $status.integrity.unique_task_ids) {
    throw "The terminal poison result did not count toward unique task completion."
}
if (-not $status.integrity.pair_count -or $status.pairs_compared -ne $status.expected_pairs) {
    throw "The poison task must not affect pair coverage from the normal comparison tasks."
}
if ($status.reported_retry_events -lt 3) {
    throw "Expected at least three reported retry events."
}
if ([int]$system.queues.failed_tasks.ready -le $failedBefore) {
    $queueDeadline = (Get-Date).AddSeconds([Math]::Min($TimeoutSeconds, 30))
    do {
        Start-Sleep -Seconds 1
        $system = Invoke-RestMethod -Uri "$ApiUrl/api/system"
    } while (
        [int]$system.queues.failed_tasks.ready -le $failedBefore -and
        (Get-Date) -lt $queueDeadline
    )
}
if ([int]$system.queues.failed_tasks.ready -le $failedBefore) {
    throw "No new terminal failure appeared in failed_tasks."
}
Write-Host "PASS: terminal failure was recorded after retries; the extra failed task did not change valid pair coverage."
