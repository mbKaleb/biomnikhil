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
    Write-Host ">>> No .env found - created one and opening it in notepad."
    Write-Host ">>> Set BIOMNI_PROVIDER, paste the matching API key, save, and"
    Write-Host ">>> close notepad. The server will start right after."
    Start-Process notepad "$Repo\.env" -Wait
}

# --- 5. serve ------------------------------------------------------------------
$Port = 8000
$portLine = Select-String -Path "$Repo\.env" -Pattern '^\s*PORT\s*=\s*(\d+)' |
            Select-Object -First 1
if ($portLine) { $Port = [int]$portLine.Matches[0].Groups[1].Value }

Write-Host ""
Write-Host "biomnikhil -> http://127.0.0.1:$Port   (Ctrl+C to stop)"
waitress-serve --listen=127.0.0.1:$Port wsgi:app