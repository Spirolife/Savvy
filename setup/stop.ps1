# stop.ps1 - Stop Savvy's background services on Windows (the equivalent of stop.sh)
#
#   powershell -ExecutionPolicy Bypass -File setup\stop.ps1

Write-Host "Stopping secretary services..."

function Stop-ByCommandLine($label, $pattern) {
    $procs = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
             Where-Object { $_.CommandLine -match $pattern }
    if ($procs) {
        $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        Write-Host "[OK] $label stopped"
    } else {
        Write-Host "[..] $label not running"
    }
}

Stop-ByCommandLine "Signal bot" "secretary\.py.*--signal"
Stop-ByCommandLine "Scheduler"  "scheduler\.py"
Write-Host "Done. (Ollama left running - quit it from the system tray if you want.)"
