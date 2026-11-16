$ErrorActionPreference = 'Stop'
$LauncherArgs = @($args)
$env:FLOW_BASE_WORKSPACE = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$env:FLOW_BASE_HOME = Join-Path $env:FLOW_BASE_WORKSPACE 'share_states'
[System.IO.Directory]::CreateDirectory($env:FLOW_BASE_HOME) | Out-Null
[System.IO.Directory]::CreateDirectory((Join-Path $env:FLOW_BASE_WORKSPACE 'share_logs')) | Out-Null
$LaunchId = 'launch_' + [guid]::NewGuid().ToString('N')
$env:FLOW_BASE_LAUNCH_ID = $LaunchId
$LaunchPhase = 'bootstrap'
$CommandExitCode = $null
$LaunchLogDir = Join-Path $env:FLOW_BASE_WORKSPACE 'share_logs\launcher'
$LaunchStateDir = Join-Path $env:FLOW_BASE_HOME 'launcher'
[System.IO.Directory]::CreateDirectory($LaunchLogDir) | Out-Null
[System.IO.Directory]::CreateDirectory($LaunchStateDir) | Out-Null
$LaunchLog = Join-Path $LaunchLogDir ($LaunchId + '.log')
$LaunchState = Join-Path $LaunchStateDir ($LaunchId + '.json')
$TranscriptActive = $false
function Set-FlowLaunchState {
    param([string] $Status)
    $Payload = @{launch_id=$LaunchId; status=$Status; phase=$LaunchPhase; exit_code=$CommandExitCode; log=$LaunchLog; updated_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json
    [System.IO.File]::WriteAllText($LaunchState, $Payload, [System.Text.UTF8Encoding]::new($false))
}
trap {
    $Failure = $_
    try { Set-FlowLaunchState 'failed' } catch {}
    Write-Host '啟動失敗。請複製以下訊息及日誌給維護者；排除問題後重新雙擊即可重試。' -ForegroundColor Red
    Write-Host ($Failure | Out-String)
    Write-Host $Failure.ScriptStackTrace
    Write-Host ('啟動日誌：' + $LaunchLog)
    if ($TranscriptActive) { try { Stop-Transcript | Out-Null } catch {} }
    exit 1
}
Start-Transcript -Path $LaunchLog | Out-Null
$TranscriptActive = $true
Set-FlowLaunchState 'preparing'
Write-Host 'flow_base：正在檢查並準備環境，首次啟動可能需要下載工具。'
$env:UV_CACHE_DIR = Join-Path $env:FLOW_BASE_HOME 'packages'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $env:FLOW_BASE_HOME 'python'
$env:UV_NO_MODIFY_PATH = '1'
$env:UV_NO_PROGRESS = '1'
$env:UV_NO_ENV_FILE = '1'
$env:UV_LINK_MODE = 'copy'
$env:PYTHONDONTWRITEBYTECODE = '1'
$Arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$Bootstrap = Join-Path $env:FLOW_BASE_HOME ('bootstrap\Windows-' + $Arch)
function Select-FlowUv {
    param([string[]] $Candidates)
    foreach ($Candidate in $Candidates) {
        if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { continue }
        try {
            $VersionOutput = @(& $Candidate --version 2>$null)
            if (($LASTEXITCODE -eq 0) -and (($VersionOutput -join "`n") -match '^uv\s+\d+\.\d+\.\d+')) {
                return [string] $Candidate
            }
        } catch { continue }
    }
    return $null
}
$KnownUV = @(Get-Command uv -CommandType Application -All -ErrorAction SilentlyContinue)
$PrivateUV = Join-Path $Bootstrap 'uv.exe'
$UvExe = Select-FlowUv -Candidates @($KnownUV | ForEach-Object { $_.Source })
if (-not $UvExe) { $UvExe = Select-FlowUv -Candidates @($PrivateUV) }
if (-not $UvExe) {
    Write-Host 'Preparing pinned uv 0.13.6 binary from PyPI...'
    [System.IO.Directory]::CreateDirectory($Bootstrap) | Out-Null
    $Archive = Join-Path $Bootstrap ('uv-' + [guid]::NewGuid().ToString() + '.zip')
    $UvTemp = $Archive + '.exe'
    $Arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($Arch) {
        "AMD64" { $UvUrl = 'https://files.pythonhosted.org/packages/7c/9d/714523188aea3054644d8e69ae8089aac70325a3cee2d3d05509d4f23398/uv-0.13.6-py3-none-win_amd64.whl'; $UvHash = '7c9bc35ccb65932d8dc10a0669f9c805eaa3c9827ce18ecbecb1bd67944b2cdf' }
        "ARM64" { $UvUrl = 'https://files.pythonhosted.org/packages/6a/7d/5ebb4cccbb11ae9e10f6cda704ce382722ab9dc68c9280a5b810378aee3e/uv-0.13.6-py3-none-win_arm64.whl'; $UvHash = '722c7f96803f115e53918b8ee8858d1680ea6077c614b6db750747d709ab8d9a' }
        default { throw ('Unsupported bootstrap architecture: ' + $Arch) }
    }
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $UvUrl -OutFile $Archive -TimeoutSec 120
        if ((Get-FileHash -Algorithm SHA256 $Archive).Hash.ToLowerInvariant() -ne $UvHash) { throw 'uv integrity check failed.' }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $Zip = [IO.Compression.ZipFile]::OpenRead($Archive)
        try {
            $Entry = $Zip.GetEntry('uv-0.13.6.data/scripts/uv.exe')
            if (-not $Entry) { throw 'uv executable missing from wheel.' }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($Entry, $UvTemp, $true)
            Move-Item -Force $UvTemp $PrivateUV
            $LicenseText = ''
            foreach ($Item in $Zip.Entries) {
                if ($Item.FullName -like 'uv-0.13.6.dist-info/licenses/*') {
                    $Reader = New-Object IO.StreamReader($Item.Open())
                    try { $LicenseText += $Reader.ReadToEnd() + "`n" } finally { $Reader.Dispose() }
                }
            }
            Set-Content -Path (Join-Path $Bootstrap 'UV-LICENSES.txt') -Value $LicenseText -Encoding UTF8
        } finally { $Zip.Dispose() }
    } finally {
        Remove-Item $Archive -ErrorAction SilentlyContinue
        Remove-Item $UvTemp -ErrorAction SilentlyContinue
    }
    $UvExe = Select-FlowUv -Candidates @($PrivateUV)
    if (-not $UvExe) { throw 'Downloaded uv did not pass its executable check. Existing .venv has not been moved.' }
}
Write-Host ('Using uv executable: ' + $UvExe)
$env:PATH = (Split-Path $UvExe) + [IO.Path]::PathSeparator + $env:PATH
$Wheel = Join-Path $PSScriptRoot 'flow_base-0.2.10-py3-none-any.whl'
$Actual = (Get-FileHash -Algorithm SHA256 $Wheel).Hash.ToLowerInvariant()
if ($Actual -ne '78d51508789ce0d2738ee741e0bf7930236bbfa7cf6a29f324f42b67d1f8ac05') { throw 'Runtime package integrity check failed.' }
$Marker = Join-Path $env:FLOW_BASE_HOME 'base-environment'
$Venv = Join-Path $env:FLOW_BASE_WORKSPACE '.venv'
$Python = Join-Path $Venv 'Scripts\python.exe'
$PythonCheck = 'import pathlib,sys; sys.exit(0 if sys.version_info[:2] == (3,12) and pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.argv[1]).resolve() else 1)'
$RuntimeCheck = 'import flow_base,flow_base.cli,sys; sys.exit(0 if flow_base.__version__ == sys.argv[1] else 1)'
function Test-FlowPython {
    if (-not (Test-Path -LiteralPath $Python)) { return $false }
    try {
        & $Python -I -c $PythonCheck $Venv 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
function Test-FlowRuntime {
    if (-not (Test-FlowPython)) { return $false }
    try {
        & $Python -I -c $RuntimeCheck '0.2.10' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
$Expected = $Actual + '|' + $env:FLOW_BASE_WORKSPACE + '|Windows|' + $env:PROCESSOR_ARCHITECTURE
$Saved = if (Test-Path $Marker) { (Get-Content -Raw $Marker).Trim() } else { '' }
if (($Saved -ne $Expected) -or (-not (Test-FlowRuntime))) {
    $Lock = Join-Path $env:FLOW_BASE_HOME 'bootstrap.lock'
    if (Test-Path $Lock) { throw 'Another bootstrap is active. If interrupted, remove share_states/bootstrap.lock and retry.' }
    New-Item -ItemType Directory -Path $Lock -ErrorAction Stop | Out-Null
    try {
        $Creating = Join-Path $env:FLOW_BASE_HOME 'base-creating'
        if ((Test-Path $Venv) -and ((Get-Item -Force $Venv).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Refusing to replace a symbolic-link .venv.'
        }
        if (Test-Path -LiteralPath $Venv) {
            if (-not (Test-Path -LiteralPath (Join-Path $Venv 'pyvenv.cfg'))) {
                throw '.venv is not a recognized virtual environment. Move that directory aside before retrying.'
            }
            $BackupRoot = Join-Path $env:FLOW_BASE_HOME 'retired_envs'
            [System.IO.Directory]::CreateDirectory($BackupRoot) | Out-Null
            $Backup = Join-Path $BackupRoot ('venv-' + [guid]::NewGuid().ToString())
            Move-Item -LiteralPath $Venv -Destination $Backup -ErrorAction Stop
            Write-Host ('Previous environment files preserved at: ' + $Backup)
        }
        Write-Host 'Creating local .venv from the pinned runtime package...'
        Set-Content -Path $Creating -Value 'creating' -Encoding ASCII
        & $UvExe venv --no-config --no-project --python 3.12 $Venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
        if (-not (Test-FlowPython)) { throw 'Expected local .venv with Python 3.12; refusing to install into another interpreter.' }
        & $UvExe pip install --no-config --python $Python --no-build $Wheel
        if ($LASTEXITCODE -ne 0) { throw 'Runtime installation failed.' }
        if (-not (Test-FlowRuntime)) { throw 'Installed runtime did not pass its Python/version/import check.' }
        Set-Content -Path $Marker -Value $Expected -Encoding UTF8
        Remove-Item $Creating
    } finally { Remove-Item $Lock -ErrorAction SilentlyContinue }
}
Set-FlowLaunchState 'ready'
Write-Host '環境準備完成，進入 flow_base。'
# Command execution: keep noninteractive output inside the launch transcript.
if (($LauncherArgs.Count -eq 1) -and ($LauncherArgs[0] -eq '--bootstrap-only')) {
    Stop-Transcript | Out-Null
    $TranscriptActive = $false
    exit 0
}
if ($LauncherArgs.Count -eq 0) { $LauncherArgs = @('chat') }
$Interactive = ($LauncherArgs -contains 'chat') -or (($LauncherArgs -contains 'launch') -and
    ($LauncherArgs -notcontains '--prepare-only') -and ($LauncherArgs -notcontains '--action'))
$LaunchPhase = 'command'
Set-FlowLaunchState 'running'
Write-Host ('啟動日誌：' + $LaunchLog)
if ($Interactive) {
    Write-Host '互動輸入不錄製；執行錯誤另存至 share_logs 中的執行紀錄。'
    Stop-Transcript | Out-Null
    $TranscriptActive = $false
}
$SavedErrorPreference = $ErrorActionPreference
$SavedNativePreference = $PSNativeCommandUseErrorActionPreference
try {
    # Native stderr is diagnostic output, not a PowerShell terminating error.
    $ErrorActionPreference = 'Continue'
    $PSNativeCommandUseErrorActionPreference = $false
    $global:LASTEXITCODE = 1
    if ($Interactive) {
        & $Python -I -m flow_base --workspace $env:FLOW_BASE_WORKSPACE @LauncherArgs
    } else {
        # Force rendering before Stop-Transcript; include stderr on Windows PS 5.1.
        & $Python -I -m flow_base --workspace $env:FLOW_BASE_WORKSPACE @LauncherArgs 2>&1 |
            ForEach-Object { Write-Host ([string]$_) }
    }
    $CommandExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $SavedErrorPreference
    $PSNativeCommandUseErrorActionPreference = $SavedNativePreference
    if ($TranscriptActive) {
        Write-Host ('flow_base exit code: ' + $CommandExitCode)
        Stop-Transcript | Out-Null
        $TranscriptActive = $false
    }
}
if ($CommandExitCode -eq 0) { Set-FlowLaunchState 'completed' } else { Set-FlowLaunchState 'failed' }
exit $CommandExitCode
