# Builds the disposable box AROUND this repo clone. No admin, no WSL, no Docker.
#
# Layout after running (box = the folder you cloned the repo into):
#   <box>\
#     miniforge\      python, self-contained
#     env\            conda env with biomni + flask
#     pip-cache\      pip downloads
#     data\           biomni data lake (~11 GB, downloads on first task)
#     biomnikhil\      this repo
#
# Usage (once, after cloning):
#   powershell -ExecutionPolicy Bypass -File scripts\setup-box.ps1
#
# Teardown = delete the box folder. Nothing else is touched.

$ErrorActionPreference = "Stop"

$Repo = (Resolve-Path "$PSScriptRoot\..").Path
$Box  = (Resolve-Path "$Repo\..").Path
Write-Host "repo: $Repo"
Write-Host "box:  $Box"

if (-not (Test-Path "$Box\miniforge")) {
    Write-Host "downloading Miniforge..."
    Invoke-WebRequest "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe" -OutFile "$Box\Miniforge3.exe"
    Start-Process "$Box\Miniforge3.exe" -ArgumentList "/InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=$Box\miniforge" -Wait
    Remove-Item "$Box\Miniforge3.exe"
}

& "$Box\miniforge\shell\condabin\conda-hook.ps1"

if (-not (Test-Path "$Box\env")) {
    conda create --prefix "$Box\env" python=3.11 -y
}
conda activate "$Box\env"

$env:PYTHONUTF8 = "1"
$env:PIP_CACHE_DIR = "$Box\pip-cache"
pip install --upgrade -r "$Repo\requirements.txt"

if (-not (Test-Path "$Repo\.env")) {
    Copy-Item "$Repo\.env.example" "$Repo\.env"
    Write-Host ""
    Write-Host ">>> created .env — open it and add your API key <<<"
}

Write-Host ""
Write-Host "done. every new shell:  . scripts\activate.ps1"
Write-Host "then start the app:     .\run.ps1"
