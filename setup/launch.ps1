# launch.ps1 - Start Savvy on Windows (the equivalent of launch.sh)
#
# Ollama, Signal bot, and Scheduler run hidden in the background.
# Only the Secretary REPL runs in this window.
#
#   powershell -ExecutionPolicy Bypass -File setup\launch.ps1

$Dir    = Split-Path $PSScriptRoot -Parent
$Src    = Join-Path $Dir "src"
$Py     = Join-Path $Dir ".venv\Scripts\python.exe"
$LogDir = Join-Path $Dir "logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$env:PYTHONUTF8 = "1"

if (-not (Test-Path $Py)) {
    Write-Host "[!] No .venv found - run setup\setup.ps1 first."
    exit 1
}

# The tray app (src\desktop.py) supervises the bot and scheduler itself.
$desktop = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
           Where-Object { $_.CommandLine -match 'desktop\.py' }
if ($desktop) {
    Write-Host "[OK] Savvy desktop is running and managing the background services."
    Set-Location $Src
    & $Py secretary.py
    exit
}

# A previous launch's bot/scheduler would keep running alongside the new ones.
& (Join-Path $PSScriptRoot "stop.ps1") | Out-Null

Write-Host "Starting background services..."

# Ollama (skip if already running - the Windows installer usually autostarts it)
try {
    Invoke-RestMethod http://localhost:11434/api/tags -TimeoutSec 2 | Out-Null
    Write-Host "[OK] Ollama already running"
} catch {
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden `
        -RedirectStandardOutput "$LogDir\ollama.log" -RedirectStandardError "$LogDir\ollama.err.log"
    Write-Host "[OK] Ollama started"
    Start-Sleep 2
}

# Start-Process needs separate files for stdout and stderr.
function Start-Background($name, $argList) {
    Start-Process $Py -ArgumentList $argList -WorkingDirectory $Src -WindowStyle Hidden `
        -RedirectStandardOutput "$LogDir\$name.log" -RedirectStandardError "$LogDir\$name.err.log"
    Write-Host "[OK] $name started (logs: logs\$name.log, logs\$name.err.log)"
}

Start-Background "signal"    @("-u", "secretary.py", "--signal")
Start-Background "scheduler" @("-u", "scheduler.py")

Start-Sleep 1
Write-Host ""
Write-Host "Opening Secretary..."
Write-Host "  Logs: Get-Content -Wait $LogDir\signal.err.log"
Write-Host "        Get-Content -Wait $LogDir\scheduler.err.log"
Write-Host "  Stop background services: setup\stop.ps1"
Write-Host ""

# Secretary REPL - foreground
Set-Location $Src
& $Py secretary.py
