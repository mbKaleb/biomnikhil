# run.ps1 - Windows launcher, counterpart to run-mac.sh. Started by double-
# clicking setup-windows.cmd (or the biomnikhil shortcut the installer
# creates), or:
#   powershell -ExecutionPolicy Bypass -File app\run.ps1
#
# Until biomnikhil is installed, this fetches uv (pinned below) into
# runtime\tools, uses it to make a small Python environment with pywebview,
# and runs the installer window (app\installer), which does the real setup and
# is the same on Mac and Windows. After that it checks for an update and opens
# the app window (app\installer\window.py), which runs the server in the
# background; this console closes. Moving the folder makes the next run install again.
#
#   -Reinstall                install again
#   BIOMNIKHIL_NO_UPDATE=1    skip the update check
#   BIOMNIKHIL_NO_DIALOGS=1   install and run in this window, no installer or app window

param(
    [switch]$Reinstall  # install again
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest crawls with its progress bar
# Windows PowerShell 5.1 may not offer TLS 1.2 by default; GitHub needs it.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$Root = Split-Path -Parent $PSScriptRoot
$Venv = "$Root\runtime\.venv"
$VenvPy = "$Venv\Scripts\python.exe"
$Tools = "$Root\runtime\tools"
$Uv = "$Tools\uv.exe"
$UvVersion = "0.13.5"
$env:UV_CACHE_DIR = "$Tools\cache"
$env:UV_PYTHON_INSTALL_DIR = "$Root\runtime\python"

function Fail($What, $Todo) {
    Write-Host ""
    Write-Host "!! $What" -ForegroundColor Red
    Write-Host "   $Todo"
    if (-not $env:BIOMNIKHIL_NO_DIALOGS) {
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show("$What`n`n$Todo", "biomnikhil", "OK", "Error")
    }
    exit 1
}

function Invoke-Native([scriptblock]$Command) {  # runs a native command, echoing its output; returns its exit code
    # Windows PowerShell 5.1 turns a native command's stderr into errors, which
    # "Stop" would make fatal.
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = 1  # stays 1 when the command doesn't exist
    try { & $Command 2>&1 | ForEach-Object { Write-Host "$_" }; return $LASTEXITCODE }
    catch { return 1 } finally { $ErrorActionPreference = $old }
}

function Install-Uv {  # the pinned uv in runtime\tools; $true when it's there
    if (Test-Path $Uv) {
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $have = "$(& $Uv --version 2>$null)"
        $ErrorActionPreference = $old
        if ($have -match "^uv $([regex]::Escape($UvVersion))\b") { return $true }
    }
    try {
        New-Item -ItemType Directory -Force $Tools | Out-Null
        Invoke-WebRequest "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile "$Tools\uv.zip" -UseBasicParsing -TimeoutSec 300
        Expand-Archive "$Tools\uv.zip" -DestinationPath $Tools -Force
        Remove-Item "$Tools\uv.zip"
        return $true
    } catch {
        Write-Host "uv download failed: $($_.Exception.Message)"
        return $false
    }
}

function Start-Hidden($Script) {  # runs a PowerShell snippet in its own hidden process
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Script))
    Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-EncodedCommand", $enc
}

function Test-App($Url) {
    try { return (Invoke-WebRequest "$Url/ping" -UseBasicParsing -TimeoutSec 2).Content -match "biomnikhil" }
    catch { return $false }
}

# --- update: release installs (app\VERSION from the release zip, no .git
# folder) update themselves from the latest GitHub release. data\ and runtime\
# aren't in the zip, so they're kept. Git checkouts update with git; test
# builds (versions like 0.2.0-rc1) stay on the build being tested.
if ((Test-Path "$Root\app\VERSION") -and -not (Test-Path "$Root\.git")) {
    $updated = $false
    $current = (Get-Content "$Root\app\VERSION" -Raw).Trim()
    if (-not $env:BIOMNIKHIL_NO_UPDATE -and -not $current.Contains("-")) {
        try {
            $latest = (Invoke-RestMethod "https://api.github.com/repos/mbKaleb/biomnikhil/releases/latest" -TimeoutSec 10).tag_name.TrimStart("v")
            if ($latest -and $latest -ne $current) {
                Write-Host "updating biomnikhil $current -> $latest..."
                $tmp = Join-Path $env:TEMP "biomnikhil-update"
                Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
                New-Item -ItemType Directory $tmp | Out-Null
                Invoke-WebRequest "https://github.com/mbKaleb/biomnikhil/releases/latest/download/biomnikhil.zip" -OutFile "$tmp\biomnikhil.zip" -UseBasicParsing
                Expand-Archive "$tmp\biomnikhil.zip" -DestinationPath $tmp
                # robocopy merges into existing folders; exit codes 8+ are failures
                robocopy "$tmp\biomnikhil" $Root /E /NFL /NDL /NJH /NJS /NP | Out-Null
                if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }
                Remove-Item $tmp -Recurse -Force
                $updated = $true
            }
        } catch {
            Write-Host "update failed ($($_.Exception.Message)) - starting $current"
        }
    }
    if ($updated) {
        & "$Root\app\run.ps1" @PSBoundParameters  # restart on the new script
        exit $LASTEXITCODE
    }
}

# --- install -------------------------------------------------------------------
# The installer writes .biomnikhil-ready last; pip's own launcher breaks when
# the folder moves, which also sends a run back through the installer.
$iv = "$Tools\installer"  # the installer's Python environment, with pywebview; it runs the app window too
$ivPy = "$iv\Scripts\python.exe"

function Install-InstallerEnv {  # makes $iv unless it's there
    if ((Invoke-Native { & $ivPy -c "import webview" *> $null }) -eq 0) { return }
    $retry = "Check your internet connection, then run setup-windows.cmd again."
    if (-not (Install-Uv)) { Fail "Couldn't download the installer." $retry }
    # An installed Python 3.11-3.13 if there is one, else uv's own 3.12.
    if ((Invoke-Native { & $Uv venv -q --clear --python-preference only-system --python ">=3.11,<3.14" $iv *> $null }) -ne 0 -and
        (Invoke-Native { & $Uv venv -q --clear --managed-python --python 3.12 $iv }) -ne 0) {
        Fail "Couldn't set up the installer." $retry
    }
    if ((Invoke-Native { & $Uv pip install -q --python $ivPy -r "$Root\app\installer\requirements.lock" }) -ne 0) {
        Fail "Couldn't set up the installer." $retry
    }
}

New-Item -ItemType Directory -Force "$Root\data" | Out-Null
if ($Reinstall -or -not (Test-Path "$Venv\.biomnikhil-ready") -or
    (Invoke-Native { & "$Venv\Scripts\pip.exe" --version *> $null }) -ne 0) {
    Write-Host "Preparing installer..."
    if ($Reinstall) { $env:BIOMNIKHIL_REINSTALL = "1" }
    Install-InstallerEnv
    # Run directly, not through Invoke-Native, so the console fallback can prompt.
    $ErrorActionPreference = "Continue"
    & $ivPy "$Root\app\installer"
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($code -eq 3) { exit 0 }  # installed, but not opening now
    if ($code -ne 0) { exit 1 }
} else {
    # Installs anything an update added; takes a second when nothing changed.
    if (Install-Uv) {
        $code = Invoke-Native { & $Uv pip install -q --python $VenvPy -r "$Root\app\requirements.lock" *> $null }
    } else {
        $code = Invoke-Native { & $VenvPy -m pip install -q --disable-pip-version-check -r "$Root\app\requirements.lock" *> $null }
    }
    if ($code -ne 0) { Write-Host ">> Couldn't update the app's packages (offline?), starting anyway." }
    if (-not (Test-Path "$Root\data\.env")) { Copy-Item "$Root\app\.env.example" "$Root\data\.env" }
    if (-not $env:BIOMNIKHIL_NO_DIALOGS) { Install-InstallerEnv }
}

# --- start ---------------------------------------------------------------------
$Port = 8000
$portLine = Select-String -Path "$Root\data\.env" -Pattern '^\s*PORT\s*=\s*(\d+)' | Select-Object -First 1
if ($portLine) { $Port = [int]$portLine.Matches[0].Groups[1].Value }

if (-not $env:BIOMNIKHIL_NO_DIALOGS) {
    # The app window (app\installer\window.py) starts the server in the
    # background and stops it when the window closes. pythonw: no console.
    Start-Process "$iv\Scripts\pythonw.exe" -ArgumentList "`"$Root\app\installer\window.py`"", $Port -WorkingDirectory $Root
    exit 0
}

# Without dialogs: the server runs in this window, in a browser tab.
$Url = "http://localhost:$Port"
if (Test-App $Url) {
    Write-Host ""; Write-Host "biomnikhil is already running, opening $Url"
    Start-Process $Url
    exit 0
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Fail "Port $Port is in use by another program." "Close the other program that uses port $Port, then start biomnikhil again."
}

Write-Host ""
Write-Host "biomnikhil is running at $Url"
Write-Host "Keep this window open while you use it; closing it stops the app."
# Open the browser once the server answers.
Start-Hidden ("for (`$i = 0; `$i -lt 90; `$i++) { try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '$Url/ping').Content -match 'biomnikhil') { Start-Process '$Url'; break } } catch {}; Start-Sleep 1 }")

$env:PYTHONUTF8 = "1"
# The agent can pip-install packages mid-run; this puts the venv's pip first.
$env:VIRTUAL_ENV = $Venv
$env:PATH = "$Venv\Scripts;$env:PATH"
Set-Location $Root
# waitress and --threads: see app\installer\window.py.
& "$Venv\Scripts\waitress-serve.exe" --listen=127.0.0.1:$Port --threads=16 app.wsgi:app
