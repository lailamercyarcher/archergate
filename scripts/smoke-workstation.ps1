param(
    [Parameter(Mandatory)][string]$BuildDirectory,
    [Parameter(Mandatory)][string]$DiagnosticsDirectory
)

$ErrorActionPreference = 'Stop'
$dist = Join-Path $BuildDirectory 'distribution'
$verification = Get-Content -LiteralPath (Join-Path $dist 'verification.json') -Raw | ConvertFrom-Json
$folder = "ArcherGate-$($verification.app_version)"
$extract = Join-Path $BuildDirectory 'portable-smoke'
$installRoot = Join-Path $BuildDirectory 'installed-smoke'
$appRoot = Join-Path $extract $folder
$payload = Join-Path $appRoot 'payload'
$workspace = Join-Path $BuildDirectory 'smoke-workspace'

$data = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) $verification.bundle_identifier
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$($verification.product_name)"
foreach ($path in @($data, $uninstallKey, $extract, $installRoot, $workspace)) {
    if (Test-Path -LiteralPath $path) { throw "Use a fresh Windows profile and smoke directory. Found $path." }
}

$backendLog = Join-Path $data 'backend.log'
$result = [ordered]@{
    commit = $verification.commit
    app_version = $verification.app_version
    package_checksums = 'not_run'
    installed_payload = 'not_run'
    embedded_doctor = 'not_run'
    bundled_backend_session = 'not_run'
    native_app_process = 'not_run'
    authenticated_webview2_startup = 'not_run'
    native_file_picker = 'not_run'
    application_and_backend_shutdown = 'not_run'
}
$app = $null
$backend = $null
$stage = 'package_checksums'
try {
    foreach ($file in $verification.files) {
        $path = Join-Path $dist $file.name
        $checksum = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
        $sidecar = (Get-Content -LiteralPath "$path.sha256" -Raw).Trim()
        if ($checksum -ne $file.sha256 -or $sidecar -cne "$checksum  $($file.name)" -or
            (Get-Item -LiteralPath $path).Length -ne $file.bytes) {
            throw "Distribution metadata or SHA-256 check failed for $($file.name)."
        }
    }
    $result[$stage] = 'passed'

    $stage = 'installed_payload'
    Expand-Archive -LiteralPath (Join-Path $dist "$folder-win-x64-portable.zip") -DestinationPath $extract
    $setup = Join-Path $dist "$folder-win-x64-setup.exe"
    # NSIS requires an unquoted /D argument as the last item.
    $install = Start-Process -FilePath $setup -ArgumentList "/S /NS /D=$installRoot" -Wait -PassThru
    Write-Host "NSIS installer exit code $($install.ExitCode)"
    if ($install.ExitCode -ne 0) { throw 'The alpha installer failed.' }
    $installed = Get-ItemProperty -LiteralPath $uninstallKey
    if ($installed.DisplayName -ne $verification.product_name -or $installed.DisplayVersion -ne $verification.app_version) {
        throw 'The installer registered an unexpected product name or version.'
    }
    $portableFiles = @(Get-Item -LiteralPath (Join-Path $appRoot 'ArcherGate.exe')) +
        @(Get-ChildItem -LiteralPath $payload -File -Recurse)
    foreach ($file in $portableFiles) {
        $relative = [IO.Path]::GetRelativePath($appRoot, $file.FullName)
        if ($relative -eq 'ArcherGate.exe') { $relative = "$($verification.main_binary_name).exe" }
        $installedFile = Join-Path $installRoot $relative
        $portableHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $installedFile).Hash -ne $portableHash) {
            throw "Installed file differs from the verified portable ZIP: $relative"
        }
    }
    $result[$stage] = 'passed'
    Write-Host "NSIS installed all $($portableFiles.Count) portable executable and payload files byte for byte."

    $stage = 'embedded_doctor'
    $env:ARCHERGATE_FFMPEG = Join-Path $payload 'media-tools/ffmpeg.exe'
    $env:ARCHERGATE_FFPROBE = Join-Path $payload 'media-tools/ffprobe.exe'
    $env:ARCHERGATE_TEST_PYTHON = Join-Path $payload 'python/python.exe'
    & $env:ARCHERGATE_TEST_PYTHON -m archergate.studio --workspace $workspace doctor
    if ($LASTEXITCODE -ne 0) { throw 'The embedded Python / FFmpeg doctor failed.' }
    $whisper = Join-Path $payload 'speech-tools/whisper-cli.exe'
    & $whisper --help 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'The packaged whisper.cpp executable did not start.' }
    $result[$stage] = 'passed'

    $stage = 'bundled_backend_session'
    & node (Join-Path $PSScriptRoot '../tests/studio/desktop-backend.mjs')
    if ($LASTEXITCODE -ne 0) { throw 'The packaged backend session test failed.' }
    $result[$stage] = 'passed'

    if (-not [Environment]::UserInteractive -or (Get-Process -Id $PID).SessionId -eq 0) {
        foreach ($check in @('native_app_process', 'authenticated_webview2_startup', 'native_file_picker',
                'application_and_backend_shutdown')) {
            $result[$check] = 'OWNER SMOKE TEST REQUIRED'
        }
        $result['gui_limitation'] = 'The Windows job has no interactive desktop session.'
        Write-Host 'OWNER SMOKE TEST REQUIRED: the Windows job has no interactive desktop session.'
        return
    }

    $stage = 'native_app_process'
    $null = New-Item -ItemType Directory -Path $data
    @{ workspace = $workspace } | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $data 'workspace.json') -Encoding utf8NoBOM
    $app = Start-Process -FilePath (Join-Path $appRoot 'ArcherGate.exe') -WorkingDirectory $appRoot -PassThru
    $result['native_app_pid'] = $app.Id
    if ($app.HasExited) { throw "The desktop executable exited at launch with code $($app.ExitCode)." }
    $result[$stage] = 'passed'

    $stage = 'authenticated_webview2_startup'
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([DateTime]::UtcNow -lt $deadline -and -not $app.HasExited) {
        $app.Refresh()
        if ($app.MainWindowHandle -ne 0 -and (Test-Path -LiteralPath $backendLog)) {
            $log = Get-Content -LiteralPath $backendLog -Raw
            if ($log -match '"GET /app\.js HTTP/1\.1" 200' -and $log -match '"GET /api/timelines HTTP/1\.1" 200') {
                $result[$stage] = 'passed'
                break
            }
        }
        Start-Sleep -Milliseconds 500
    }
    if ($result[$stage] -ne 'passed') { throw 'Native window and authenticated WebView2 startup were not observed.' }
    $child = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($app.Id) AND Name = 'python.exe'"
    if (-not $child) { throw 'Could not identify the embedded Python child process.' }
    $backend = Get-Process -Id $child.ProcessId
    Write-Host 'Native window and authenticated WebView2 startup passed.'

    $stage = 'native_file_picker'
    $fixture = Join-Path $BuildDirectory 'Synthetic picker clip.mp4'
    & $env:ARCHERGATE_FFMPEG -nostdin -v error -f lavfi -i 'color=blue:s=160x90:r=24:d=1' -c:v libx264 $fixture
    if ($LASTEXITCODE -ne 0) { throw 'The synthetic picker fixture could not be generated.' }
    # Windows PowerShell provides the .NET Framework UI Automation client assemblies.
    & powershell.exe -NoProfile -Mta -File (Join-Path $PSScriptRoot 'verify-native-picker.ps1') -AppPid $app.Id -Fixture $fixture
    if ($LASTEXITCODE -ne 0) { throw 'The Windows native file picker smoke failed.' }
    $result[$stage] = 'passed'

    $stage = 'application_and_backend_shutdown'
    if (-not $app.CloseMainWindow() -or -not $app.WaitForExit(15000) -or $app.ExitCode -ne 0) {
        throw 'The native workstation did not close cleanly.'
    }
    if (-not $backend.WaitForExit(10000)) { throw 'The embedded backend survived workstation closure.' }
    $result[$stage] = 'passed'
    Write-Host 'The native window and its child backend shut down cleanly.'
} catch {
    $result[$stage] = 'failed'
    $result['error'] = $_.Exception.Message
    throw
} finally {
    $result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $DiagnosticsDirectory 'smoke.json') -Encoding utf8
    if ($app -and -not $app.HasExited) { $app.Kill($true) }
    if ($backend -and -not $backend.HasExited) { $backend.Kill() }
    if (Test-Path -LiteralPath $backendLog) {
        Copy-Item -LiteralPath $backendLog -Destination (Join-Path $DiagnosticsDirectory 'backend.log')
    }
}
