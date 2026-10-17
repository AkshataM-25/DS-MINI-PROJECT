param(
    [string]$DatasetId = "",
    [string]$ApiUrl = "http://localhost:8000",
    [int]$TimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    docker compose up -d --build --scale worker=2 api worker
    if ($LASTEXITCODE -ne 0) { throw "Compose could not start the API and two workers." }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $health = Invoke-RestMethod -Uri "$ApiUrl/api/health" -TimeoutSec 5
            $system = Invoke-RestMethod -Uri "$ApiUrl/api/system" -TimeoutSec 5
            if ($health.status -eq "healthy" -and $system.task_consumers -ge 2) { break }
        }
        catch {
            Write-Host "Waiting for API, RabbitMQ, aggregator, and workers..."
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    if ($health.status -ne "healthy" -or $system.task_consumers -lt 2) {
        throw "The stack did not become ready with two task consumers."
    }

    if (-not $DatasetId) {
        $datasetBody = @{
            name = "smoke"
            documents = 10
            seed = 42
            planted_pairs = 1
            overlaps = @(0.6)
        } | ConvertTo-Json
        $dataset = Invoke-RestMethod -Method Post -Uri "$ApiUrl/api/datasets/generate" -ContentType "application/json" -Body $datasetBody
        $DatasetId = $dataset.dataset_id
    }

    $body = @{
        dataset_id = $DatasetId
        threshold = 0.1
        block_size = 4
    } | ConvertTo-Json
    $job = Invoke-RestMethod -Method Post -Uri "$ApiUrl/api/jobs" -ContentType "application/json" -Body $body
    Write-Host "Submitted smoke job $($job.job_id) ($($job.expected_pairs) expected pairs)."

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $status = Invoke-RestMethod -Uri "$ApiUrl/api/jobs/$($job.job_id)"
        Write-Host "$($status.status): $($status.tasks_done)/$($status.total_tasks) tasks."
        if ($status.status -in @("completed", "completed_with_failures")) { break }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    if ($status.status -ne "completed" -or -not $status.integrity.unique_task_ids -or -not $status.integrity.pair_count) {
        throw "Smoke job did not complete with both integrity checks true."
    }
    $verification = Invoke-RestMethod -Method Post -Uri "$ApiUrl/api/jobs/$($job.job_id)/verify"
    if (-not $verification.passed) { throw "Distributed results did not pass baseline verification." }
    Write-Host "PASS: job completion, pair/task integrity, and baseline verification."
}
finally {
    Pop-Location
}
