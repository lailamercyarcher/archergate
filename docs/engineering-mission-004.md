# Mission 004 engineering record

## Editorial commands

The browser, Studio CLI and Studio MCP share `operations.edit` for source-range insertion, trim, split, move and transcript passage insertion. It builds the draft from a saved manifest, checks the operation and its targets, and uses the existing timeline validation and SQLite revision transaction. Proposals read and verify referenced originals. A new commit checks them again and atomically saves the manifest and its command receipt. An exact commit retry returns the saved receipt. Inserted and split clip IDs are deterministic for a given revision ID, and older saved clip IDs and receipts remain readable.

Run the official SDK-based stdio server against an operator-selected studio directory. Use a Python environment with `requirements.lock` installed. The workstation's embedded interpreter serves the desktop backend.

```sh
.venv/bin/python -m archergate.mcp_server --studio workspace/studio
.venv/bin/python -m archergate.mcp_server --studio workspace/studio --studio-write
```

The first invocation exposes inspection and proposal tools. With `--studio-write`, the operator authorizes that server instance and its client to commit editorial commands to that workspace for the lifetime of the process. `prepare_edit` gives the operator a reviewable draft. `commit_edit` independently validates the supplied command and can be called directly by a write-enabled client. The existing XML tool still writes an export file.

1. Call `inspect_revision` with `revision_id`. The result includes the saved manifest, its canonical SHA-256, a validation report with duration, and the parent ID and fingerprint for a child revision.
2. Call `inspect_source` with `asset_id`. Each registered stream has an exact start and duration in rational seconds relative to the probed container origin.
3. Call `prepare_edit` with `operation` and `command`. Each command requires `base_id`, `parent_sha256` from inspection, and a new `revision_id`. The proposal includes the prospective manifest, its SHA-256 and validation report. Its revision ID is available to other writers until commit.
4. Send the identical arguments to `commit_edit` and inspect the new revision. Its ID also serves as the retry key. For a fresh edit or changed inputs, use a fresh revision ID.

For `insert_source_range`, include `asset_id`, `track_id`, `source_start`, `duration`, and `at`. For `trim_clip`, include `clip_id`, `edge` (`in` or `out`), and `amount`. Split and move both take `clip_id` and `at`, which sets the split tick or new start on the same track. For `insert_passage`, send `asset_id`, `track_id`, the zero-based `segment` from `inspect_transcript`, and `at`. Tick inputs are JSON integers in the parent timebase. Source intervals are half-open. Compute one frame in ticks as `1 / (timebase × fps)` and use it when exactly representable.

Each edit names an immutable parent and creates a separate saved revision. A fingerprint mismatch rejects a stale request. Two intentional edits from the same immutable parent can create siblings. MCP commits preserve the browser's current selection. Reload the sequence list and select the new revision to review it.

The CLI uses the same command field names. The authenticated browser API supports revision inspection at `GET /api/timeline/revision?id=<revision-id>`. Its insert and trim endpoints also accept `parent_sha256` for callers that recorded it. The existing browser requests take a parent fingerprint from the server-side snapshot.

```sh
.venv/bin/python -m archergate.studio --workspace workspace/studio inspect-revision base
.venv/bin/python -m archergate.studio --workspace workspace/studio inspect-source "$ASSET_ID"
.venv/bin/python -m archergate.studio --workspace workspace/studio prepare-edit insert_source_range request.json
.venv/bin/python -m archergate.studio --workspace workspace/studio commit-edit insert_source_range request.json
```

Set `ASSET_ID` to the source's registered identifier before using the CLI example.

## Windows x64 test builds

An approved push to `mission/004-editing-acceptance` starts the **Windows x64 development alpha** workflow. The runner checks out the triggering commit and builds with pinned Python, Node, Rust MSVC and Tauri versions, the Windows image’s CMake and C++ tools, and Cargo’s lockfile. The app and NSIS installer use the **ArcherGate Alpha** identity and `0.3.0-alpha.1` version. The artifact name identifies the source commit, run and attempt. [GitHub runner images](https://github.com/actions/runner-images)

On a local Windows x64 build host, run the same packaging script in PowerShell. With no output argument it creates a new `artifacts/test-build-*` directory. A supplied output path must be new. The script uses the Python launcher by default. Pass `-PythonExecutable` to select a Python 3.14 x64 executable.

```powershell
./scripts/build-workstation.ps1 -OutputDirectory 'C:\work\archergate-test-build'
```

The job runs the Studio and Chromium regressions, verifies both package checksums, and checks the NSIS installation against the extracted ZIP byte for byte. The smoke runs the bundled tools and authenticates to the packaged backend. On an interactive desktop, it also launches the portable application, checks WebView2 startup, selects a synthetic file via Windows UI Automation, and verifies shutdown. A runner without an interactive desktop records **OWNER SMOKE TEST REQUIRED**. The build publishes the package artifact after the required steps pass and preserves its diagnostic logs on failure. See [the alpha download and verification steps](windows-development-alpha.md).

## Workroom editing

Select a segment on the timeline, drag its body to move it on the same track, or drag an edge inward to trim it. Place the playhead with the timeline slider and use **split selected clip at playhead**. A move that overlaps another clip on the same track is rejected. The saved parent remains available after each edit. Finish timing edits before applying fades or volume automation. Splitting requires a clip without timed effects.

In Materials, select a row under **english transcript** to seek to its start. Use **insert selected passage into sequence** to insert at the current timeline playhead on the chosen destination track. The revision stores the asset ID and the timestamps rounded to editable ticks within the destination stream. A completed local transcript is required.

## Transitions and audio automation

Use the existing `apply-operations` command or the consent-controlled creative revision workflow. An operation targets a clip by its ID and supplies an `effects` object. `fade_in` and `fade_out` specify durations in clip ticks. A picture fade reveals a lower video track or black. Place the incoming picture on a higher track and fade it in over the outgoing picture to make a dissolve. Overlap two audio tracks and apply complementary fades to make a crossfade. Set picture and audio overlap positions independently.

Audio `volume_points` takes up to 24 ordered `{"tick": ..., "gain_db": ...}` points starting at clip tick zero. Gain interpolates linearly in dB through the points and holds the last value. A dip and recovery in a music envelope can leave room for dialogue. Static `gain_db` can be applied alongside the envelope. Use rendered MP4 to carry these effects. Editable XML remains available for the base revision.

## External filmmaker session

Give one filmmaker a fresh workspace, a synthetic speaking clip, a B-roll clip and a music file. Record the app commit, OS, task time, whether each task succeeded without help, and any exact error or confusing UI label. Ask them to narrate what they expect, then give prompts without specifying controls.

| Task | Success evidence |
| --- | --- |
| Import the speaking, B-roll and music files and start a sequence | Three assets appear with provenance and rights, and the sequence has playable picture and sound |
| Add a B-roll range, split it, move one piece and shorten its end | The changes appear in the timeline, and the rendered preview matches the intended cut |
| Add a spoken passage from the transcript | The selected line is recognizable in playback at the chosen edit point |
| Export and reopen the cut | They locate the MP4 and XML downloads and reopen the same saved revision |

Record outcome, elapsed time, observed behavior and the participant's words for each issue. This session is pending an external participant.

## Observed results

On 2026-11-25, the live remote branch `test/media-correctness-harness` reported `95877e43265676c2d2bf3200f312d943016114be`. The starting local commit was `c36fc5dc20e3d68c109b062e7747a3cdbf3ce4ab`. The dedicated branch carries that commit and the in-progress MCP and packaging work.

- The follow-up branch audit found no remote-only commits. The separate Workroom reliability commit `10b85ff` remains on `fix/workroom-v3-reliability`. Mission 004 already carries a tested A/B control layout and versioned IBM Plex assets. The core and Studio verifiers passed independently. The updated Chromium test started import from the empty workspace, downloaded the rendered cut, stopped the backend, and launched a new authenticated browser context against the same workspace. Selecting the saved revision from the sequence list played the same MP4 bytes after restart.
- The full studio regression passed 29 Python tests with local whisper.cpp enabled. Chromium reopened the exact exported edit, rejected a corrupted preview with HTTP 404, and recovered the same MP4 bytes and an exact HTTP 206 range. A separate failed encode left the existing preview and checksum intact.
- Chromium selected a clip, placed the playhead with the slider, split at tick 84, dragged the right clip to tick 96 and trimmed three ticks with its edge handle. A drag into an occupied interval was rejected. The speech test selected a real whisper.cpp transcript row, checked its registered source hash and timestamps, and decoded audio from the rendered passage.
- Decoded video at 24 fps verified the picture dissolve began at frame 24, reached the midpoint at frame 36 and left unrelated picture unchanged. The outgoing fade reached its midpoint at frame 84. Independently timed AAC samples contained both tones at half amplitude midway through the crossfade. Later samples matched the expected −6 dB and −12 dB automation gains.
- The official MCP SDK repeated the insert, trim, split and move commands over stdio, agreed with authenticated HTTP revisions, rejected an overlapping move, and reopened all six revisions in a separate client process. The core regression passed five Python tests and Chromium playback, seeking and byte-range checks. Both historical ZIP copies and the published installer matched their saved SHA-256 checksums.
- The Windows workflow passed `actionlint`, and PowerShell parsed the package build, native smoke and picker scripts. Windows package generation and native picker results require a real Windows run. The external filmmaker session remains pending.

Earlier verification on this work is retained below.

- The initial checkout was clean at `c36fc5d` on `test/media-correctness-harness`, one commit ahead of its remote tracking branch. Outside the sandbox, the baseline studio suite ran 26 Python tests with 25 passing and the optional speech test skipped. Chromium studio, manual editing and desktop-backend checks passed. The core suite passed five Python tests and the Chromium playback, seeking and byte-range checks.
- The browser already dispatched insert and trim to the revision engine. The CLI and Studio MCP had no entry points for these manual operations. The checkout had a Windows packaging script and no GitHub Actions workflow. Native Windows behavior was untested here.
- The final studio suite ran 27 Python tests with 26 passing and the optional speech test skipped under the default environment. That speech test passed separately with the local whisper.cpp executable, model and synthetic dialogue. Chromium A/B, the manual source editing and export journey, and desktop backend shutdown passed. The final core suite passed all five Python tests and its Chromium checks.
- The official MCP SDK negotiated protocol `2025-11-25`. Separate MCP and authenticated HTTP workspaces produced identical canonical revisions for picture insertion, sound insertion and a one-frame picture trim. A new stdio process retrieved the saved manifests and parent fingerprints. The temporary workspace had four timeline rows and three command receipts after the valid and invalid requests.
- FFprobe reported 48 H.264 frames at 24 fps, AAC audio and a duration of 2 seconds. The decoded luminance showed 24 black frames, 23 white frames, and a final black frame after the trim. The two AAC samples measured 329.0 Hz and 549.0 Hz. All four synthetic fixture hashes matched their managed originals in each workspace after the edits and render.
- `actionlint`, PowerShell's parser, the pinned Tauri CLI configuration schema, Python and JavaScript syntax, and `git diff --check` passed. The Linux packaging audit used an already-built x64 executable and a staged payload. The new ZIP passed CRC and SHA-256 verification and contained all 25 required payload files. The assembler rejected an existing ZIP, a missing payload and a non-PE input.
- The initial and final local copies of all three historical Windows packages matched by SHA-256. The Windows job and native smoke still require a Windows runner.

## Failure log

| Command or check | Observed result | Cause and retest |
| --- | --- | --- |
| `./scripts/verify-studio` and `./scripts/verify` in the sandbox | Studio had two errors. Core had one error. | The sandbox denied loopback socket creation, and both official SDK initializations timed out there. The same commands passed with approved local execution outside the sandbox. |
| Release tool download probes with `curl -fsSI` in the sandbox | Both returned exit 6. | The sandbox could not resolve `github.com`. The approved downloads succeeded and the local copies were checked against release checksums before use. |
| Portable `pwsh -NoProfile -File parse.ps1` on Linux | Startup failed with exit 126, then 134. | The extracted executable needed its execute bit. PowerShell then tried to create a cache in the read-only home directory. With the execute bit set and its XDG data, configuration and cache directed to temporary directories, all four PowerShell scripts parsed. |
