# ArcherGate 0.2.0 Workstation Alpha

## Run the workstation build

On Windows 10 or 11 x64, use the setup executable when supplied. The installer uses Microsoft WebView2 and can provision it offline. For the portable ZIP, extract the entire folder and open `ArcherGate.exe`. The portable edition uses WebView2 already present on the workstation. Keep the `payload` folder next to the executable. These local alpha files are unsigned.

The application creates its private workspace under `%LOCALAPPDATA%\com.archergate.medialab\workspace`. To open an existing workspace, use **Open workspace** and choose its folder. Workspaces are independent of the app installation directory. Preserve the complete workspace directory when backing it up.

1. Open **Materials**. Choose or drop a local media file, provide provenance and rights notes, then import. Compact and standard proxies limit picture size to 640×360 and 960×540 respectively.
2. Open the clip and select **Start edit from source**. Save the new edit in **Work** and render a preview.
3. Select two saved edits to render an A/B comparison. Switching revisions preserves the playhead. Accept or reject B to record the decision. The saved edits and source media remain available.
4. Use **Export Premiere XML** for frame-aligned edits without effects. For revisions with effects, use **Download rendered MP4**. This exports a rendered review copy instead of an editable effects timeline.
5. To enable optional CPU transcription, download the English model in **Lab**, then use **Transcribe English** on an imported clip. This model download is optional.

## Creative direction

The workstation includes an optional Claude adapter. It requires your own API key and per-request approval. Requests use the model selected in Work and can incur provider charges on your account. The API key is kept only for the active request. The request includes direction and selected clip timing. Still images, transcript excerpts and Director's Memory are separate opt-ins. The adapter caches approved responses locally and never sends source files or paths.

The local engine currently applies validated animated scale and position with linear, smoothstep or hold interpolation, plus audio gain. Independent picture and sound cuts continue to use the timeline editor. Speed ramps, transitions, color changes and additional model providers are unavailable in this alpha. The two local edit manifests stay independently editable. Claude proposals are data, with only supported operations accepted for rendering.

## Release engineering

Tauri hosts the existing local Python workspace in a native WebView2 window. The payload contains CPython's embeddable runtime and pinned Windows FFmpeg tools, so the media engine works without a separately installed Python. This avoids running a PyInstaller freeze for a standard-library service and avoids bundling a separate Chromium runtime. Source, derived files and credentials are outside the application installation. Tauri's local backend binds to a fresh loopback port and uses an ephemeral session cookie. [Python embedded distribution](https://docs.python.org/3/using/windows.html#embedded-distribution) · [Tauri Windows installers](https://v2.tauri.app/distribute/windows-installer/)

The package includes the runtime license notices for Python, FFmpeg, and whisper.cpp. FFmpeg's pinned Windows package is the Gyan 6.1.1 essentials build distributed under GPL v3. Its exact source reference and configuration are in the packaged FFmpeg README. Hauora Sans is included under SIL OFL 1.1. [Pinned FFmpeg source commit](https://github.com/FFmpeg/FFmpeg/commit/e38092ef93) · [Hauora Sans](https://github.com/WCYS-Co/Hauora-Sans)

On Windows x64 with the build prerequisites already configured, maintainers can run `scripts/build-workstation.ps1`. Generated files land in the ignored `artifacts` directory. On Linux, see the release evidence for what was built and tested. Do not use a deployed or public repository runner for private footage.

## Linux verification

- Synthetic J-cut and L-cut exports decoded with picture and audio. The first test interval contained audio in the J-cut and silence in the L-cut. Chrome switched between the composed edits at the same playhead position and recorded a rejection while returning to the base.
- A data-only push-in changed the decoded image. Source hashes and the base manifest matched their initial values. Unsupported operations and editable XML export of a revision with effects were rejected.
- The real MCP and browser regression workflows, CPU Whisper test, bounded context selection and private desktop backend session tests ran on Linux. Closing the parent pipe shut down the backend. The existing Experiment 001 verification passed again.
- A local Linux test imported an eight-second 1080p/24 fps synthetic clip in 6.57 seconds using a compact proxy. The composed preview took 3.87 seconds. Peak measured FFmpeg child RSS was 139.1 MiB. This measurement excludes the desktop window and represents one sample on the Codespace host.
- The approved interface image was unavailable. The current interface uses the requested concrete and olive palette, Hauora Sans and Work, Materials and Lab navigation. Visual fidelity to the reference remains unverified.
- No live Claude request, Windows workstation launch, Windows optional speech test or Premiere import was performed. Local consent and context bounds were tested without spending API credits. An actual natural-language revision requires an explicitly approved request using the workstation user's own API key.
