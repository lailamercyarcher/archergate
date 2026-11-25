# ArcherGate cinematic workspace

The application runs on your computer and stores media, transcripts, timelines and creative notes in a private workspace. The default is `workspace/studio` inside this checkout. For production, put that workspace in a private folder outside the checkout with `--workspace` before the command. Back up that folder in its entirety after stopping the application.

## Start on Windows

Use Python 3.14 and a Windows build of FFmpeg that includes ffprobe and libx264. Unpack the binaries into `.tools` in this checkout, use their directory on PATH, or set the two paths in PowerShell. The browser and CLI use the Python standard library. The MCP server additionally uses the locked requirements.

```powershell
py -3.14 -m venv .venv
$env:ARCHERGATE_FFMPEG = 'C:\Tools\ffmpeg\bin\ffmpeg.exe'
$env:ARCHERGATE_FFPROBE = 'C:\Tools\ffmpeg\bin\ffprobe.exe'
.\.venv\Scripts\python.exe -m archergate.studio doctor
.\.venv\Scripts\python.exe -m archergate.studio demo
.\.venv\Scripts\python.exe -m archergate.studio launch
```

For double-click launching with PATH or the unpacked binaries in `.tools`, run `scripts\launch-studio.cmd`. Leave its window open while editing. Press Ctrl+C to stop the server. Windows execution and Premiere import require a workstation check.

## Start on Linux or Codespaces

On Ubuntu 24.04 x86-64 in Codespaces, use the existing workspace-local setup. On another Linux environment with Python 3.14 and a suitable FFmpeg on PATH, use the system's Python and the same module commands.

```bash
./scripts/setup
.venv/bin/python -m archergate.studio doctor
.venv/bin/python -m archergate.studio demo
.venv/bin/python -m archergate.studio serve
./scripts/verify-studio
./scripts/verify
```

Run the verification commands in another terminal or after stopping the server. `demo` expects a workspace without timelines. In Codespaces, forward port `8020` privately and open it from the Ports tab. On the workstation, `launch` starts the loopback server and opens the default browser.

## Work with footage and creative direction

- Paste an absolute media path into **Media library** and add provenance and a rights note. The path is on the computer running ArcherGate. In Codespaces, it refers to the Codespace's files.
- The import retains a byte-identical managed original and produces a browser proxy. Search by title, provenance or rights note. Open a clip to see its waveform, click likely shot boundaries, or select a speech segment.
- Use **Director’s Memory** for film and scene references, music cues, aesthetic notes, example footage and timestamped annotations. Enter creative direction as ordinary text. Suggestion records retain their accepted, rejected and modified decisions. Observed media data lives in the asset record separately.
- In **Timelines**, inspect the two demo variants or edit the JSON. Give a new variant its own ID before saving. Click a clip to inspect its source at the selected time. Compare saved variants and review the reported changes. Review suggestions describe evidence and leave the manifest untouched.

### Optional English speech

Supply a local whisper.cpp `whisper-cli` and an English model, such as `ggml-tiny.en.bin`. Set `ARCHERGATE_WHISPER` and `ARCHERGATE_WHISPER_MODEL` to their absolute paths before starting the server or importing at the CLI. The backend uses CPU-only English transcription and stores timestamped segments locally. An audio track with no configured backend reports **unavailable**. A failed execution reports **failed**. Completed analysis can return an empty segment list. Segment boundaries and wording are machine estimates. [whisper.cpp](https://github.com/ggml-org/whisper.cpp)

To exercise the real speech check, set those two variables and set `ARCHERGATE_TEST_SPEECH_WAV` to a locally synthesized WAV containing the word “documentary” and at least three seconds of English dialogue. Then run `./scripts/verify-studio`. The core verification has no speech model dependency.

## Timeline and Premiere handoff

Version 2 uses a rational number of seconds per tick in `timebase` (for example `"1/24"`) and a rational sequence frame rate in `fps` (for example `"24"` or `"30000/1001"`). Each track has an ID and a `kind` of `video` or `audio`. Each clip has a stable ID, an asset ID, timeline tick `at`, `source_start` measured from the asset's playable origin and `duration` in ticks. Ranges exclude their end. A later start leaves a gap. Place simultaneous sounds on separate tracks. A shared optional `sync` ID lets review identify sound that leads picture (J-cut) or carries past picture (L-cut).

1. Select a saved timeline and click **Export Premiere XML**, or run `python -m archergate.studio export interview-j` from the configured environment. XML copies are stored under `exports/` in the workspace.
2. Open a scratch project in Premiere Pro and use **File > Import** to select the XML. Open the imported sequence and inspect its picture and sound tracks, gaps and cut offsets.
3. If clips appear offline, use **Link Media** and locate the corresponding files in the workspace's `media/<asset-id>/original/` folders, or locate your byte-identical source files. Verify the first and last frames around each cut. [Adobe Premiere Pro reference](https://helpx.adobe.com/content/dam/help/en/pdf/premiere_pro_reference.pdf) · [Adobe Link Media guide](https://helpx.adobe.com/premiere/desktop/organize-media/ingest-proxy-workflow/relink-offline-clips.html)

The exporter writes Final Cut Pro 7 xmeml v5. XML is validated programmatically in Linux. Actual Premiere import, relinking, multichannel interpretation and native Windows launch still need verification. Export requires frame-aligned timeline and source points and representable rates. Ambiguous variable-rate material can be browsed as a proxy and may be refused for interchange. The browser compares editable timelines and plays source proxies. It does not play a composed multitrack timeline. [Apple xmeml specification](https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/FinalCutPro_XML/Basics/Basics.html)

## MCP and dependencies

Install the existing `requirements.lock` into the Python environment to use MCP. Configure your client to run the checkout's Python from the repository root with `-m archergate.mcp_server --studio <absolute-workspace>`. The five added tools search assets, inspect a transcript, retrieve creative preferences, validate a proposed timeline and export a saved timeline to a new XML file. Import and preference edits happen in the CLI or browser. Existing Premiere projects are outside those operations.

The application reuses the existing FFmpeg build and MCP SDK. The browser has no runtime packages or remote assets. The OpenTimelineIO FCP adapter was considered. The Python XML writer covers this limited track-and-clip subset without adding its native runtime, and the exporter rejects positions that cannot be represented exactly. [OpenTimelineIO FCP adapter](https://github.com/OpenTimelineIO/otio-fcp-adapter)

For a reproducible spoken fixture with a local eSpeak NG executable, run:

```bash
espeak-ng -v en-us -s 140 -w synthetic-dialogue.wav \
  "This is an original documentary test. The voice in this clip is synthetic."
```

Keep the WAV in the private workspace and point `ARCHERGATE_TEST_SPEECH_WAV` at it. [eSpeak NG](https://github.com/espeak-ng/espeak-ng)
