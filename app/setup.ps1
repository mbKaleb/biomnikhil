# setup.ps1 - Windows installer step, started by setup-windows.cmd. Copies app\
# from the unzipped release to the folder you choose (%LOCALAPPDATA%\biomnikhil
# unless you change it) and runs it from there, so the unzipped folder can be
# deleted afterwards. A git checkout, or a folder that is already installed, runs
# where it is. A finished install stays where it is, so running a newer zip
# doesn't ask again. BIOMNIKHIL_INSTALL_DIR=D:\some\folder installs in that folder
# without asking. Everything is logged to setup.log in the install folder.

param(
    [switch]$Reinstall  # passed on to run.ps1
)

$ErrorActionPreference = "Stop"
$Source = Split-Path -Parent $PSScriptRoot
$DefaultTarget = Join-Path $env:LOCALAPPDATA "biomnikhil"
# Where the next setup and the uninstaller find the install.
$SavedLocation = Join-Path $env:LOCALAPPDATA "biomnikhil-location.txt"
$MaximumTargetLength = 100  # the app's packages add deep paths under it; Windows stops at 260
$Target = $DefaultTarget
$Log = Join-Path $Target "setup.log"

function Show-Failure($Message) {
    Write-Host ""
    Write-Host "!! biomnikhil setup failed: $Message" -ForegroundColor Red
    Write-Host "   Details are in $Log and $Target\data\diagnostics.log"
    if (-not $env:BIOMNIKHIL_NO_DIALOGS) {
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show("$Message`n`nDetails are in $Log", "biomnikhil", "OK", "Error")
    }
}

function Copy-App {  # makes $Target\app match $Source\app, and brings the uninstall launcher
    $uninstallLauncher = Join-Path $Source "uninstall-windows.cmd"
    if (Test-Path -LiteralPath $uninstallLauncher) {
        Copy-Item -LiteralPath $uninstallLauncher -Destination $Target -Force
    }
    $sourceApp = Join-Path $Source "app"
    $targetApp = Join-Path $Target "app"
    Copy-Item -LiteralPath $sourceApp -Destination $Target -Recurse -Force
    $targetFiles = Get-ChildItem -LiteralPath $targetApp -Recurse -File
    foreach ($file in $targetFiles) {
        $relative = $file.FullName.Substring($targetApp.Length)
        if (-not (Test-Path -LiteralPath ($sourceApp + $relative))) {
            Remove-Item -LiteralPath $file.FullName -Force
        }
    }
}

function Save-Location {
    try {
        Set-Content -LiteralPath $SavedLocation -Value $Target -Encoding UTF8
    } catch {
        Write-Host "Couldn't save where biomnikhil is installed: $($_.Exception.Message)"
    }
}

function Test-Install([string]$Path) {
    if (-not $Path) { return $false }
    try {
        $coreFile = [IO.Path]::Combine($Path, "app\installer\core.py")
        $configFile = [IO.Path]::Combine($Path, "app\config.py")
        return (Test-Path -LiteralPath $coreFile) -and (Test-Path -LiteralPath $configFile)
    } catch {
        return $false
    }
}

function Test-FinishedInstall([string]$Path) {
    if (-not (Test-Install $Path)) { return $false }
    return (Test-Path -LiteralPath ([IO.Path]::Combine($Path, "runtime\.venv\.biomnikhil-ready")))
}

function Get-SavedTarget {  # the saved folder, if there's still an install in it; else ""
    if (-not (Test-Path -LiteralPath $SavedLocation)) { return "" }
    try {
        $saved = [string](Get-Content -LiteralPath $SavedLocation -TotalCount 1)
    } catch {
        return ""
    }
    if (-not (Test-Install $saved)) { return "" }
    return $saved.TrimEnd("\")
}

function Get-TargetIn([string]$Chosen) {  # always a biomnikhil folder, so an uninstall never takes the chosen folder itself
    $withBackslashes = $Chosen.Replace("/", "\")
    if ($withBackslashes -notmatch '^[A-Za-z]:\\') { return $withBackslashes }
    try {
        $full = [IO.Path]::GetFullPath($withBackslashes)
    } catch {
        return $withBackslashes
    }
    $trimmed = $full.TrimEnd("\")
    if ([IO.Path]::GetFileName($trimmed) -ieq "biomnikhil") { return $trimmed }
    return [IO.Path]::Combine($full, "biomnikhil")
}

function Test-CloudFolder([string]$Path) {
    $syncedNames = '\\(OneDrive[^\\]*|Dropbox[^\\]*|Google Drive|GoogleDrive|My Drive|Box|Box Sync|iCloudDrive|iCloud Drive)(\\|$)'
    if ($Path -match $syncedNames) { return $true }
    $syncedFolders = @($env:OneDrive, $env:OneDriveCommercial, $env:OneDriveConsumer)
    foreach ($folder in $syncedFolders) {
        if (-not $folder) { continue }
        $folderWithSlash = $folder.TrimEnd("\") + "\"
        if (($Path + "\").StartsWith($folderWithSlash, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Test-CanWrite([string]$Path) {
    $existed = Test-Path -LiteralPath $Path
    try {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
        $probe = [IO.Path]::Combine($Path, ".biomnikhil-write-check")
        Set-Content -LiteralPath $probe -Value "check"
        Remove-Item -LiteralPath $probe -Force
    } catch {
        return $false
    }
    if (-not $existed) {
        try { Remove-Item -LiteralPath $Path -Force } catch {}
    }
    return $true
}

function Get-LocationProblem([string]$Path) {  # why biomnikhil can't go here; "" if it can
    if ($Path.StartsWith("\\")) {
        return "That's a network folder, which is too slow for biomnikhil and can vanish while it runs. Choose a folder on this PC."
    }
    if ($Path -notmatch '^[A-Za-z]:\\') {
        return "That isn't a full path."
    }
    $driveLetter = $Path.Substring(0, 1)
    $drive = New-Object IO.DriveInfo $driveLetter
    if ($drive.DriveType -eq [IO.DriveType]::Network) {
        return "That's a network drive, which is too slow for biomnikhil and can vanish while it runs. Choose a folder on this PC."
    }
    if ($drive.DriveType -eq [IO.DriveType]::NoRootDirectory) {
        return "There's no drive ${driveLetter}: on this PC."
    }
    if ($Path.Length -gt $MaximumTargetLength) {
        return "That path is too long for the files biomnikhil installs. Choose a folder with a shorter path."
    }
    if (Test-CloudFolder $Path) {
        return "That folder syncs to the cloud, which would upload biomnikhil's files and lock them while it runs. Choose a folder that doesn't sync."
    }
    if (($Path + "\").StartsWith($Source + "\", [StringComparison]::OrdinalIgnoreCase)) {
        return "That's inside the unzipped folder, which you can delete after setup. Choose a folder outside it."
    }
    if ($env:USERPROFILE -and ($Path -ieq $env:USERPROFILE.TrimEnd("\"))) {
        return "biomnikhil can't use your whole user folder. Choose a folder inside it."
    }
    if (Test-Path -LiteralPath $Path) {
        try {
            $existing = Get-Item -LiteralPath $Path -Force
            if ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                return "$Path is a link to another folder. Choose the real folder."
            }
            if (-not $existing.PSIsContainer) {
                return "There's already a file called $Path."
            }
            $firstEntry = Get-ChildItem -LiteralPath $Path -Force | Select-Object -First 1
            if ($firstEntry -and -not (Test-Install $Path)) {
                return "$Path already has other files in it. Choose another folder."
            }
        } catch {
            return "biomnikhil can't read $Path. Choose another folder."
        }
    }
    if (-not (Test-CanWrite $Path)) {
        return "biomnikhil can't write there. Choose a folder in your user folder or on another drive."
    }
    return ""
}

function Get-NearestFolder([string]$Path) {
    $folder = $Path
    while ($folder -and -not (Test-Path -LiteralPath $folder)) {
        $folder = [IO.Path]::GetDirectoryName($folder)
    }
    return $folder
}

function Get-FreeSpaceText([string]$Path) {
    try {
        $drive = New-Object IO.DriveInfo $Path.Substring(0, 1)
        $gigabytes = [math]::Floor($drive.AvailableFreeSpace / 1GB)
        return "$gigabytes GB free there."
    } catch {
        return ""
    }
}

function Read-ChosenFolder($Owner, [string]$StartIn) {  # the folder, or "" if none was chosen
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    try {
        $dialog.Description = "Choose where to put biomnikhil. It goes in a biomnikhil folder inside the one you choose."
        $dialog.SelectedPath = $StartIn
        $result = $dialog.ShowDialog($Owner)
        if ($result -ne [System.Windows.Forms.DialogResult]::OK) { return "" }
        return $dialog.SelectedPath
    } finally {
        $dialog.Dispose()
    }
}

function Read-Target([string]$Suggested) {  # the folder, or "" if cancelled
    Add-Type -AssemblyName System.Windows.Forms
    # A window nobody sees, so the dialogs open in front of everything else.
    $owner = New-Object System.Windows.Forms.Form
    try {
        $owner.TopMost = $true
        $current = $Suggested
        while ($true) {
            $message = "Install biomnikhil in this folder?`n`n$current`n`nIt needs about 16 GB, with the data lake. $(Get-FreeSpaceText $current)`n`nYes installs here. No lets you choose another folder."
            $buttons = [System.Windows.Forms.MessageBoxButtons]::YesNoCancel
            $icon = [System.Windows.Forms.MessageBoxIcon]::Question
            $answer = [System.Windows.Forms.MessageBox]::Show($owner, $message, "biomnikhil", $buttons, $icon)
            if ($answer -eq [System.Windows.Forms.DialogResult]::Cancel) { return "" }
            if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) { return $current }
            $chosen = Read-ChosenFolder $owner (Get-NearestFolder ([IO.Path]::GetDirectoryName($current)))
            if (-not $chosen) { continue }
            $candidate = Get-TargetIn $chosen
            $problem = Get-LocationProblem $candidate
            if ($problem) {
                $warning = [System.Windows.Forms.MessageBoxIcon]::Warning
                [void][System.Windows.Forms.MessageBox]::Show($owner, $problem, "biomnikhil can't install there", [System.Windows.Forms.MessageBoxButtons]::OK, $warning)
                continue
            }
            $current = $candidate
        }
    } finally {
        $owner.Dispose()
    }
}

function Get-InstallTarget {  # the folder, or "" if cancelled
    if ($env:BIOMNIKHIL_INSTALL_DIR) {
        $given = Get-TargetIn $env:BIOMNIKHIL_INSTALL_DIR
        $problem = Get-LocationProblem $given
        if ($problem) {
            Write-Host "biomnikhil setup can't install in ${given}: $problem" -ForegroundColor Red
            exit 1
        }
        return $given
    }
    $saved = Get-SavedTarget
    if (Test-FinishedInstall $saved) { return $saved }
    if (Test-FinishedInstall $DefaultTarget) { return $DefaultTarget }
    $suggested = $DefaultTarget
    if ($saved) { $suggested = $saved }
    if ($env:BIOMNIKHIL_NO_DIALOGS) { return $suggested }
    try {
        return (Read-Target $suggested)
    } catch {
        # No dialogs here (a remote session, say), which shouldn't stop the install.
        Write-Host "Couldn't show the install location window: $($_.Exception.Message)"
        return $suggested
    }
}

$installed = Test-Path -LiteralPath (Join-Path $Source "runtime\.venv\.biomnikhil-ready")
$checkout = Test-Path -LiteralPath (Join-Path $Source ".git")
$inPlace = $installed -or $checkout -or ($Source -ieq $DefaultTarget) -or ($Source -ieq (Get-SavedTarget))
if ($inPlace) {
    $Target = $Source
} else {
    # Before there's an install folder to log to, so a failure here only goes to
    # this console and a dialog.
    try {
        $Target = Get-InstallTarget
    } catch {
        Write-Host ($_ | Out-String)
        Write-Host "!! biomnikhil setup failed while choosing where to install: $($_.Exception.Message)" -ForegroundColor Red
        if (-not $env:BIOMNIKHIL_NO_DIALOGS) {
            Add-Type -AssemblyName System.Windows.Forms
            [void][System.Windows.Forms.MessageBox]::Show("Setup failed while choosing where to install:`n`n$($_.Exception.Message)", "biomnikhil", "OK", "Error")
        }
        exit 1
    }
    if (-not $Target) {
        Write-Host "Setup cancelled. Nothing was changed."
        exit 0
    }
}
$Log = Join-Path $Target "setup.log"

$code = 1
try {
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
    Start-Transcript -LiteralPath $Log -Append | Out-Null
    Write-Host "biomnikhil setup: $Source -> $Target"
    if (-not $inPlace) {
        Write-Host "Copying biomnikhil to $Target ..."
        Copy-App
        Save-Location
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
