# Editorial engine hardening — architecture decision and current slice

## Observed architecture

- `Library` owns the workspace SQLite database and managed media tree. Asset IDs are random 32-character hex strings; the registered original's SHA-256 is stored with its provenance, rights, and probed metadata.
- Timeline IDs are the primary keys for immutable v2/v3 JSON manifests. Time is represented as integer ticks plus rational `timebase`/`fps`; source and timeline intervals are half-open.
- `operations.apply`, source-range insertion, and one-frame trim validate an edit and commit a new immutable timeline row with a command receipt. Revision lineage is available through the parent ID stored with that receipt.
- Previews are rendered to a temporary directory and moved to a SHA-256-named MP4. The key includes the manifest and source hashes, but not an explicit renderer/tool identity or full settings. An existing path is trusted without checking its content or decoding it. A failed render before the move leaves an existing output in place; concurrent requests can race to replace the same key.
- Import crosses the filesystem and SQLite: it stages/copies source and derivatives, renames the asset directory, then inserts the asset row, compensating for an SQLite failure. An abrupt process exit between those steps can leave an orphan directory. XML exports write a unique file directly.
- HTTP media work is synchronous. The desktop server's process-local lock serializes selected routes, but CLI/MCP callers and multiple backend processes do not share it. A process exit loses in-flight work; no durable job state or restart recovery exists.
- Timeline inspection validates manifests, registered asset references, and track/source ranges, but does not hash original files. Revision commits, previews, and XML export verify original hashes. Resource bounds include 24 tracks, 500 clips per track, 48 preview clips, ten-minute previews, 1 MiB JSON requests, and a 30-minute subprocess timeout.
- Tests include decoded synthetic audiovisual assertions for exact multitrack timing, fractional frame rates, audio gaps, picture content, source hashes, actual MCP calls, import/export behavior, and the manual source-range workflow. The A/B controls now occupy separate hit areas and are exercised with real Chromium clicks.

## Highest-risk gaps

1. **Observed:** preview cache hits trust any existing file at the expected key; a truncated or substituted file can be reported as a successful render. Cache identity also omits explicit tool/renderer versions and render settings.
2. **Resolved:** revision IDs are retry keys for insertion, effects, and one-frame trim; retries return the saved result and conflicting reuse fails.
3. **Resolved:** revision commits verify every referenced original and reject missing or changed managed media before saving.
4. **Theoretical failure window:** abrupt termination between asset-directory rename and SQLite insert can leave orphaned media. The ordinary SQLite error path compensates, but process death cannot run that compensation.
5. **Observed limitation:** media processing is synchronous and has no persisted job/progress/recovery state. The desktop lock is process-local and does not provide durable or cross-process coordination.

## Decision

The existing immutable revision operations (`insert_source_range`, `apply`, and `trim_clip`) use a narrow SQLite-backed commit receipt and the current timeline schema. The caller-selected `revision_id` is the retry key; the receipt binds it to the operation, canonical input fingerprint, parent ID/hash, resulting revision hash, and original validation report. Insert the revision and receipt in one `BEGIN IMMEDIATE` transaction after validating its full manifest and verifying referenced managed source hashes. A retry with the same request returns the stored report; reuse with different inputs, a changed parent, or a modified stored revision fails explicitly.

`Library.verify_original` centralizes the existing source-integrity check and is reused by revision commits, preview rendering, and XML export. No new dependency or persistence service is introduced. Existing timelines and manifests remain readable and unchanged; the command table is created idempotently for existing workspaces.

## Alternatives rejected

- A generic command bus or event-sourcing framework: not justified by the two current editing operations.
- Mutable “current timeline” pointers or implicit ripple/overwrite behavior: unnecessary; revisions remain explicit branches from immutable parent IDs.
- Redis, Celery, or another job service: contrary to the local-first design and premature before a single durable preview-job slice is scoped.
- Treating a filename or cache key as proof of output integrity: contradicted by the observed cache-hit behavior.

## Scope, verification, and remaining work

The editor now supports source proxy playback/seeking, integer-tick in/out marks, picture/audio track selection, playhead placement, authenticated source-range insertion, one-frame non-ripple trim, rendering/playback, XML/MP4 download, and returning to a saved parent revision. A real Chromium test exercises the visible workflow against a fresh desktop-authenticated local service using generated media with Unicode/space paths. Decoded assertions check the expected trimmed picture frame and inserted tone; source hashes are checked before and after.

Still needed: preview identity/sidecar metadata and cache verification; staged render validation and concurrency control; durable preview jobs and restart recovery; read-only workspace diagnostics/orphan detection; broader filesystem-failure testing; and native Windows/WebView2 testing. The desktop bridge and picker were simulated in Chromium; native Windows file picking, Tauri launch/close, and actual Premiere XML import were not tested in this Linux environment.

The `./scripts/verify-studio` workflow now includes the browser A/B interaction test, the desktop-bridge manual editing journey, and the desktop backend shutdown test. The browser test retains the original show-A/show-B/accept/reject selectors; it verifies the two control regions do not overlap rather than weakening the interaction assertions.

## Next engineering slice

Harden preview artifacts before adding background jobs: canonicalize cache inputs with renderer/tool versions and settings, render into a unique staged path, decode and validate the full output, record input/output hashes and probe metadata, and only publish verified artifacts. Add corruption, failed replacement, and simultaneous-request integration tests using synthetic media.
