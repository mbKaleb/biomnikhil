# setup.ps1 - Windows installer step, started by setup-windows.cmd. Copies app\
# from the unzipped release to %LOCALAPPDATA%\biomnikhil and runs it from there,
# so the install is never inside OneDrive or Downloads. The unzipped folder can
# be deleted afterwards. A git checkout, or a folder that is already installed,
# runs where it is. Everything is logged to setup.log in the install folder.

param(
    [switch]$Reinstall  # passed on to run.ps1
)

$ErrorActionPreference = "Stop"
$Source = Split-Path -Parent $PSScriptRoot
$Target = Join-Path $env:LOCALAPPDATA "biomnikhil"
$Log = Join-Path $Target "setup.log"

function Show-Failure($Message) {
    Write-Host ""
    Write-Host "!! biomnikhil setup failed: $Message" -ForegroundColor Red
    Write-Host "   Details are in $Log and $Target\data\diagnostics.log"
    if (-not $env:BIOMNIKHIL_NO_DIALOGS) {
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show("$Message`n`nDetails are in $Log", "biomnikhil setup", "OK", "Error")
    }
}

function Copy-App {  # makes $Target\app match $Source\app, and brings the uninstall launcher
    $uninstallLauncher = Join-Path $Source "uninstall-windows.cmd"
    if (Test-Path -LiteralPath $uninstallLauncher) {
        Copy-Item -LiteralPath $uninstallLauncher -Destination $Target -Force
    }
    $sourceApp = Join-Path $Source "app"
    $targetApp = Join-Path $Target "app"
    Copy-Item $sourceApp -Destination $Target -Recurse -Force
    $targetFiles = Get-ChildItem $targetApp -Recurse -File
    foreach ($file in $targetFiles) {
        $relative = $file.FullName.Substring($targetApp.Length)
        if (-not (Test-Path -LiteralPath ($sourceApp + $relative))) {
            Remove-Item -LiteralPath $file.FullName -Force
        }
    }
}

$installed = Test-Path (Join-Path $Source "runtime\.venv\.biomnikhil-ready")
$checkout = Test-Path (Join-Path $Source ".git")
$inPlace = $installed -or $checkout -or ($Source -eq $Target)
if ($inPlace) {
    $Target = $Source
    $Log = Join-Path $Target "setup.log"
}

$code = 1
try {
    New-Item -ItemType Directory -Force $Target | Out-Null
    Start-Transcript -Path $Log -Append | Out-Null
    Write-Host "biomnikhil setup: $Source -> $Target"
    if (-not $inPlace) {
        Write-Host "Copying biomnikhil to $Target ..."
        Copy-App
    }
    & (Join-Path $Target "app\run.ps1") @PSBoundParameters
    $code = $LASTEXITCODE
    if ($null -eq $code) { $code = 0 }
    if ($code -ne 0) { Show-Failure "Setup stopped before biomnikhil could open (exit code $code)." }
} catch {
    Write-Host ($_ | Out-String)
    Show-Failure $_.Exception.Message
    $code = 1
} finally {
    try { Stop-Transcript | Out-Null } catch {}
}
exit $code
