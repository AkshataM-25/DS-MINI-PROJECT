param(
    [string]$ApiUrl = "http://localhost:8000",
    [string]$DatasetId = "demo",
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PreviousSkew = $env:WORKER_CLOCK_SKEW_SECONDS
Push-Location $ProjectRoot
try {
    $env:WORKER_CLOCK_SKEW_SECONDS = "-3600"
    docker compose up -d --force-recreate --scale worker=3 api worker
    if ($LASTEXITCODE -ne 0) { throw "Could not recreate API and workers with simulated skew." }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $system = Invoke-RestMethod -Uri "$ApiUrl/api/system" -TimeoutSec 5
            if ($system.task_consumers -ge 3) { break }
        }
        catch {
            Write-Host "Waiting for API and worker services to recover after recreation..."
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    if (-not $system -or $system.task_consumers -lt 3) { throw "Three workers did not register before timeout." }

    $body = @{
        dataset_id = $DatasetId
        threshold = 0.1
        block_size = 4
    } | ConvertTo-Json
    $job = Invoke-RestMethod -Method Post -Uri "$ApiUrl/api/jobs" -ContentType "application/json" -Body $body
    $jobDeadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $status = Invoke-RestMethod -Uri "$ApiUrl/api/jobs/$($job.job_id)"
        if ($status.status -in @("completed", "completed_with_failures")) { break }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $jobDeadline)
    if ($status.status -ne "completed") { throw "Skew demonstration job did not complete successfully." }

    $wall = Invoke-RestMethod -Uri "$ApiUrl/api/jobs/$($job.job_id)/timeline?order=wall"
    $lamport = Invoke-RestMethod -Uri "$ApiUrl/api/jobs/$($job.job_id)/timeline?order=lamport"
    if (-not $wall.simulated_clock_skew) { throw "API did not label the simulated worker clock skew." }
    $received = $wall.events | Where-Object { $_.event -eq "task_received" } | Select-Object -First 1
    if (-not $received) { throw "No worker receive event was recorded." }
    $sentWall = $wall.events | Where-Object {
        $_.event -eq "task_published" -and $_.task_id -eq $received.task_id
    } | Select-Object -First 1
    $sentLamport = $lamport.events | Where-Object {
        $_.event -eq "task_published" -and $_.task_id -eq $received.task_id
    } | Select-Object -First 1
    $receivedLamport = $lamport.events | Where-Object {
        $_.event -eq "task_received" -and $_.task_id -eq $received.task_id
    } | Select-Object -First 1
    if (-not $sentWall -or -not $sentLamport -or -not $receivedLamport) {
        throw "Timeline is missing a matching publish/receive event pair."
    }
    if ($received.wall_time_reported -ge $sentWall.wall_time_reported) {
        throw "The configured skew did not invert this pair's reported wall timestamps."
    }
    if ($sentLamport.lc -ge $receivedLamport.lc) {
        throw "Lamport order did not preserve send-before-receive causality."
    }
    Write-Host "PASS: simulated wall timestamps invert the event pair; Lamport ordering preserves causality. Open Timeline to view both lists."
}
finally {
    if ($null -eq $PreviousSkew) {
        Remove-Item Env:WORKER_CLOCK_SKEW_SECONDS -ErrorAction SilentlyContinue
    } else {
        $env:WORKER_CLOCK_SKEW_SECONDS = $PreviousSkew
    }
    Pop-Location
}
