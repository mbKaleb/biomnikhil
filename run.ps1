# Bridge for v0.1.0 installs, whose updater copies a release over the folder and
# then restarts this path. Without this file it would restart its own old
# launcher, which would see its old VERSION and update forever. The launchers
# now live in app\. Remove once no v0.1.0 installs are left.
param(
    [switch]$Reinstall
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot\app\run.ps1" @PSBoundParameters
exit $LASTEXITCODE
