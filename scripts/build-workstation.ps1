$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

function Check-NativeExit {
    if ($LASTEXITCODE -ne 0) { throw "Build command failed with code $LASTEXITCODE." }
}

py -3.14 scripts/prepare-workstation.py
Check-NativeExit

# This is the maintainer build command, from a Windows x64 machine
# with Rust MSVC, Visual Studio Build Tools and Node already set up.
npx --yes --package=@tauri-apps/cli@2.12.6 tauri build --target x86_64-pc-windows-msvc --ci --no-sign -- --locked
Check-NativeExit

py -3.14 scripts/assemble-workstation.py
Check-NativeExit

$installer = Get-ChildItem src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/*-setup.exe |
    Select-Object -First 1
if (-not $installer) { throw 'The Tauri NSIS setup executable is missing.' }
$dest = Join-Path $PWD "artifacts/$($installer.Name)"
Copy-Item $installer.FullName $dest
$hash = (Get-FileHash -Algorithm SHA256 $dest).Hash.ToLowerInvariant()
Set-Content "${dest}.sha256" "$hash  $($installer.Name)" -Encoding ascii
Write-Output $dest
