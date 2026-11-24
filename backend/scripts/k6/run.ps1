param(
    [ValidateSet(1, 5, 10)][int]$Vus = 1,
    [switch]$Smoke,
    [switch]$Readiness,
    [string]$Python = ".\.venv\Scripts\python.exe",
    [ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$RunName = 'baseline'
)
$ErrorActionPreference = 'Stop'
$backend = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$fixture = Join-Path $backend 'local/k6'
$results = Join-Path $fixture 'results'
New-Item -ItemType Directory -Force $results | Out-Null
Push-Location $backend
try {
    & $Python -m scripts.health_k6_fixture tokens
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic token refresh failed.' }
    if (Test-Path (Join-Path $results ($RunName + '.json'))) { throw 'RunName already exists; use a new result name.' }
    $mode = if ($Smoke) { 'smoke' } else { 'load' }
    docker run --rm `
        -v "${PSScriptRoot}:/scripts:ro" `
        -v "${fixture}/fixture.json:/fixture/fixture.json:ro" `
        -v "${results}:/results" `
        -e "READINESS=$Readiness" -e "MODE=$mode" -e "VUS=$Vus" -e "RUN_NAME=$RunName" `
        grafana/k6:2.3.0 run --quiet /scripts/health-rest.js
    if ($LASTEXITCODE -ne 0) { throw 'k6 did not pass. Check preflight and result metrics before increasing load.' }
} finally {
    Pop-Location
}
