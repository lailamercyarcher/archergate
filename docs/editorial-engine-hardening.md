# Editorial engine hardening — architecture decision and current slice

## Observed architecture

- `Library` owns the workspace SQLite database and managed media tree. Asset IDs are random 32-character hex strings; the registered original's SHA-256 is stored with its provenance, rights, and probed metadata.
- Timeline IDs are the primary keys for immutable v2/v3 JSON manifests. Time is represented as integer ticks plus rational `timebase`/`fps`; source and timeline intervals are half-open.
- `operations.apply` and source-range insertion validate an edit and create a new timeline row. Before this change, the new row was written independently, with no durable parent record or retry receipt.
- Previews are rendered to a temporary directory and moved to a SHA-256-named MP4. The key includes the manifest and source hashes, but not an explicit renderer/tool identity or full settings. An existing path is trusted without checking its content or decoding it. A failed render before the move leaves an existing output in place; concurrent requests can race to replace the same key.
- Import crosses the filesystem and SQLite: it stages/copies source and derivatives, renames the asset directory, then inserts the asset row, compensating for an SQLite failure. An abrupt process exit between those steps can leave an orphan directory. XML exports write a unique file directly.
- HTTP media work is synchronous. The desktop server's process-local lock serializes selected routes, but CLI/MCP callers and multiple backend processes do not share it. A process exit loses in-flight work; no durable job state or restart recovery exists.
- Timeline inspection validates manifests, registered asset references, and track/source ranges, but does not hash original files. Preview and XML export do check hashes. Resource bounds include 24 tracks, 500 clips per track, 48 preview clips, ten-minute previews, 1 MiB JSON requests, and a 30-minute subprocess timeout.
- Existing tests include decoded synthetic audiovisual assertions for exact multitrack timing, fractional frame rates, audio gaps, picture content, source hashes, actual MCP calls, and import/export behavior. The Studio browser test has a known real pointer-interception failure: `accept b` covers `show b`.

## Highest-risk gaps

1. **Observed:** preview cache hits trust any existing file at the expected key; a truncated or substituted file can be reported as a successful render. Cache identity also omits explicit tool/renderer versions and render settings.
2. **Observed:** repeating an insertion after a lost response fails with “Timeline ID already exists” rather than returning the prior successful result. Concurrent duplicate submissions have no durable deduplication contract.
3. **Observed:** timeline validation alone accepts an asset whose registered original is missing or changed; later rendering/export rejects it. A command can therefore save a revision that is not currently renderable.
4. **Theoretical failure window:** abrupt termination between asset-directory rename and SQLite insert can leave orphaned media. The ordinary SQLite error path compensates, but process death cannot run that compensation.
5. **Observed limitation:** media processing is synchronous and has no persisted job/progress/recovery state. The desktop lock is process-local and does not provide durable or cross-process coordination.

## Decision

Add a narrow SQLite-backed commit receipt for the existing immutable revision operations (`insert_source_range` and `apply`). Reuse the current timeline schema and validation. The caller-selected `revision_id` is the retry key; the receipt binds it to the operation, canonical input fingerprint, parent ID/hash, resulting revision hash, and original validation report. Insert the revision and receipt in one `BEGIN IMMEDIATE` transaction after validating its full manifest and verifying referenced managed source hashes. A retry with the same request returns the stored report; reuse with different inputs, a changed parent, or a modified stored revision fails explicitly.

`Library.verify_original` centralizes the existing source-integrity check and is reused by revision commits, preview rendering, and XML export. No new dependency or persistence service is introduced. Existing timelines and manifests remain readable and unchanged; the command table is created idempotently for existing workspaces.

## Alternatives rejected

- A generic command bus or event-sourcing framework: not justified by the two current editing operations.
- Mutable “current timeline” pointers or implicit ripple/overwrite behavior: unnecessary; revisions remain explicit branches from immutable parent IDs.
- Redis, Celery, or another job service: contrary to the local-first design and premature before a single durable preview-job slice is scoped.
- Treating a filename or cache key as proof of output integrity: contradicted by the observed cache-hit behavior.

## Scope, verification, and remaining work

This is a Phase 0 investigation and a bounded Phase 1 revision-commit slice, not completion of the full hardening brief. Tests prove retry idempotency, concurrent duplicate serialization, conflict rejection, stale-parent rejection, changed-original rejection, immutable parent preservation, and decoded output correctness for existing insertion cases.

Still needed: preview identity/sidecar metadata and cache verification; staged render validation and concurrency control; durable preview jobs and restart recovery; read-only workspace diagnostics/orphan detection; applying a uniform command contract to any future editing route; native Windows/WebView2, real Premiere import, and broader filesystem-failure testing.

Baseline and current Studio browser limitation: `./scripts/verify-studio` ran all 20 pre-change Studio unit tests successfully (one optional speech test skipped), then failed at `tests/studio/browser.mjs:62` because `accept b` intercepted the `show b` click. The script stopped before its desktop-backend stage. This failure is intentionally still visible and was not bypassed by changing selectors.

## Next engineering slice

Harden preview artifacts before adding background jobs: canonicalize cache inputs with renderer/tool versions and settings, render into a unique staged path, decode and validate the full output, record input/output hashes and probe metadata, and only publish verified artifacts. Add corruption, failed replacement, and simultaneous-request integration tests using synthetic media.
