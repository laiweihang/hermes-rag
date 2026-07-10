# One-shot dev: FastAPI + Next.js (Windows PowerShell)
# Run from project root OR from scripts/:
#   .\scripts\start-dev.ps1
# Stop: Ctrl+C
#
# Custom ports:
#   $env:API_PORT = "9000"; $env:FRONTEND_PORT = "4000"; .\scripts\start-dev.ps1
#
# Note: This file uses ASCII-only strings so PowerShell 5.x (default ANSI encoding)
# does not misp-parse UTF-8 multibyte characters.

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $PSScriptRoot
Set-Location $ROOT

$venvPython = Join-Path $ROOT ".venv\Scripts\python.exe"
$venvUvicorn = Join-Path $ROOT ".venv\Scripts\uvicorn.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Missing .venv. Run: python -m venv .venv ; .\.venv\Scripts\pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

$API_PORT = if ($env:API_PORT) { $env:API_PORT } else { "8000" }
$FRONTEND_PORT = if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { "3000" }

$script:apiProc = $null
$script:feProc = $null

function Stop-Children {
    if ($script:apiProc -and -not $script:apiProc.HasExited) {
        Stop-Process -Id $script:apiProc.Id -Force -ErrorAction SilentlyContinue
    }
    if ($script:feProc -and -not $script:feProc.HasExited) {
        Stop-Process -Id $script:feProc.Id -Force -ErrorAction SilentlyContinue
    }
}

try {
    if (Test-Path $venvUvicorn) {
        $apiExe = $venvUvicorn
        $apiArgs = @("api:app", "--host", "0.0.0.0", "--port", $API_PORT)
    } else {
        $apiExe = $venvPython
        $apiArgs = @("-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", $API_PORT)
    }

    $apiLog = Join-Path $ROOT "logs\api.log"
    $apiErr = Join-Path $ROOT "logs\api.err.log"
    $logsDir = Split-Path -Parent $apiLog
    if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }

    Write-Host "Starting API -> http://127.0.0.1:${API_PORT}" -ForegroundColor Cyan
    Write-Host "  API logs    -> $apiLog" -ForegroundColor DarkGray
    Write-Host "  API stderr  -> $apiErr" -ForegroundColor DarkGray
    $script:apiProc = Start-Process -FilePath $apiExe -ArgumentList $apiArgs `
        -WorkingDirectory $ROOT -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $apiLog -RedirectStandardError $apiErr

    Write-Host "Starting frontend -> http://localhost:${FRONTEND_PORT}" -ForegroundColor Cyan
    $script:feProc = Start-Process -FilePath "npm" -ArgumentList @("run", "dev", "--", "--port", $FRONTEND_PORT) -WorkingDirectory (Join-Path $ROOT "frontend") -PassThru -NoNewWindow

    Write-Host "Press Ctrl+C to stop both." -ForegroundColor Yellow
    Write-Host "Tip: tail -f api logs in another terminal:" -ForegroundColor DarkGray
    Write-Host "  Get-Content -Path '$apiLog' -Wait -Tail 30" -ForegroundColor DarkGray

    while ($true) {
        if ($script:apiProc.HasExited) {
            Write-Host "API exited with code $($script:apiProc.ExitCode)" -ForegroundColor Red
            break
        }
        if ($script:feProc.HasExited) {
            Write-Host "Frontend exited with code $($script:feProc.ExitCode)" -ForegroundColor Red
            break
        }
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Host ""
    Write-Host "Stopping services..."
    Stop-Children
}
