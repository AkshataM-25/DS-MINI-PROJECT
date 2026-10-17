param(
    [string]$ApiUrl = "http://localhost:8000",
    [string]$DatasetId = "demo",
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    try {
        docker compose up -d --scale worker=0 worker
        if ($LASTEXITCODE -ne 0) { throw "Could not scale workers to zero." }

        $zeroDeadline = (Get-Date).AddSeconds($TimeoutSeconds)
        do {
            $system = Invoke-RestMethod -Uri "$ApiUrl/api/system"
            if ($system.task_consumers -eq 0) { break }
            Start-Sleep -Seconds 1
        } while ((Get-Date) -lt $zeroDeadline)
        if ($system.task_consumers -ne 0) {
            throw "RabbitMQ still reports task consumers after scaling workers to zero."
        }
        $readyBefore = [int]$system.queues.tasks.ready

        $body = @{
            dataset_id = $DatasetId
            threshold = 0.1
            block_size = 4
        } | ConvertTo-Json
        $job = Invoke-RestMethod -Method Post -Uri "$ApiUrl/api/jobs" -ContentType "application/json" -Body $body
        Write-Host "Submitted job $($job.job_id) with $($job.submitted_tasks) durable tasks."

        $queueDeadline = (Get-Date).AddSeconds($TimeoutSeconds)
        do {
            $system = Invoke-RestMethod -Uri "$ApiUrl/api/system"
            $ready = [int]$system.queues.tasks.ready
            Write-Host "Queue ready=$ready; consumers=$($system.task_consumers)"
            if ($ready -ge ($readyBefore + $job.submitted_tasks)) { break }
            Start-Sleep -Seconds 1
        } while ((Get-Date) -lt $queueDeadline)
        if ($ready -lt ($readyBefore + $job.submitted_tasks)) {
            throw "The task queue did not reach the expected queued count before timeout."
        }
    }
    finally {
        docker compose up -d --scale worker=3 worker
        if ($LASTEXITCODE -ne 0) { Write-Warning "Could not restore three workers." }
    }

    $jobDeadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $status = Invoke-RestMethod -Uri "$ApiUrl/api/jobs/$($job.job_id)"
        Write-Host "$($status.status): $($status.tasks_done)/$($status.total_tasks) tasks; $($status.pairs_compared)/$($status.expected_pairs) pairs."
        if ($status.status -in @("completed", "completed_with_failures")) { break }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $jobDeadline)
    if ($status.status -ne "completed" -or -not $status.integrity.unique_task_ids -or -not $status.integrity.pair_count) {
        throw "Queue drain did not finish with both integrity checks true."
    }
    Write-Host "PASS: queued tasks drained and both integrity checks passed."
}
finally {
    Pop-Location
}
