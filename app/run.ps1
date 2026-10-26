# run.ps1 - Windows launcher, counterpart to run-mac.sh. Double-click
# "Start biomnikhil.cmd", or run: powershell -ExecutionPolicy Bypass -File run.ps1
#
# The first run sets everything up inside this folder, explaining each step in
# this window and with pop-ups: a Python environment (.venv, plus a private
# Python in python\ when the PC has no Python 3.11-3.13), the app's packages
# (pinned in requirements.lock) and .env. Later runs check for an update and
# start in seconds. Moving the folder makes the next run redo the setup.
#
#   -Reinstall                redo the setup
#   BIOMNIKHIL_NO_UPDATE=1    skip the update check
#   BIOMNIKHIL_NO_DIALOGS=1   no pop-ups (the steps still print here)

param(
    [switch]$Reinstall  # redo the setup
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest crawls with its progress bar
# Windows PowerShell 5.1 may not offer TLS 1.2 by default; GitHub and PyPI need it.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$Repo = $PSScriptRoot
$Log = "$Repo\data\setup.log"
$UvVersion = "0.13.5"  # uv supplies the private Python when the PC has none
$NoDialogs = [bool]$env:BIOMNIKHIL_NO_DIALOGS
$VenvPy = "$Repo\.venv\Scripts\python.exe"
$Ready = "$Repo\.venv\.biomnikhil-ready"

# --- prompts, pop-ups and helpers -----------------------------------------------
function Show-Box($Text, $Buttons, $Icon) {
    Add-Type -AssemblyName System.Windows.Forms
    return [System.Windows.Forms.MessageBox]::Show($Text, "biomnikhil setup", $Buttons, $Icon)
}

function Start-Hidden($Script) {  # runs a PowerShell snippet in its own hidden process
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Script))
    Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-EncodedCommand", $enc
}

function Confirm-Setup($Text) {
    Write-Host ""; Write-Host $Text; Write-Host ""
    if ($NoDialogs) { return }
    if ((Show-Box $Text "OKCancel" "Information") -ne "OK") {
        Write-Host "Setup cancelled."
        exit 1
    }
}

function Fail($What, $Todo) {
    Write-Host ""; Write-Host ""
    Write-Host "!! $What" -ForegroundColor Red
    Write-Host "   $Todo"
    if (-not $NoDialogs) { [void](Show-Box "$What`n`n$Todo" "OK" "Error") }
    exit 1
}

function Show-Finished($Text) {  # non-blocking: the app starts while it's shown
    if ($NoDialogs) { return }
    Start-Hidden ("Add-Type -AssemblyName System.Windows.Forms; [void][System.Windows.Forms.MessageBox]::Show('" +
        $Text.Replace("'", "''") + "', 'biomnikhil', 'OK', 'Information')")
}

function Step($N, $Label) {  # Step "2/5" "Internet access"  ->  "[2/5] Internet access ........ "
    Write-Host -NoNewline ("[{0}] {1} {2} " -f $N, $Label, ("." * [Math]::Max(1, 22 - $Label.Length)))
}

function Test-Python($Parts) {  # Parts: "py -3.12" split into exe + args
    if (-not (Get-Command $Parts[0] -ErrorAction SilentlyContinue)) { return $false }
    $pre = @($Parts | Select-Object -Skip 1)
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        # The Microsoft Store's "python" stub exits with 9009 here instead of running.
        & $Parts[0] @pre -c "import sys; sys.exit(not (3, 11) <= sys.version_info[:2] <= (3, 13))" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false } finally { $ErrorActionPreference = $old }
}

function Test-Reachable($Url) {
    try { Invoke-WebRequest -Uri $Url -Method Head -UseBasicParsing -TimeoutSec 10 | Out-Null; return $true }
    catch { return [bool]$_.Exception.Response }  # an HTTP error still means the site answered
}

function Test-App($Url) {
    try { return (Invoke-WebRequest "$Url/about" -UseBasicParsing -TimeoutSec 2).Content -match "biomnikhil" }
    catch { return $false }
}

function Invoke-Logged([scriptblock]$Command, $What, $Todo) {  # native command, output to the log
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $Command 2>&1 | Out-File -Append -Encoding utf8 $Log
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($code -ne 0) { Fail $What "$Todo Details are in $Log" }
}

function Test-Venv {  # pip's launcher breaks when the folder moves
    if (-not (Test-Path "$Repo\.venv\Scripts\pip.exe")) { return $false }
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & "$Repo\.venv\Scripts\pip.exe" --version *> $null
    $ok = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $old
    return $ok
}

# --- update: release installs (VERSION from the release zip, no .git folder)
# update themselves from the latest GitHub release. .env, data\, .venv and
# python\ aren't in the zip, so they're kept. Git checkouts update with git;
# test builds (versions like 0.2.0-rc1) stay on the build being tested.
if ((Test-Path "$Repo\VERSION") -and -not (Test-Path "$Repo\.git")) {
    $updated = $false
    $current = (Get-Content "$Repo\VERSION" -Raw).Trim()
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
                robocopy "$tmp\biomnikhil" $Repo /E /NFL /NDL /NJH /NJS /NP | Out-Null
                if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }
                Remove-Item $tmp -Recurse -Force
                $updated = $true
            }
        } catch {
            Write-Host "update failed ($($_.Exception.Message)) - starting $current"
        }
    }
    if ($updated) {
        & "$Repo\run.ps1" @PSBoundParameters  # restart on the new script
        exit $LASTEXITCODE
    }
}

# --- first-run setup -----------------------------------------------------------
if ($Reinstall -or -not (Test-Path $Ready) -or -not (Test-Venv)) {
    Write-Host "biomnikhil setup: everything installs inside"
    Write-Host "  $Repo"
    Write-Host "The first run takes 5-15 minutes; later starts take seconds."
    Write-Host ""
    New-Item -ItemType Directory -Force "$Repo\data" | Out-Null
    Set-Content -Path $Log -Value ""

    # Every package is built for this hardware (see requirements.lock).
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    if ($arch -ne "AMD64") {
        Fail "This PC has an $arch processor." "biomnikhil needs a 64-bit Intel or AMD PC: some of its packages (biopython, pyopenms) aren't built for Windows on ARM."
    }
    if ($Repo -match "\\OneDrive") {
        Fail "This folder is inside OneDrive." "biomnikhil keeps about 13 GB here, which OneDrive would try to sync. Move the biomnikhil folder out of OneDrive (for example to C:\biomnikhil) and start it from there."
    }

    Step "1/5" "Python 3.11-3.13"
    $PyParts = $null
    foreach ($c in @("py -3.13", "py -3.12", "py -3.11", "python")) {
        $parts = $c -split " "
        if (Test-Python $parts) { $PyParts = $parts; break }
    }
    if ($PyParts) {
        $pre = @($PyParts | Select-Object -Skip 1)
        $pyver = (& $PyParts[0] @pre --version).Trim()
        $pypath = (& $PyParts[0] @pre -c "import sys; print(sys.executable)").Trim()
        Write-Host "found $pyver ($pypath)"
        $pythonPlan = "Use your $pyver"
    } else {
        Write-Host "not found, a private copy will go in this folder"
        $pythonPlan = "Download a private copy of Python into this folder (about 40 MB)"
    }

    $spaceNote = ""
    try {
        $freeGb = [Math]::Floor((Get-PSDrive $Repo.Substring(0, 1)).Free / 1GB)
        if ($freeGb -lt 13) { $spaceNote = "`n`nOnly $freeGb GB is free on this drive; biomnikhil needs about 13 GB." }
    } catch {}  # network paths have no drive letter; skip the check
    Confirm-Setup ("biomnikhil will set itself up in this folder:`n$Repo`n`n" +
        "- $pythonPlan`n- Download the app's packages (about 1 GB)`n" +
        "- Later, the first analysis downloads the data lake (about 10 GB)`n`n" +
        "This takes 5-15 minutes the first time.$spaceNote")

    Step "2/5" "Internet access"
    $envFile = if (Test-Path "$Repo\.env") { "$Repo\.env" } else { "$Repo\.env.example" }
    $cfg = @{}
    Get-Content $envFile | ForEach-Object { if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*)$') { $cfg[$Matches[1]] = $Matches[2].Trim() } }
    $region = if ($cfg["AWS_REGION"]) { $cfg["AWS_REGION"] } else { "us-west-2" }
    $hosts = @("pypi.org", "files.pythonhosted.org", "github.com", "biomni-release.s3.amazonaws.com", "bedrock-mantle.$region.api.aws")
    if ($cfg["COGNITO_DOMAIN"]) { $hosts += ($cfg["COGNITO_DOMAIN"] -replace '^https://', '') }
    $blocked = @($hosts | Where-Object { -not (Test-Reachable "https://$_") })
    if ($blocked.Count -gt 0) {
        Fail ("This computer can't reach: " + ($blocked -join ", ")) "biomnikhil downloads its packages and data from these sites and sends model requests to Bedrock. Lab and hospital networks sometimes block them: try another network, or ask IT to allow these sites."
    }
    Write-Host "ok"

    Step "3/5" "Private Python"
    $retry = "Check your internet connection and start biomnikhil again."
    if ($PyParts) {
        Write-Host "not needed"
        Invoke-Logged { & $PyParts[0] @pre -m venv --clear "$Repo\.venv" } "Couldn't create the Python environment." ""
    } else {
        $tools = "$Repo\tools"
        New-Item -ItemType Directory -Force $tools | Out-Null
        try {
            Invoke-WebRequest "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile "$tools\uv.zip" -UseBasicParsing
            Expand-Archive "$tools\uv.zip" -DestinationPath $tools -Force
            Remove-Item "$tools\uv.zip"
        } catch {
            Fail "Couldn't download the Python installer (uv)." "$retry ($($_.Exception.Message))"
        }
        $env:UV_PYTHON_INSTALL_DIR = "$Repo\python"
        $env:UV_CACHE_DIR = "$tools\cache"
        Invoke-Logged { & "$tools\uv.exe" venv --clear --seed --managed-python --python 3.12 "$Repo\.venv" } "Couldn't install Python." $retry
        Write-Host ("installed " + (& $VenvPy --version).Trim())
    }

    Step "4/5" "App packages"
    Write-Host "installing about 1 GB, this takes a few minutes..."
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $VenvPy -m pip install --no-cache-dir --disable-pip-version-check -r "$Repo\requirements.lock" --log $Log
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($code -ne 0) {
        Fail "Installing the app's packages failed." "$retry It picks up where it stopped. Details are in $Log"
    }
    New-Item -ItemType File -Force $Ready | Out-Null

    Step "5/5" "Settings"
    if (Test-Path "$Repo\.env") { Write-Host "kept your .env" } else {
        Copy-Item "$Repo\.env.example" "$Repo\.env"
        Write-Host "created .env (no key needed, you sign in instead)"
    }
    $justSetUp = $true
} else {
    # Installs anything an update added; takes a second when nothing changed.
    New-Item -ItemType Directory -Force "$Repo\data" | Out-Null
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $VenvPy -m pip install -q --no-cache-dir --disable-pip-version-check -r "$Repo\requirements.lock" --log $Log
    if ($LASTEXITCODE -ne 0) { Write-Host ">> Couldn't update the app's packages (offline?), starting anyway." }
    $ErrorActionPreference = $old
    if (-not (Test-Path "$Repo\.env")) { Copy-Item "$Repo\.env.example" "$Repo\.env" }
    $justSetUp = $false
}

# --- start ---------------------------------------------------------------------
$Port = 8000
$portLine = Select-String -Path "$Repo\.env" -Pattern '^\s*PORT\s*=\s*(\d+)' | Select-Object -First 1
if ($portLine) { $Port = [int]$portLine.Matches[0].Groups[1].Value }
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
Start-Hidden ("for (`$i = 0; `$i -lt 90; `$i++) { try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '$Url/about').Content -match 'biomnikhil') { Start-Process '$Url'; break } } catch {}; Start-Sleep 1 }")
if ($justSetUp) {
    Show-Finished "Setup finished. biomnikhil is opening in your browser.`n`nKeep the biomnikhil window open while you use it; closing it stops the app."
}

$env:PYTHONUTF8 = "1"
# The agent can pip-install packages mid-run; this puts the venv's pip first.
$env:VIRTUAL_ENV = "$Repo\.venv"
$env:PATH = "$Repo\.venv\Scripts;$env:PATH"
Set-Location $Repo
# --threads: each open SSE trace stream pins a worker thread for the life of
# its task, so the default of 4 would stall the app with a few open tabs.
& "$Repo\.venv\Scripts\waitress-serve.exe" --listen=127.0.0.1:$Port --threads=16 wsgi:app
