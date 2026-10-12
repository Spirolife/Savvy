# setup.ps1 - One-shot setup for Savvy on Windows (the equivalent of setup.sh)
#
# Run from PowerShell:
#   powershell -ExecutionPolicy Bypass -File setup\setup.ps1

# Not "Stop": in Windows PowerShell 5.1 that turns any stderr output from a
# native program (pip notices, ollama progress) into a fatal error. Steps that
# matter check $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"

# Always work from the project root, wherever this was launched from.
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = "1"   # read/write files as UTF-8, not the Windows code page

function Refresh-Path {
    # winget installs update PATH for new shells only; pull it in for this one.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Test-Ollama {
    try { Invoke-RestMethod http://localhost:11434/api/tags -TimeoutSec 2 | Out-Null; return $true }
    catch { return $false }
}

Write-Host "========================================="
Write-Host "  Savvy - Setup (Windows)"
Write-Host "========================================="
Write-Host ""

# -----------------------------------------------
# 1. Ollama
# -----------------------------------------------
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "[OK] Ollama is already installed"
} else {
    Write-Host "[..] Installing Ollama with winget..."
    winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
    Refresh-Path
}

# -----------------------------------------------
# 2. Start Ollama if not running
# -----------------------------------------------
if (Test-Ollama) {
    Write-Host "[OK] Ollama server is running"
} else {
    Write-Host "[..] Starting Ollama in the background..."
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep 3
    if (Test-Ollama) { Write-Host "[OK] Ollama server started" }
    else { Write-Host "[!] Could not start Ollama. Try running 'ollama serve' in another window." }
}

# -----------------------------------------------
# 3. Models
# -----------------------------------------------
Write-Host ""
Write-Host "--- Pulling Models ---"
Write-Host "[..] Pulling Mistral 7B Instruct (Q4_K_M) - ~4.4 GB download..."
ollama pull mistral:7b-instruct-v0.3-q4_K_M
Write-Host "[..] Pulling nomic-embed-text - ~270 MB download..."
ollama pull nomic-embed-text
Write-Host "[OK] Models ready"

# -----------------------------------------------
# 4. Python + venv
# -----------------------------------------------
Write-Host ""
Write-Host "--- Python Setup ---"
# Prefer the py launcher: a bare 'python' may be the Microsoft Store stub.
$py = $null
$candidates = @(@{ exe = "py"; args = @("-3") }, @{ exe = "python"; args = @() })
foreach ($c in $candidates) {
    if (-not (Get-Command $c.exe -ErrorAction SilentlyContinue)) { continue }
    $a = $c.args
    $ver = & $c.exe @a -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $ver -and [version]$ver -ge [version]"3.11") { $py = $c; break }
}
if (-not $py) {
    Write-Host "[!] Python 3.11+ not found. Install it with:"
    Write-Host "      winget install -e --id Python.Python.3.12"
    Write-Host "    then open a NEW PowerShell window and re-run this script."
    exit 1
}
Write-Host "[..] Python $ver"

if (-not (Test-Path .venv)) {
    Write-Host "[..] Creating virtual environment..."
    $a = $py.args
    & $py.exe @a -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host "[!] Could not create .venv"; exit 1 }
}
$venvPy = ".venv\Scripts\python.exe"
Write-Host "[..] Installing Python dependencies..."
& $venvPy -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "[!] pip install failed - see above."; exit 1 }
Write-Host "[OK] Python dependencies installed"

# -----------------------------------------------
# 5. Credentials & config (interactive)
# -----------------------------------------------
# Safe to re-run on its own:  .venv\Scripts\python setup\configure.py
Write-Host ""
& $venvPy setup\configure.py

# -----------------------------------------------
# 6. Start Menu shortcut for the tray app
# -----------------------------------------------
$root = (Get-Location).Path
$pyw  = Join-Path $root ".venv\Scripts\pythonw.exe"
$lnk  = Join-Path ([Environment]::GetFolderPath("Programs")) "Savvy.lnk"
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$sh.TargetPath = $pyw
$sh.Arguments = "`"$root\src\desktop.py`""
$sh.WorkingDirectory = "$root\src"
$sh.Description = "Savvy - private secretary"
$sh.Save()
Write-Host "[OK] Added Savvy to the Start Menu"

Write-Host ""
Write-Host "========================================="
Write-Host "  Setup complete!"
Write-Host "========================================="
Write-Host ""
Write-Host "Savvy runs from the system tray: it starts on login, and you can open it"
Write-Host "any time from the Start Menu ('Savvy')."
Write-Host "Terminal REPL instead:       powershell -ExecutionPolicy Bypass -File setup\launch.ps1"
Write-Host "To change settings later:    .venv\Scripts\python setup\configure.py"
Write-Host ""

$ans = Read-Host "Start Savvy in the system tray now? [Y/n]"
if ($ans -notmatch '^[Nn]') {
    Start-Process $pyw -ArgumentList "`"$root\src\desktop.py`"" -WorkingDirectory "$root\src"
    Write-Host "[OK] Savvy is starting - look for its icon in the system tray."
}
