# Dot-source this in every new PowerShell session:   . scripts\activate.ps1
# Activates the box env and keeps every cache/config inside the box.

$Repo = (Resolve-Path "$PSScriptRoot\..").Path
$Box  = (Resolve-Path "$Repo\..").Path

& "$Box\miniforge\shell\condabin\conda-hook.ps1"
conda activate "$Box\env"

$env:PYTHONUTF8 = "1"
$env:PIP_CACHE_DIR = "$Box\pip-cache"
$env:OLLAMA_MODELS = "$Box\ollama-models"

Write-Host "biomnikhil env active (box: $Box)"
