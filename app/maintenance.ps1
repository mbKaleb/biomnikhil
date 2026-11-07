# maintenance.ps1 - Runs setup or cleanup for the Settings tab, once the app has
# closed itself (app\services\maintenance.py starts this in its own console, then
# closes the app):
#
#   -Action setup     installs again in the setup window, which can open the app
#   -Action cleanup   app\uninstall.ps1 -Mode cleanup in this console, then opens the app

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("setup", "cleanup")]
    [string]$Action
)

$Root = Split-Path -Parent $PSScriptRoot

function Test-AppClosed {
    $port = "8000"
    $portLine = Select-String -Path "$Root\data\.env" -Pattern '^\s*PORT\s*=\s*(\d+)' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($portLine) { $port = $portLine.Matches[0].Groups[1].Value }
    try {
        $previousProgress = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        $response = Invoke-WebRequest "http://127.0.0.1:$port/ping" -UseBasicParsing -TimeoutSec 2
        $ProgressPreference = $previousProgress
        if ($response.Content -match "biomnikhil") { return $false }
    } catch {
        $ProgressPreference = "Continue"
    }

    # The window's WebView2 processes name data\webview in their command lines,
    # and they outlive the window by a moment. Cleanup refuses until they're gone.
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    foreach ($process in $processes) {
        if ($process.ProcessId -eq $PID) { continue }
        $commandLine = [string]$process.CommandLine
        if ($commandLine -match 'maintenance\.ps1') { continue }
        if ($commandLine.IndexOf($Root + "\", [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $false }
    }
    return $true
}

Write-Host "Waiting for biomnikhil to close..."
for ($waited = 0; $waited -lt 60; $waited++) {
    if (Test-AppClosed) { break }
    Start-Sleep -Seconds 1
}

if ($Action -eq "setup") {
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\app\setup.ps1" -Reinstall
    exit $LASTEXITCODE
}

& powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\app\uninstall.ps1" -Mode cleanup
Write-Host ""
Read-Host "Press Enter to close this window and open biomnikhil again" | Out-Null
# Cleanup can delete this folder when the app runs from a stray copy.
if (Test-Path -LiteralPath "$Root\app\run.ps1") {
    Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$Root\app\run.ps1`""
}
