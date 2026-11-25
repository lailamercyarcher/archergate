param(
    [string]$OutputDirectory,
    [string]$PythonExecutable = 'py'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$target = 'x86_64-pc-windows-msvc'
$tauri = @('--yes', '--package=@tauri-apps/cli@2.12.6', 'tauri')
$python = @()
if ($PythonExecutable -eq 'py') { $python = @('-3.14') }

function Invoke-BuildCommand([string]$Executable, [string[]]$Arguments) {
    Write-Host "> $Executable $($Arguments -join ' ')"
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE." }
}

if (-not $OutputDirectory) {
    $id = "$(Get-Date -Format 'yyyyMMdd-HHmmss')-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    $OutputDirectory = Join-Path $root "artifacts/test-build-$id"
}
if (Test-Path -LiteralPath $OutputDirectory) {
    throw "Select a fresh output directory. This path already exists: $OutputDirectory"
}
$build = New-Item -ItemType Directory -Path $OutputDirectory
$dist = New-Item -ItemType Directory -Path (Join-Path $build.FullName 'distribution')
$previousTarget = $env:CARGO_TARGET_DIR
$env:CARGO_TARGET_DIR = Join-Path $build.FullName 'target'

Push-Location $root
try {
    Invoke-BuildCommand $PythonExecutable ($python + @('-c',
        'import struct, sys; print(sys.version); sys.exit(0 if sys.version_info[:2] == (3, 14) and struct.calcsize(''P'') == 8 else ''Python 3.14 x64 required'')'))
    Invoke-BuildCommand 'node.exe' @('--version')
    Invoke-BuildCommand 'npx.cmd' @('--version')
    Invoke-BuildCommand 'cmake.exe' @('--version')
    Invoke-BuildCommand 'cargo.exe' @('--version')
    $rust = Invoke-BuildCommand 'rustc.exe' @('-vV')
    $rust | Write-Host
    $targets = Invoke-BuildCommand 'rustup.exe' @('target', 'list', '--installed')
    if ($rust -notcontains "host: $target" -or $targets -notcontains $target) {
        throw "Install the x64 Rust MSVC toolchain and target $target."
    }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    $visualStudio = Invoke-BuildCommand $vswhere @('-latest', '-products', '*', '-version', '[17.0,18.0)',
        '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationVersion')
    if (-not $visualStudio) { throw 'Visual Studio 2022 with the x64 C++ build tools is required.' }
    Write-Host "Visual Studio version $visualStudio"
    Invoke-BuildCommand 'npx.cmd' ($tauri + @('--version'))

    Invoke-BuildCommand $PythonExecutable ($python + @('scripts/prepare-workstation.py'))
    Invoke-BuildCommand 'npx.cmd' ($tauri + @('build', '--target', $target, '--ci', '--no-sign', '--', '--locked'))

    $config = Get-Content -LiteralPath 'src-tauri/tauri.conf.json' -Raw | ConvertFrom-Json
    $version = $config.version
    $release = Join-Path $env:CARGO_TARGET_DIR "$target/release"
    $exe = Get-Item -LiteralPath (Join-Path $release 'archergate.exe')
    $embeddedVersion = $exe.VersionInfo.ProductVersion
    if ($embeddedVersion -ne $version -and $embeddedVersion -ne "$version.0") {
        throw "Expected application version $version, found $embeddedVersion in $($exe.Name)."
    }
    $installer = @(Get-ChildItem -LiteralPath (Join-Path $release 'bundle/nsis') -Filter '*-setup.exe' -File)
    if ($installer.Count -ne 1 -or $installer[0].Length -eq 0) {
        throw 'Expected one nonempty Tauri NSIS installer in the fresh target directory.'
    }

    Invoke-BuildCommand $PythonExecutable ($python + @('scripts/assemble-workstation.py',
        '--exe', $exe.FullName, '--output-dir', $dist.FullName))
    $setup = Join-Path $dist.FullName $installer[0].Name
    [IO.File]::Copy($installer[0].FullName, $setup)
    $setupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $setup).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$setup.sha256" -Value "$setupHash  $($installer[0].Name)" -Encoding ascii
    $portable = Join-Path $dist.FullName "ArcherGate-$version-win-x64-portable.zip"
    $portableHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $portable).Hash.ToLowerInvariant()
    if ((Get-Content -LiteralPath "$portable.sha256" -Raw).Trim() -ne "$portableHash  $(Split-Path $portable -Leaf)") {
        throw 'Portable ZIP checksum verification failed.'
    }
    $commit = Invoke-BuildCommand 'git.exe' @('rev-parse', 'HEAD')
    $status = Invoke-BuildCommand 'git.exe' @('status', '--porcelain')
    @{
        commit = $commit
        dirty_checkout = [bool]$status
        workflow_run = $env:GITHUB_RUN_ID
        workflow_attempt = $env:GITHUB_RUN_ATTEMPT
        runner_image = $env:ImageVersion
        target = $target
        app_version = $version
        executable_version = $embeddedVersion
        portable_zip_crc = 'passed'
        required_payload = 'passed'
        files = @(
            @{ name = $installer[0].Name; sha256 = $setupHash; bytes = (Get-Item -LiteralPath $setup).Length }
            @{ name = Split-Path $portable -Leaf; sha256 = $portableHash; bytes = (Get-Item -LiteralPath $portable).Length }
        )
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $dist.FullName 'verification.json') -Encoding utf8
    Write-Output $dist.FullName
} finally {
    Pop-Location
    $env:CARGO_TARGET_DIR = $previousTarget
}
