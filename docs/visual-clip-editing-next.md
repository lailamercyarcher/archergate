# next engineering boundary: direct visual clip editing

**status:** the reusable source-range insert operation and its desktop-authenticated local endpoint are implemented. Direct-manipulation UI and the other editing operations remain unimplemented; this is not a claim that a visual timeline editor exists.

## implemented backend operation: insert source range

`archergate.studio.operations.insert_source_range` creates a saved revision from an existing timeline without changing that timeline or the registered source. It inserts into an existing track only; `track_id` is the canonical track ID, whose existing `kind` must be `video` (picture) or `audio`.

The local desktop API is `POST /api/timeline/insert`. Its JSON object has exactly these fields:

```json
{
  "base_id": "interview-j",
  "asset_id": "0123456789abcdef0123456789abcdef",
  "track_id": "picture",
  "source_start": 24,
  "duration": 24,
  "at": 48,
  "revision_id": "interview-insert"
}
```

`source_start`, `duration`, and `at` are nonnegative integer ticks in the base timeline's `timebase`; duration must be positive. The source interval is half-open and must fit the selected asset's stream matching the target track kind. Timeline placement is also half-open: touching an existing clip boundary is allowed, but any overlap on the selected track is rejected. This is non-ripple placement: existing clips are never moved, clips on other tracks remain independent, and unfilled gaps remain gaps. The API requires the existing ephemeral desktop session cookie and workspace header. Success returns the canonical timeline validation report for the new revision; callers can then use the existing preview route.

The revision ID is also the idempotency key: retry the exact same command with the same revision ID to receive the original result. Reusing that ID with different command inputs is a conflict. The revision record stores its parent ID and canonical SHA-256; the parent is rechecked in the same SQLite write transaction that creates the revision and command receipt. Before commit, all referenced managed originals must still match their registered SHA-256. These guarantees cover the shared `insert_source_range` and `apply` operations; arbitrary manifest saves and long-running preview jobs do not yet use this command receipt contract.

## problem

ArcherGate currently stores its independent picture and sound tracks in validated, versioned JSON timeline manifests (`archergate/studio/timeline.py`). The Work view can show clip lanes and render a saved sequence, but creating or changing multi-clip edits still requires raw manifest manipulation or a limited AI operation. A drawn timeline that cannot change an edit is an inspector, not an editor.

**Product requirement:** an editor must be able to construct and revise a playable cut through direct manipulation without opening JSON.

## first usable vertical slice

1. **source selection:** in Materials, choose original media and mark `in`/`out` ranges against the actual proxy/source timebase. Show picture and sound availability and source timecodes.
2. **insert:** add the selected source range to a chosen picture or sound track at the playhead. A documented user action must produce a validated saved manifest and a freshly rendered playable sequence; no simulated completion.
3. **select and adjust:** select an actual timeline clip to trim either edge, move it, or split at the playhead. Display snapping at clip boundaries and the intended edit result before committing. Start with a focused subset if needed; do not expose nonfunctional controls.
4. **picture/sound independence:** preserve audio-only edits, picture-only edits, intentional gaps, and J/L cuts. A single AV asset may yield linked clips, but moving/splitting one should not silently destroy the independent track relationship.
5. **revisions:** commit each successful edit as a **new** timeline revision with recoverable parent/original. Provide undo/redo or explicit revert and compare without overwriting an accepted version.
6. **review/export:** use the existing `/api/preview`, `/api/compare` and `/api/export` pipeline so a saved edit can be watched, compared and exported without touching a manifest. Keep raw JSON in a genuinely advanced inspector only.

## implementation constraints

- **canonical source of truth:** the existing version 2/3 timeline schema, with `timebase` and integer `at`, `source_start`, `duration` ticks, remains authoritative. Avoid floating-point edits, off-by-one source ranges, and ad hoc browser-only timeline state.
- **validation:** reuse `timeline.schema`, `timeline.inspect`, `timeline.save` plus source-hash/original-preservation checks; do not loosen any boundary to make drag-and-drop appear to work.
- **operation layer:** implement focused, typed editorial operations in the shared Python engine (e.g. source-range insert, trim, move, split), then expose a narrow authenticated local route. The browser should send intent, not entire untrusted rewritten manifests as its primary editing protocol. Keep CLI/MCP reuse possible.
- **precise semantics:** define insert vs overwrite vs ripple explicitly, along with behavior for track overlap, clip IDs, linked sound, intentional silence, out-of-range source handles, timebase conversion and rejected edits. No destructive action by default.
- **viewer and controls:** clicking a track segment selects and seeks; trimming/moving via pointer needs keyboard equivalents, focus visibility, numeric timecode feedback and accessibility labels. Avoid importing a heavyweight editing framework until real profiling requires one.
- **performance:** avoid decoding every clip into a persistent thumbnail stream; render visible timeline segments on demand, cache thumbnails/waveforms, and preserve the low-resource local-first architecture.
- **permissions:** never send original source files to an AI provider. Remote inference remains opt-in per request; manual editing works with no API key.

## acceptance tests

- Given synthetic media with known picture/audio edges, use only the visible UI to create a new multi-clip edit, change a cut, save it, render an MP4, seek it, and export an allowed edit.
- Confirm source bytes and SHA-256 hashes remain unchanged; revision A is identical after producing B, even after a failed edit or failed render.
- Test J/L cuts, intentional gaps, split and trim at exact integer boundaries, overlap rejection, missing media, changed hashes and invalid asset IDs; verify error messages and recovery in the UI.
- Playwright: select a source range, insert, select/trim/move/split (as supported by the slice), save, reload, compare A/B at the same playhead, and confirm the rendered picture/audio content reflects the change, not merely a changed label.
- Measure startup, timeline responsiveness and preview resource usage on ordinary displays; run native Windows/WebView2 testing before claiming Windows support.

## out of scope for the first slice

Premiere-scale timeline complexity, arbitrary effects, background AI editing, new model providers, collaborative cloud editing, and extra decorative UI. Do not defer basic manual editing behind natural-language prompts.

**Definition of done:** a filmmaker can import footage, create a cut, change its clip boundaries and ordering, watch the result, compare a revision, and export without opening the JSON editor. Until that is verified against real rendered media, call the timeline a **visual inspector** rather than a functional visual editor.