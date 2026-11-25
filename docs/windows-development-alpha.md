# ArcherGate 0.3.0-alpha.1 for Windows x64

This development alpha uses its own Windows installer and app data identity. Start in a fresh workspace with synthetic or disposable media for the first test.

## Download and verify

1. Open the successful **Windows x64 development alpha** run in the repository’s **Actions** tab. Under **Artifacts**, download the `archergate-win-x64-…` archive named with the source commit and run attempt.
2. Extract the Actions archive. It contains `ArcherGate-0.3.0-alpha.1-win-x64-setup.exe`, `ArcherGate-0.3.0-alpha.1-win-x64-portable.zip`, a `.sha256` sidecar for each, and `verification.json`. Match the source commit in `verification.json` to the workflow run.
3. Open PowerShell in the extracted folder and verify both packages.

   ```powershell
   Get-ChildItem -File *.sha256 | ForEach-Object {
       $expected = (Get-Content -LiteralPath $_.FullName -Raw).Trim()
       $name = $_.BaseName
       $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $name).Hash.ToLowerInvariant()
       if ($expected -cne "$actual  $name") { throw "SHA-256 mismatch for $name" }
       Write-Output "Verified $name"
   }
   ```

4. To run the portable build, extract `ArcherGate-0.3.0-alpha.1-win-x64-portable.zip` and open `ArcherGate.exe` in the extracted folder. Keep the `payload` folder beside the executable. The portable app uses the installed Microsoft Edge WebView2 Runtime. The NSIS installer can provision that runtime.

The Actions archive includes `smoke.json`. It reports installer payload verification, embedded tool execution, authenticated backend requests and shutdown, native process launch, WebView2 initialization, and the native file picker. If it displays **OWNER SMOKE TEST REQUIRED**, open the portable build on Windows, choose a synthetic clip through **import footage**, and confirm the filename appears in the import form. Import it, start a sequence, play the rendered preview, then close the window.

The bundled whisper.cpp executable uses a separately downloaded English model. The optional Studio MCP server runs with the SDK dependencies from `requirements.lock` in an external Python environment, as described in the Mission 004 engineering record.
