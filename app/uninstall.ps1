# uninstall.ps1 - removes biomnikhil from this PC. Started by double-clicking
# uninstall-windows.cmd, by the Start menu's Uninstall biomnikhil entry, or:
#   powershell -ExecutionPolicy Bypass -File app\uninstall.ps1
#
# It lists the exact paths and sizes, then needs a typed confirmation, read from
# standard input. It stays inside these rules whatever it is told:
#   - it deletes only known names: the install's app\, data\, runtime\ and launchers,
#     and a lake's data_lake\, benchmark\ and .sizes.json, never the folder around them
#   - junctions and symlinks are removed, never followed
#   - a lake that may be used by others (a shared folder, or a folder typed in,
#     which is what app\services\datalake.py treats as maybe shared) stays unless
#     its full path is typed at its own prompt
#   - a git checkout keeps its source: only untracked data\ and runtime\ can go
#   - it refuses to run while biomnikhil is running and never stops the app itself
#
# So that it can delete its own folder, it first copies itself to a temp folder
# and runs from there with -StartedFrom. That is only where to look; the copy
# checks the folder like any other.
#
# Exit codes: 0 finished, 1 something could not be removed, 2 refused, 3 cancelled.
#
# BIOMNIKHIL_UNINSTALL_TEST_DESKTOP and BIOMNIKHIL_UNINSTALL_TEST_PROGRAMS are for
# the tests only: Windows reports those two folders whatever the environment says.

param(
    [string]$StartedFrom = ""
)

$ErrorActionPreference = "Stop"

$ExitFinished = 0
$ExitIncomplete = 1
$ExitRefused = 2
$ExitCancelled = 3
$ConfirmWord = "REMOVE"
$WhereStandard = "the standard location"
$LabelProgram = "the program"
$LabelUninstaller = "the uninstaller"
$RemoveAttempts = 3

if (-not $StartedFrom) {
    $originalRoot = Split-Path -Parent $PSScriptRoot
    $tempRoot = [IO.Path]::GetTempPath()
    $tempFolder = Join-Path $tempRoot ("biomnikhil-uninstall-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tempFolder | Out-Null
    $tempCopy = Join-Path $tempFolder "uninstall.ps1"
    Copy-Item -LiteralPath $PSCommandPath -Destination $tempCopy
    # A process's current folder can't be deleted, so neither process stays in it.
    Set-Location -LiteralPath $tempRoot
    [Environment]::CurrentDirectory = $tempRoot
    $engine = (Get-Process -Id $PID).Path
    & $engine -NoProfile -ExecutionPolicy Bypass -File $tempCopy -StartedFrom $originalRoot
    $childExitCode = $LASTEXITCODE
    Remove-Item -LiteralPath $tempFolder -Recurse -Force -ErrorAction SilentlyContinue
    exit $childExitCode
}

$script:Plan = New-Object System.Collections.ArrayList
$script:CleanupFolders = New-Object System.Collections.ArrayList
$script:Roots = New-Object System.Collections.ArrayList
$script:Ports = New-Object System.Collections.ArrayList
$script:SeenLakes = New-Object System.Collections.ArrayList
$script:OwnLakeParents = New-Object System.Collections.ArrayList
$script:SharedLakes = New-Object System.Collections.ArrayList
$script:Notes = New-Object System.Collections.ArrayList
$script:Failures = New-Object System.Collections.ArrayList


# ---- paths --------------------------------------------------------------------

function Add-Note([string]$Text) {
    [void]$script:Notes.Add($Text)
}

function Test-AbsolutePath([string]$Path) {  # C:\x or \\server\share, not C:x or \x
    if (-not $Path) { return $false }
    if ($Path -match '^[A-Za-z]:\\') { return $true }
    if ($Path -match '^\\\\[^\\?.]') { return $true }
    return $false
}

function Get-CleanPath([string]$Path) {  # no forward slashes, .. or trailing backslash; "" if unusable
    if (-not $Path) { return "" }
    $withBackslashes = $Path.Replace("/", "\")
    if (-not (Test-AbsolutePath $withBackslashes)) { return "" }
    try {
        $full = [IO.Path]::GetFullPath($withBackslashes)
    } catch {
        return ""
    }
    if ($full -notmatch '^[A-Za-z]:\\$') {
        $full = $full.TrimEnd("\")
    }
    return $full
}

function Get-LongPath([string]$Path) {  # the form that gets past the 260 character limit
    if ($Path.StartsWith("\\?\")) { return $Path }
    if ($Path.StartsWith("\\")) { return "\\?\UNC\" + $Path.Substring(2) }
    return "\\?\" + $Path
}

function Get-EntryAttributes([string]$Path) {  # the attributes, or $null if nothing is there
    try {
        return [IO.File]::GetAttributes((Get-LongPath $Path))
    } catch {
        return $null
    }
}

function Test-Entry([string]$Path) {  # only "not found" means gone: access denied may hide a file still there
    try {
        [void][IO.File]::GetAttributes((Get-LongPath $Path))
        return $true
    } catch [IO.FileNotFoundException] {
        return $false
    } catch [IO.DirectoryNotFoundException] {
        return $false
    } catch {
        return $true
    }
}

function Test-ReparsePoint([string]$Path) {  # a junction or symlink
    $attributes = Get-EntryAttributes $Path
    if ($null -eq $attributes) { return $false }
    return (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Test-Folder([string]$Path) {
    $attributes = Get-EntryAttributes $Path
    if ($null -eq $attributes) { return $false }
    return (($attributes -band [IO.FileAttributes]::Directory) -ne 0)
}

function Test-File([string]$Path) {
    $attributes = Get-EntryAttributes $Path
    if ($null -eq $attributes) { return $false }
    return (($attributes -band [IO.FileAttributes]::Directory) -eq 0)
}

function Test-Inside([string]$Path, [string]$Folder) {  # Path is Folder or something under it
    $trimmedFolder = $Folder.TrimEnd("\")
    if ($Path -ieq $trimmedFolder) { return $true }
    return $Path.StartsWith($trimmedFolder + "\", [StringComparison]::OrdinalIgnoreCase)
}

function Test-Protected([string]$Path) {  # a drive root, the home folder or a system folder
    if (-not $Path) { return $true }
    if ($Path -match '^[A-Za-z]:\\?$') { return $true }
    if ($Path -match '^\\\\[^\\]+(\\[^\\]+)?\\?$') { return $true }  # a server or a whole share
    if (Test-Inside $script:HomeFolder $Path) { return $true }  # home, or a folder that holds it

    $exactFolders = @(
        $env:LOCALAPPDATA, $env:APPDATA, $env:ProgramData, $env:PUBLIC,
        (Join-Path $script:HomeFolder "Desktop"), (Join-Path $script:HomeFolder "Documents"),
        (Join-Path $script:HomeFolder "Downloads"), (Join-Path $script:HomeFolder "AppData"),
        (Join-Path $script:HomeFolder "OneDrive"), $script:DesktopFolder, $script:ProgramsFolder
    )
    foreach ($folder in $exactFolders) {
        if ($folder -and ($Path -ieq $folder.TrimEnd("\"))) { return $true }
    }

    $systemFolders = @($env:SystemRoot, $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramW6432)
    foreach ($folder in $systemFolders) {
        if ($folder -and (Test-Inside $Path $folder)) { return $true }
    }
    return $false
}

function Add-ToPlan([string]$Path, [string]$Label) {
    [void]$script:Plan.Add([pscustomobject]@{ Path = $Path; Label = $Label })
}

function Add-CleanupFolder([string]$Folder) {  # once each, or a kept folder would be reported twice
    foreach ($known in $script:CleanupFolders) {
        if ($known -ieq $Folder) { return }
    }
    [void]$script:CleanupFolders.Add($Folder)
}

function Test-CoveredByPlan([string]$Path) {
    foreach ($item in $script:Plan) {
        if (Test-Inside $Path $item.Path) { return $true }
    }
    return $false
}


# ---- reading an install's settings ----------------------------------------------

function Read-EnvValue([string]$File, [string]$Key) {  # the value, as python-dotenv reads it
    if (-not (Test-File $File)) { return "" }
    $value = ""
    try {
        $lines = @(Get-Content -LiteralPath $File -Encoding UTF8)
    } catch {
        return ""
    }
    foreach ($line in $lines) {
        $match = [regex]::Match($line, '^\s*(export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
        if ($match.Success -and ($match.Groups[2].Value -eq $Key)) {
            $value = $match.Groups[3].Value
        }
    }
    $quoted = [regex]::Match($value, '^(["''])(.*?)\1')
    if ($quoted.Success) { return $quoted.Groups[2].Value }
    $withoutComment = [regex]::Replace($value, '\s+#.*$', '')
    return $withoutComment.Trim()
}

function Read-SavedLocation([string]$Root) {  # the parsed file, or $null
    $file = Join-Path $Root "data\datalake-location.json"
    if (-not (Test-File $file)) { return $null }
    try {
        return (Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json)
    } catch {
        Add-Note "Couldn't read $file. A data lake it names may remain."
        return $null
    }
}


# ---- what is an install -----------------------------------------------------------

function Test-RecognisedInstall([string]$Root) {
    $hasCore = Test-File (Join-Path $Root "app\installer\core.py")
    $hasConfig = Test-File (Join-Path $Root "app\config.py")
    if ($hasCore -and $hasConfig) { return $true }
    if (Test-File (Join-Path $Root "runtime\.venv\.biomnikhil-ready")) { return $true }
    if (Test-File (Join-Path $Root "data\datalake-location.json")) { return $true }
    return $false
}

function Get-GitTrackedFiles([string]$Root, [string[]]$Paths) {  # the list, or $null without a usable git
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return $null }
    # Variables like GIT_DIR would make git answer about some other repository.
    foreach ($name in @("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")) {
        [Environment]::SetEnvironmentVariable($name, $null)
    }
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $listed = & git -C $Root ls-files -- @Paths 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($null -eq $listed) { return ,@() }
    return ,@($listed)
}

function Test-InsideAnotherRepository([string]$Root) {  # a parent folder is a checkout tracking this folder
    $folder = Split-Path -Parent $Root
    while ($folder) {
        if (Test-Entry (Join-Path $folder ".git")) {
            $tracked = Get-GitTrackedFiles $Root @(".")
            if ($null -eq $tracked) { return $true }
            return ($tracked.Count -gt 0)
        }
        $folder = Split-Path -Parent $folder
    }
    return $false
}

function Add-InstallEntry([string]$Root, [string]$Name, [string]$Label) {
    $path = Join-Path $Root $Name
    if (Test-Entry $path) { Add-ToPlan $path $Label }
}

function Add-Checkout([string]$Root) {  # only data\ and runtime\, and only if git says they're untracked
    Add-Note "$Root is a git checkout: its source stays. Only untracked data\ and runtime\ can be removed."
    $tracked = Get-GitTrackedFiles $Root @("data", "runtime")
    if ($null -eq $tracked) {
        Add-Note "git isn't available to check $Root, so its data\ and runtime\ stay too."
        return
    }
    if ($tracked.Count -gt 0) {
        Add-Note "git tracks files in data\ or runtime\ of $Root, so they stay."
        return
    }
    Add-InstallEntry $Root "data" "settings, chats, uploads, logs, browser storage and any data lake inside"
    Add-InstallEntry $Root "runtime" "Python, uv and download cache"
    # setup.ps1 writes this at the top of a checkout too.
    $trackedLog = Get-GitTrackedFiles $Root @("setup.log")
    if (($null -ne $trackedLog) -and ($trackedLog.Count -eq 0)) {
        Add-InstallEntry $Root "setup.log" "setup log"
    }
}

function Add-ReleaseInstall([string]$Root) {
    Add-InstallEntry $Root "app" $LabelProgram
    Add-InstallEntry $Root "data" "settings, chats, uploads, logs, browser storage and any data lake inside"
    Add-InstallEntry $Root "runtime" "Python, uv and download cache"
    Add-InstallEntry $Root "uninstall-mac.command" $LabelUninstaller
    Add-InstallEntry $Root "uninstall-windows.cmd" $LabelUninstaller
    $launcherNames = @("setup.log", "setup-mac.command", "setup-windows.cmd",
        "LICENSE", "run-mac.sh", "run.ps1", "agent-ideas.md", "shared-data-lake.md")
    foreach ($name in $launcherNames) {
        Add-InstallEntry $Root $name "launcher or log"
    }
    # What releases up to v0.1.3 put at the top of the folder. The updater never removed these.
    $olderNames = @(".env", ".env.example", ".gitignore", ".venv", "README.md", "Start biomnikhil.cmd",
        "Start biomnikhil.command", "docs", "python", "requirements.lock", "requirements.txt", "scripts",
        "tests", "tools", "wsgi.py")
    foreach ($name in $olderNames) {
        Add-InstallEntry $Root $name "left by an older version"
    }
    # An update that stopped partway leaves its temporary copies.
    foreach ($entry in [IO.Directory]::GetFiles((Get-LongPath $Root), ".*.update")) {
        if (-not (Test-ReparsePoint $entry)) {
            Add-InstallEntry $Root (Split-Path -Leaf $entry) "left by an interrupted update"
        }
    }
    Add-CleanupFolder $Root
}

function Add-InstallIfFound([string]$Given, [string]$Where) {
    $root = Get-CleanPath $Given
    if (-not $root) { return }
    if (-not (Test-Folder $root)) { return }
    foreach ($known in $script:Roots) {
        if ($known -ieq $root) { return }
    }
    if (Test-ReparsePoint $root) {
        Add-Note "Left alone, because it's a junction or symlink: $root"
        return
    }
    if (Test-Protected $root) {
        Add-Note "Refused, because it's a drive root, the home folder or a system folder: $root"
        return
    }
    # The standard folder is ours by its name, so a setup or an uninstall that stopped
    # partway is still found. Only known names go, whatever else is in it.
    $recognised = ($Where -eq $WhereStandard) -or (Test-RecognisedInstall $root)
    if (-not $recognised) {
        Add-Note "Left alone, because it doesn't look like a biomnikhil install ($Where): $root"
        return
    }
    if (Test-Entry (Join-Path $root ".git")) {
        [void]$script:Roots.Add($root)
        Add-Checkout $root
        return
    }
    [void]$script:Roots.Add($root)
    if (Test-InsideAnotherRepository $root) {
        Add-Checkout $root
        return
    }
    Add-ReleaseInstall $root
}


# ---- data lakes -------------------------------------------------------------------

function Test-OwnLakeParent([string]$Parent) {
    foreach ($own in $script:OwnLakeParents) {
        if ($own -ieq $Parent) { return $true }
    }
    return $false
}

function Add-LakeContents([string]$LakeDir) {  # the app's own names only; anything else in the lake stays
    foreach ($name in @("data_lake", "benchmark", ".sizes.json")) {
        $path = Join-Path $LakeDir $name
        if (Test-Entry $path) { Add-ToPlan $path "data lake" }
    }
    Add-CleanupFolder $LakeDir
}

function Add-LakeIfFound([string]$Given, [string]$Source, [string]$Kind) {  # Kind: own, system or chosen
    if (-not $Given) { return }
    $parent = Get-CleanPath $Given
    if (-not $parent) {
        Add-Note "Ignored a data lake path that isn't absolute ($Source): $Given"
        return
    }
    if (-not (Test-Folder $parent)) {
        if ($Kind -ne "own") {
            Add-Note "Data lake folder not found, so nothing was removed there; reconnect its drive if it's still in use ($Source): $parent"
        }
        return
    }
    $lakeDir = Join-Path $parent "biomni_data"
    if (Test-ReparsePoint $lakeDir) {
        Add-Note "Left alone, because it's a junction or symlink: $lakeDir"
        return
    }
    if (-not (Test-Folder $lakeDir)) { return }
    if (Test-Protected $lakeDir) {
        Add-Note "Refused, because it's a protected folder: $lakeDir"
        return
    }
    if (Test-CoveredByPlan $lakeDir) { return }
    foreach ($seen in $script:SeenLakes) {
        if ($seen -ieq $lakeDir) { return }
    }
    [void]$script:SeenLakes.Add($lakeDir)

    # Checked before the look of the lake, because a merge can leave only .sizes.json.
    if (($Kind -ne "system") -and (Test-OwnLakeParent $parent)) {
        Add-LakeContents $lakeDir
        # A preset folder exists only for the lake. Its parent goes if nothing else is in it.
        if ($parent -ine $script:HomeFolder) {
            Add-CleanupFolder $parent
        }
        return
    }

    $recognised = $false
    foreach ($name in @("data_lake", "benchmark")) {
        $inner = Join-Path $lakeDir $name
        if ((Test-Folder $inner) -and -not (Test-ReparsePoint $inner)) { $recognised = $true }
    }
    if (-not $recognised) {
        Add-Note "Left alone, because it doesn't hold data_lake\ or benchmark\: $lakeDir"
        return
    }

    [void]$script:SharedLakes.Add([pscustomobject]@{
        LakeDir = $lakeDir
        Parent = $parent
        RemoveParent = ($Kind -eq "system")
    })
}

function Find-Lakes {
    $defaultParent = Join-Path $script:HomeFolder "biomnikhil-data"
    $appDataParent = Join-Path $env:LOCALAPPDATA "biomnikhil"
    $programData = $env:ProgramData
    if (-not $programData) { $programData = "C:\ProgramData" }
    $systemParent = Join-Path $programData "biomnikhil"

    foreach ($preset in @($defaultParent, $appDataParent)) {
        # A junction may lead to a shared drive, so what it points at needs its own prompt.
        if (-not (Test-ReparsePoint $preset)) {
            [void]$script:OwnLakeParents.Add((Get-CleanPath $preset))
        }
    }

    # Shared locations first, so a path saved as shared is never taken for an own one.
    # Most computers have no shared folder, so its absence isn't worth a note.
    if (Test-Folder $systemParent) {
        Add-LakeIfFound $systemParent "the shared folder preset" "system"
    }
    $savedByRoot = @{}
    foreach ($root in $script:Roots) {
        $saved = Read-SavedLocation $root
        $savedByRoot[$root] = $saved
        if ($null -eq $saved) { continue }
        if ($saved.kind -eq "system") {
            Add-LakeIfFound ([string]$saved.path) "the shared folder saved in the app" "system"
        } else {
            Add-LakeIfFound ([string]$saved.path) "the folder saved in the app" "chosen"
        }
    }
    foreach ($root in $script:Roots) {
        $saved = $savedByRoot[$root]
        if ($null -ne $saved) {
            foreach ($previous in @($saved.previous)) {
                if ($previous) { Add-LakeIfFound ([string]$previous) "a folder used earlier" "chosen" }
            }
        }
        $envFile = Join-Path $root "data\.env"
        Add-LakeIfFound (Read-EnvValue $envFile "BIOMNI_DATA_PATH") "BIOMNI_DATA_PATH in data\.env" "chosen"
        Add-LakeIfFound (Read-EnvValue (Join-Path $root ".env") "BIOMNI_DATA_PATH") "BIOMNI_DATA_PATH in an older version's .env" "chosen"
        foreach ($key in @("BIOMNI_UPLOAD_PATH", "BIOMNI_CHAT_DB", "BIOMNI_OUTPUT_PATH", "BIOMNI_INPUT_PATH", "BIOMNI_HARNESS_PATH")) {
            $value = Read-EnvValue $envFile $key
            if ($value -and -not (Test-Inside $value $root)) {
                Add-Note "Left alone, because data\.env points $key outside the install: $value"
            }
        }
    }
    Add-LakeIfFound $env:BIOMNI_DATA_PATH "the BIOMNI_DATA_PATH environment variable" "chosen"
    Add-LakeIfFound $defaultParent "the default location" "own"
    Add-LakeIfFound $appDataParent "the app data location" "own"
    foreach ($root in $script:Roots) {
        # A checkout whose data\ stays keeps the lake in it too.
        $dataFolder = Join-Path $root "data"
        if (Test-CoveredByPlan $dataFolder) {
            Add-LakeIfFound $dataFolder "the app folder" "own"
        }
    }
    # A merge empties a lake but leaves the folder around it.
    foreach ($preset in @($defaultParent, $appDataParent)) {
        Add-EmptiedPreset $preset
    }
}

function Add-EmptiedPreset([string]$Preset) {
    if (-not (Test-Folder $Preset)) { return }
    if (Test-ReparsePoint $Preset) { return }
    $lakeDir = Join-Path $Preset "biomni_data"
    foreach ($shared in $script:SharedLakes) {
        if ($shared.LakeDir -ieq $lakeDir) { return }  # not ours to tidy, and it's asked about on its own
    }
    Add-CleanupFolder $Preset
}


# ---- shortcuts --------------------------------------------------------------------

function Add-ShortcutIfOurs([string]$Path, [string]$ArgumentPattern, [string]$Label) {
    if (-not (Test-File $Path)) { return }
    if (Test-ReparsePoint $Path) {
        Add-Note "Left alone, because it's a symlink: $Path"
        return
    }
    try {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($Path)
        $arguments = [string]$shortcut.Arguments
    } catch {
        Add-Note "Left alone, because it couldn't be read: $Path"
        return
    }
    if ($arguments -match $ArgumentPattern) {
        Add-ToPlan $Path $Label
    } else {
        Add-Note "Left alone, because it isn't the shortcut biomnikhil made: $Path"
    }
}

function Add-InstallsNamedInShortcuts {  # an older install can be anywhere
    $shortcutPaths = @(
        (Join-Path $script:DesktopFolder "biomnikhil.lnk"),
        (Join-Path $script:ProgramsFolder "biomnikhil.lnk"),
        (Join-Path $script:ProgramsFolder "Uninstall biomnikhil.lnk")
    )
    foreach ($path in $shortcutPaths) {
        if (-not (Test-File $path)) { continue }
        if (Test-ReparsePoint $path) { continue }
        try {
            $shell = New-Object -ComObject WScript.Shell
            $arguments = [string]$shell.CreateShortcut($path).Arguments
        } catch {
            continue
        }
        $match = [regex]::Match($arguments, '-File\s+"([^"]*)\\app\\(run|uninstall)\.ps1"')
        if ($match.Success) {
            Add-InstallIfFound $match.Groups[1].Value "the folder a shortcut starts"
        }
    }
}

function Find-Shortcuts {
    $startPattern = '-File\s+"[^"]*\\app\\run\.ps1"'
    $uninstallPattern = '-File\s+"[^"]*\\app\\uninstall\.ps1"'
    Add-ShortcutIfOurs (Join-Path $script:DesktopFolder "biomnikhil.lnk") $startPattern "Desktop shortcut"
    Add-ShortcutIfOurs (Join-Path $script:ProgramsFolder "biomnikhil.lnk") $startPattern "Start menu shortcut"
    Add-ShortcutIfOurs (Join-Path $script:ProgramsFolder "Uninstall biomnikhil.lnk") $uninstallPattern "Start menu uninstaller"
}


# ---- is it running ----------------------------------------------------------------

function Get-RunningReason {  # why biomnikhil seems to be running; "" if it isn't
    foreach ($root in $script:Roots) {
        $port = Read-EnvValue (Join-Path $root "data\.env") "PORT"
        if ($port -notmatch '^\d+$') { $port = "8000" }
        try {
            $previousProgress = $ProgressPreference
            $ProgressPreference = "SilentlyContinue"
            $response = Invoke-WebRequest "http://127.0.0.1:$port/ping" -UseBasicParsing -TimeoutSec 2
            $ProgressPreference = $previousProgress
            if ($response.Content -match "biomnikhil") { return "it answers on port $port" }
        } catch {
            $ProgressPreference = "Continue"
        }
    }

    try {
        $processes = @(Get-CimInstance Win32_Process)
    } catch {
        return "the list of running programs could not be read"
    }
    foreach ($process in $processes) {
        if ($process.ProcessId -eq $PID) { continue }
        $commandLine = [string]$process.CommandLine
        $executable = [string]$process.ExecutablePath
        if ($commandLine -match 'uninstall\.ps1|uninstall-windows\.cmd') { continue }
        foreach ($root in $script:Roots) {
            $inCommand = $commandLine.IndexOf($root + "\", [StringComparison]::OrdinalIgnoreCase) -ge 0
            $isExecutable = $executable -and (Test-Inside $executable $root)
            if ($inCommand -or $isExecutable) {
                return "process $($process.ProcessId): $commandLine"
            }
        }
    }
    return ""
}

function Stop-IfRunning {
    $reason = Get-RunningReason
    if ($reason) {
        Write-Host ""
        Write-Host "biomnikhil is running, so nothing was removed ($reason)."
        Write-Host "Close the biomnikhil window, then run this again."
        exit $ExitRefused
    }
}


# ---- listing and asking -----------------------------------------------------------

function Get-TreeBytes([string]$Path) {  # never walks into a junction or symlink
    if (Test-ReparsePoint $Path) { return [long]0 }
    if (Test-File $Path) {
        try {
            return [long](New-Object IO.FileInfo (Get-LongPath $Path)).Length
        } catch {
            return [long]0
        }
    }
    $total = [long]0
    $pending = New-Object System.Collections.Stack
    $pending.Push($Path)
    while ($pending.Count -gt 0) {
        $folder = [string]$pending.Pop()
        try {
            $entries = [IO.Directory]::GetFileSystemEntries((Get-LongPath $folder))
        } catch {
            continue
        }
        foreach ($entry in $entries) {
            if (Test-ReparsePoint $entry) { continue }
            if (Test-Folder $entry) {
                $pending.Push($entry)
            } else {
                try {
                    $total += (New-Object IO.FileInfo $entry).Length
                } catch {
                    continue
                }
            }
        }
    }
    return $total
}

function Format-Size([long]$Bytes) {
    $culture = [Globalization.CultureInfo]::InvariantCulture
    if ($Bytes -ge 1073741824) { return ($Bytes / 1073741824).ToString("0.0", $culture) + " GB" }
    if ($Bytes -ge 1048576) { return ($Bytes / 1048576).ToString("0.0", $culture) + " MB" }
    return ([math]::Ceiling($Bytes / 1024)).ToString("0", $culture) + " KB"
}

function Show-Notes {
    foreach ($note in $script:Notes) {
        Write-Host "  - $note"
    }
}

function Show-Plan {
    Write-Host ""
    if (-not (Test-AnythingToRemove)) {
        Write-Host "Nothing would be removed without a separate confirmation."
        return
    }
    if ($script:Plan.Count -gt 0) {
        Write-Host "This will permanently delete:"
    }
    foreach ($item in $script:Plan) {
        $size = Format-Size (Get-TreeBytes $item.Path)
        Write-Host ("  {0,10}  {1}  ({2})" -f $size, $item.Path, $item.Label)
    }
    if ($script:CleanupFolders.Count -gt 0) {
        Write-Host "These folders go too, if nothing else is left in them:"
        foreach ($folder in $script:CleanupFolders) {
            Write-Host "  $folder"
        }
    }
}

function Test-AnythingToRemove {
    return (($script:Plan.Count -gt 0) -or ($script:CleanupFolders.Count -gt 0))
}

function Read-Answer {  # standard input, so the tests can answer too; "" at its end
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { return "" }
    return $line
}

function Confirm-SharedLakes {
    foreach ($shared in $script:SharedLakes) {
        $size = Format-Size (Get-TreeBytes $shared.LakeDir)
        Write-Host ""
        Write-Host "This data lake may be used by other people or computers, so it is kept unless you say otherwise:"
        Write-Host ("  {0,10}  {1}" -f $size, $shared.LakeDir)
        Write-Host "To delete its data lake files, type its full path. Press Enter to keep it."
        Write-Host -NoNewline "> "
        $answer = Read-Answer
        if ($answer -ceq $shared.LakeDir) {
            Add-LakeContents $shared.LakeDir
            if ($shared.RemoveParent) {
                Add-CleanupFolder $shared.Parent
            }
        } else {
            Add-Note "Kept: $($shared.LakeDir)"
        }
    }
}


# ---- deleting ---------------------------------------------------------------------

function Remove-Tree([string]$Path) {  # junctions and symlinks lose only the link; failures are collected
    $longPath = Get-LongPath $Path
    $attributes = Get-EntryAttributes $Path
    if ($null -eq $attributes) { return }
    $isFolder = (($attributes -band [IO.FileAttributes]::Directory) -ne 0)
    $isLink = (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)

    if ($isFolder -and -not $isLink) {
        try {
            $children = [IO.Directory]::GetFileSystemEntries($longPath)
        } catch {
            $children = @()
        }
        foreach ($child in $children) {
            Remove-Tree $child
        }
    }
    try {
        [IO.File]::SetAttributes($longPath, [IO.FileAttributes]::Normal)
        if ($isFolder) {
            # Not recursive: on a junction or symlink this removes only the link.
            [IO.Directory]::Delete($longPath, $false)
        } else {
            [IO.File]::Delete($longPath)
        }
    } catch {
        # Reported once at the top level, when the path is still there.
    }
}

function Test-SafeToDelete([string]$Path) {
    if ((Get-CleanPath $Path) -ine $Path) { return $false }
    if (-not $Path) { return $false }
    if (Test-Protected $Path) { return $false }
    if (-not (Test-Folder (Split-Path -Parent $Path))) { return $false }
    return $true
}

function Remove-Planned([string]$Path) {
    if (-not (Test-SafeToDelete $Path)) {
        [void]$script:Failures.Add("$Path (refused: not a path this uninstaller may delete)")
        return
    }
    # A window that just closed, or a virus scanner, can hold a file for a moment.
    $attempt = 1
    while ($attempt -le $RemoveAttempts) {
        Remove-Tree $Path
        if (-not (Test-Entry $Path)) { return }
        if ($attempt -lt $RemoveAttempts) { Start-Sleep -Seconds 2 }
        $attempt = $attempt + 1
    }
    [void]$script:Failures.Add($Path)
}

function Remove-IfEmpty([string]$Folder) {
    if (-not (Test-Folder $Folder)) { return }
    if (Test-ReparsePoint $Folder) { return }
    if (-not (Test-SafeToDelete $Folder)) { return }
    try {
        $remaining = @([IO.Directory]::GetFileSystemEntries((Get-LongPath $Folder)))
    } catch {
        [void]$script:Failures.Add($Folder)
        return
    }
    # The probe an older version's location picker left, and Finder's file, don't count.
    $others = New-Object System.Collections.ArrayList
    $ignorable = New-Object System.Collections.ArrayList
    foreach ($entry in $remaining) {
        $entryName = Split-Path -Leaf $entry
        if (($entryName -eq ".biomnikhil-write-test") -or ($entryName -eq ".DS_Store")) {
            [void]$ignorable.Add($entry)
        } else {
            [void]$others.Add($entry)
        }
    }
    if ($others.Count -eq 0) {
        foreach ($entry in $ignorable) {
            Remove-Tree $entry
        }
    }
    if ($others.Count -gt 0) {
        Add-Note "Kept because it holds files biomnikhil didn't create: $Folder"
        return
    }
    Remove-Tree $Folder
    if (Test-Entry $Folder) {
        [void]$script:Failures.Add($Folder)
    }
}


# ---- main -------------------------------------------------------------------------

function Get-KnownFolder([string]$TestVariable, [string]$FolderName) {
    $override = [Environment]::GetEnvironmentVariable($TestVariable)
    if ($override) { return (Get-CleanPath $override) }
    return (Get-CleanPath ([Environment]::GetFolderPath($FolderName)))
}

Set-Location -LiteralPath ([IO.Path]::GetTempPath())
[Environment]::CurrentDirectory = [IO.Path]::GetTempPath()

$script:HomeFolder = Get-CleanPath $env:USERPROFILE
if ((-not $script:HomeFolder) -or (-not (Get-CleanPath $env:LOCALAPPDATA)) -or ($script:HomeFolder -match '^[A-Za-z]:\\$')) {
    Write-Host "USERPROFILE or LOCALAPPDATA can't be used safely, so biomnikhil's files can't be found. Nothing was removed."
    exit $ExitRefused
}
$script:DesktopFolder = Get-KnownFolder "BIOMNIKHIL_UNINSTALL_TEST_DESKTOP" "Desktop"
$script:ProgramsFolder = Get-KnownFolder "BIOMNIKHIL_UNINSTALL_TEST_PROGRAMS" "Programs"

Write-Host "biomnikhil uninstaller"
Add-InstallIfFound (Join-Path $env:LOCALAPPDATA "biomnikhil") $WhereStandard
Add-InstallIfFound $StartedFrom "the folder this was started from"
Add-InstallsNamedInShortcuts
Find-Lakes
Find-Shortcuts

if ((-not (Test-AnythingToRemove)) -and ($script:SharedLakes.Count -eq 0)) {
    Write-Host ""
    Write-Host "No biomnikhil files were found, so nothing was removed."
    Show-Notes
    exit $ExitFinished
}

Stop-IfRunning

Show-Plan
if ($script:SharedLakes.Count -gt 0) {
    Confirm-SharedLakes
    Show-Plan
}
Write-Host ""
if ($script:Notes.Count -gt 0) {
    Write-Host "Not removed:"
    Show-Notes
    Write-Host ""
}
if (-not (Test-AnythingToRemove)) {
    Write-Host "Nothing to remove."
    exit $ExitFinished
}

Write-Host "This can't be undone. Type $ConfirmWord to delete everything listed above, or press Enter to cancel."
Write-Host -NoNewline "> "
$finalAnswer = Read-Answer
if ($finalAnswer -cne $ConfirmWord) {
    Write-Host "Cancelled. Nothing was removed."
    exit $ExitCancelled
}

Stop-IfRunning  # it could have been opened while this was waiting

# The uninstaller itself goes last, and only if the rest went, so that "run this
# again" still works after a failure.
foreach ($item in $script:Plan) {
    if (($item.Label -ne $LabelProgram) -and ($item.Label -ne $LabelUninstaller)) {
        Write-Host "Removing $($item.Path)"
        Remove-Planned $item.Path
    }
}
if ($script:Failures.Count -eq 0) {
    foreach ($item in $script:Plan) {
        if (($item.Label -eq $LabelProgram) -or ($item.Label -eq $LabelUninstaller)) {
            Write-Host "Removing $($item.Path)"
            Remove-Planned $item.Path
        }
    }
    # Deepest first, so a folder isn't reported as kept for what's still to go inside it.
    $deepestFirst = @($script:CleanupFolders | Sort-Object -Property Length -Descending)
    foreach ($folder in $deepestFirst) {
        Remove-IfEmpty $folder
    }
}

Write-Host ""
if ($script:Failures.Count -gt 0) {
    Write-Host "biomnikhil was NOT completely removed. These could not be deleted:"
    foreach ($failure in $script:Failures) {
        Write-Host "  - $failure"
    }
    Write-Host "Close anything using them, or ask an administrator if they need one, then run this again."
    exit $ExitIncomplete
}
Write-Host "biomnikhil was removed."
if ($script:Notes.Count -gt 0) {
    Write-Host "Not removed:"
    Show-Notes
}
exit $ExitFinished
