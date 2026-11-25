param(
    [Parameter(Mandatory)][string]$BuildDirectory,
    [Parameter(Mandatory)][string]$DiagnosticsDirectory
)

$ErrorActionPreference = 'Stop'
$dist = Join-Path $BuildDirectory 'distribution'
$verification = Get-Content -LiteralPath (Join-Path $dist 'verification.json') -Raw | ConvertFrom-Json
$folder = "ArcherGate-$($verification.app_version)"
$extract = Join-Path $BuildDirectory 'portable-smoke'
if (Test-Path -LiteralPath $extract) { throw 'The portable smoke directory already exists.' }
Expand-Archive -LiteralPath (Join-Path $dist "$folder-win-x64-portable.zip") -DestinationPath $extract
$appRoot = Join-Path $extract $folder
$payload = Join-Path $appRoot 'payload'

# Tauri resolves this through the Windows known folder API.
$data = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'com.archergate.medialab'
if (Test-Path -LiteralPath $data) { throw "Use a Windows user with no existing ArcherGate app data at $data." }
$null = New-Item -ItemType Directory -Path $data
$workspace = Join-Path $BuildDirectory 'smoke-workspace'
@{ workspace = $workspace } | ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $data 'workspace.json') -Encoding utf8NoBOM
$backendLog = Join-Path $data 'backend.log'

# Exercise the embedded interpreter and bundled media binaries separately from the native window.
$env:ARCHERGATE_FFMPEG = Join-Path $payload 'media-tools/ffmpeg.exe'
$env:ARCHERGATE_FFPROBE = Join-Path $payload 'media-tools/ffprobe.exe'
& (Join-Path $payload 'python/python.exe') -m archergate.studio --workspace $workspace doctor
if ($LASTEXITCODE -ne 0) { throw 'The embedded Python / FFmpeg doctor failed.' }
Write-Host 'Embedded Python and FFmpeg/FFprobe doctor passed.'

$fixture = Join-Path $BuildDirectory 'Synthetic picker clip.mp4'
& $env:ARCHERGATE_FFMPEG -nostdin -v error -f lavfi -i 'color=blue:s=160x90:r=24:d=1' -c:v libx264 $fixture
if ($LASTEXITCODE -ne 0) { throw 'The synthetic picker fixture could not be generated.' }

$app = Start-Process -FilePath (Join-Path $appRoot 'ArcherGate.exe') -WorkingDirectory $appRoot -PassThru
$backend = $null
try {
    $started = $false
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([DateTime]::UtcNow -lt $deadline -and -not $app.HasExited) {
        $app.Refresh()
        if ($app.MainWindowHandle -ne 0 -and (Test-Path -LiteralPath $backendLog)) {
            $log = Get-Content -LiteralPath $backendLog -Raw
            if ($log -match '"GET /app\.js HTTP/1\.1" 200' -and $log -match '"GET /api/timelines HTTP/1\.1" 200') {
                $started = $true
                break
            }
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $started) { throw 'Native window and authenticated WebView2 startup requests were not observed.' }
    $child = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($app.Id) AND Name = 'python.exe'"
    if (-not $child) { throw 'Could not identify the embedded Python child process.' }
    $backend = Get-Process -Id $child.ProcessId
    # Windows PowerShell provides the .NET Framework UI Automation client assemblies.
    & powershell.exe -NoProfile -Mta -File (Join-Path $PSScriptRoot 'verify-native-picker.ps1') -AppPid $app.Id -Fixture $fixture
    if ($LASTEXITCODE -ne 0) { throw 'The Windows native file picker smoke failed.' }
    if (-not $app.CloseMainWindow() -or -not $app.WaitForExit(15000) -or $app.ExitCode -ne 0) {
        throw 'The native workstation did not close cleanly.'
    }
    if (-not $backend.WaitForExit(10000)) { throw 'The embedded backend survived workstation closure.' }
    @{
        embedded_doctor = 'passed'
        native_portable_launch = 'passed'
        authenticated_webview2_startup = 'passed'
        native_file_picker = 'passed'
        application_and_backend_shutdown = 'passed'
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $DiagnosticsDirectory 'smoke.json') -Encoding utf8
    Write-Host 'Native portable window, authenticated WebView2 startup, file picker and backend shutdown passed.'
} finally {
    if (-not $app.HasExited) { $app.Kill($true) }
    if ($backend -and -not $backend.HasExited) { $backend.Kill() }
    if (Test-Path -LiteralPath $backendLog) {
        Copy-Item -LiteralPath $backendLog -Destination (Join-Path $DiagnosticsDirectory 'backend.log')
    }
}
