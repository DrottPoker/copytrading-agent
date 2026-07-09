param(
    [switch]$KeepServices
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repoRoot "docker-compose.test.yml"
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$servicesStarted = $false

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment was not found at $python."
}

try {
    docker compose -f $composeFile up -d --wait
    if ($LASTEXITCODE -ne 0) {
        throw "Disposable integration services failed to start."
    }
    $servicesStarted = $true

    $env:APP_ENV = "test"
    $env:DATABASE_URL = (
        "postgresql+asyncpg://copyagent_test:copyagent-test-password" +
        "@127.0.0.1:55432/copyagent_test"
    )
    $env:DATABASE_URL_DIRECT = (
        "postgresql://copyagent_test:copyagent-test-password" +
        "@127.0.0.1:55432/copyagent_test"
    )
    $env:TEST_DATABASE_URL = $env:DATABASE_URL
    $env:REDIS_URL = "redis://127.0.0.1:56379/15"
    $env:TEST_REDIS_URL = $env:REDIS_URL
    $env:DASHBOARD_AUTH_ENABLED = "false"
    $env:WORKER_RUN_IN_API_PROCESS = "false"
    $env:LIVE_TRADING_ENABLED = "false"
    $env:LIVE_TRADING_COPY_ENABLED = "false"

    Push-Location (Join-Path $repoRoot "backend")
    try {
        & $python -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "Alembic migration failed."
        }
        & $python -m pytest tests -m integration -q -rxX
        if ($LASTEXITCODE -ne 0) {
            throw "Integration tests failed."
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($servicesStarted -and -not $KeepServices) {
        docker compose -f $composeFile down -v
    }
}
