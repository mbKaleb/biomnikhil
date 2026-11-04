# run.ps1 - zero-to-running on a fresh Windows machine. Per-user, no admin, no WSL.
#
# First time on a machine:  powershell -ExecutionPolicy Bypass -File run.ps1
# Every time after:         .\run.ps1
#
# What it does, in order: finds conda (installs Miniforge per-user if missing),
# creates the 'bionikhil' env on first run, pip-installs requirements, makes
# sure .env exists, then serves the app with waitress.

param(
    [switch]$Reinstall  # force reinstall of python packages
)

$ErrorActionPreference = "Stop"
$Repo    = $PSScriptRoot
$EnvName = "bionikhil"
$Forge   = "$env:USERPROFILE\Miniforge3"

# --- 0. update: release installs (VERSION from the release zip, no .git folder)
# update themselves from the latest GitHub release. .env and data\ aren't in
# the zip, so they're kept. BIOMNIKHIL_NO_UPDATE=1 skips this; git checkouts
# update with git instead.
if ((Test-Path "$Repo\VERSION") -and -not (Test-Path "$Repo\.git")) {
    $updated = $false
    if (-not $env:BIOMNIKHIL_NO_UPDATE) {
        $current = (Get-Content "$Repo\VERSION" -Raw).Trim()
        try {
            $latest = (Invoke-RestMethod "https://api.github.com/repos/mbKaleb/biomnikhil/releases/latest" -TimeoutSec 10).tag_name.TrimStart("v")
            if ($latest -and $latest -ne $current) {
                Write-Host "updating biomnikhil $current -> $latest..."
                $tmp = Join-Path $env:TEMP "biomnikhil-update"
                Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
                New-Item -ItemType Directory $tmp | Out-Null
                Invoke-WebRequest "https://github.com/mbKaleb/biomnikhil/releases/latest/download/biomnikhil.zip" -OutFile "$tmp\biomnikhil.zip"
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

# --- 1. conda: use existing, or install Miniforge per-user -------------------
if (Test-Path "$Forge\shell\condabin\conda-hook.ps1") {
    & "$Forge\shell\condabin\conda-hook.ps1"
}
elseif (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    Write-Host "conda not found - installing Miniforge into $Forge (per-user, no admin)..."
    $exe = "$env:TEMP\Miniforge3.exe"
    Invoke-WebRequest "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe" -OutFile $exe
    Start-Process $exe -ArgumentList "/InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=$Forge" -Wait
    Remove-Item $exe
    & "$Forge\shell\condabin\conda-hook.ps1"
}

# --- 2. env: create on first run, then activate ------------------------------
$condaBase = (& conda info --base).Trim()
$envPath   = Join-Path $condaBase "envs\$EnvName"
$freshEnv  = $false
if (-not (Test-Path $envPath)) {
    Write-Host "creating conda env '$EnvName' (python 3.11)..."
    conda create -n $EnvName python=3.11 -y
    $freshEnv = $true
}
conda activate $EnvName

# --- 3. packages --------------------------------------------------------------
if ($freshEnv -or $Reinstall) {
    pip install --upgrade -r "$Repo\requirements.txt"
} else {
    # idempotent + fast when everything is already satisfied
    pip install -q -r "$Repo\requirements.txt"
}

# --- 4. session config ---------------------------------------------------------
$env:PYTHONUTF8 = "1"
Set-Location $Repo

if (-not (Test-Path "$Repo\.env")) {
    Copy-Item "$Repo\.env.example" "$Repo\.env"
    Write-Host ""
    Write-Host ">>> No .env found - created one. Sign in with your Cognito account"
    Write-Host ">>> when the page opens."
}

# --- 5. serve ------------------------------------------------------------------
$Port = 8000
$portLine = Select-String -Path "$Repo\.env" -Pattern '^\s*PORT\s*=\s*(\d+)' |
            Select-Object -First 1
if ($portLine) { $Port = [int]$portLine.Matches[0].Groups[1].Value }

Write-Host ""
Write-Host "biomnikhil -> http://localhost:$Port   (Ctrl+C to stop)"
# --threads: each open SSE trace stream pins a worker thread for the life of
# its task, so the default of 4 would stall the app with a few open tabs.
waitress-serve --listen=127.0.0.1:$Port --threads=16 wsgi:app