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

function Assert-PackageIdentity($File, $Config) {
    $info = $File.VersionInfo
    if ($info.ProductVersion -ne $Config.version -or $info.FileVersion -ne $Config.version -or
        $info.ProductName -ne $Config.productName) {
        throw "Unexpected Windows version metadata in $($File.Name): $($info.ProductName) $($info.ProductVersion) ($($info.FileVersion))."
    }
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
    $commit = Invoke-BuildCommand 'git.exe' @('rev-parse', 'HEAD')
    $status = Invoke-BuildCommand 'git.exe' @('status', '--porcelain')
    if ($env:GITHUB_ACTIONS -eq 'true' -and ($commit -ne $env:GITHUB_SHA -or $status)) {
        throw 'CI must build the exact source commit from a clean checkout.'
    }
    $config = Get-Content -LiteralPath 'src-tauri/tauri.conf.json' -Raw | ConvertFrom-Json
    $version = $config.version
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
    Invoke-BuildCommand 'npx.cmd' ($tauri + @('--version'))

    Invoke-BuildCommand $PythonExecutable ($python + @('scripts/prepare-workstation.py'))
    # This alpha ships one installer format and uses the same executable in the portable ZIP.
    Invoke-BuildCommand 'npx.cmd' ($tauri + @('build', '--target', $target, '--ci', '--no-sign',
        '--no-binary-patching', '--', '--locked'))

    $release = Join-Path $env:CARGO_TARGET_DIR "$target/release"
    $exe = Get-Item -LiteralPath (Join-Path $release "$($config.mainBinaryName).exe")
    Assert-PackageIdentity $exe $config
    $installer = @(Get-ChildItem -LiteralPath (Join-Path $release 'bundle/nsis') -Filter '*-setup.exe' -File)
    if ($installer.Count -ne 1 -or $installer[0].Length -eq 0) {
        throw 'Expected one nonempty Tauri NSIS installer in the fresh target directory.'
    }
    Assert-PackageIdentity $installer[0] $config

    Invoke-BuildCommand $PythonExecutable ($python + @('scripts/assemble-workstation.py',
        '--exe', $exe.FullName, '--output-dir', $dist.FullName))
    $setupName = "ArcherGate-$version-win-x64-setup.exe"
    $setup = Join-Path $dist.FullName $setupName
    [IO.File]::Copy($installer[0].FullName, $setup)
    $setupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $setup).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$setup.sha256" -Value "$setupHash  $setupName" -Encoding ascii
    $portable = Join-Path $dist.FullName "ArcherGate-$version-win-x64-portable.zip"
    $portableHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $portable).Hash.ToLowerInvariant()
    if ((Get-Content -LiteralPath "$portable.sha256" -Raw).Trim() -ne "$portableHash  $(Split-Path $portable -Leaf)") {
        throw 'Portable ZIP checksum verification failed.'
    }
    if ((Invoke-BuildCommand 'git.exe' @('rev-parse', 'HEAD')) -ne $commit -or
        ($env:GITHUB_ACTIONS -eq 'true' -and (Invoke-BuildCommand 'git.exe' @('status', '--porcelain')))) {
        throw 'The source checkout changed during the build.'
    }
    @{
        commit = $commit
        dirty_checkout = [bool]$status
        workflow_run = $env:GITHUB_RUN_ID
        workflow_attempt = $env:GITHUB_RUN_ATTEMPT
        workflow_url = if ($env:GITHUB_ACTIONS -eq 'true') {
            "$env:GITHUB_SERVER_URL/$env:GITHUB_REPOSITORY/actions/runs/$env:GITHUB_RUN_ID"
        }
        runner_image = $env:ImageVersion
        target = $target
        app_version = $version
        bundle_identifier = $config.identifier
        product_name = $config.productName
        main_binary_name = $config.mainBinaryName
        executable_version = $exe.VersionInfo.ProductVersion
        installer_version = $installer[0].VersionInfo.ProductVersion
        portable_zip_crc = 'passed'
        required_portable_payload = 'passed'
        packaged_engine_source_bytes = 'matched'
        files = @(
            @{ name = $setupName; sha256 = $setupHash; bytes = (Get-Item -LiteralPath $setup).Length }
            @{ name = Split-Path $portable -Leaf; sha256 = $portableHash; bytes = (Get-Item -LiteralPath $portable).Length }
        )
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $dist.FullName 'verification.json') -Encoding utf8
    Write-Output $dist.FullName
} finally {
    Pop-Location
    $env:CARGO_TARGET_DIR = $previousTarget
}
