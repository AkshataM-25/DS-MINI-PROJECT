param(
    [string]$DatasetId = "demo",
    [string]$ApiUrl = "http://localhost:8000",
    [int]$MaxWorkers = 4,
    [int]$Repeats = 3
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    python -m tools.benchmark --api-url $ApiUrl --dataset-id $DatasetId --max-workers $MaxWorkers --repeats $Repeats
    if ($LASTEXITCODE -ne 0) { throw "Benchmark tool did not complete successfully." }
}
finally {
    Pop-Location
}
