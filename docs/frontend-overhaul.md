# archergate / workroom 03

## the actual product, not its branding

Live repository assessed: `lailamercyarcher/archergate`, `main` at `f5e7ab9f14718e8765030ff5ceec06cd4e505c1c` (November 24, 2026, Pacific time), with no open PRs at the time of assessment. Repository instructions require an isolated branch and explicit approval before any push. Nothing here edits the published v0.2.0 release, its assets, or its checksums.

The earlier user screenshot showed three failures that color could not solve: the homepage exposed implementation detail instead of a sequence; materials forced a path/provenance/rights form ahead of a real source-selection task; Lab was a permanent record form with no useful reading or retrieval. The two previous UI patches adjusted color and labels without changing these object and action relationships enough.

### capability inventory from repository code

| code | what is actually available | what is not yet available |
| --- | --- | --- |
| `archergate/studio/library.py` | local import, original hash, derived playable proxy, video thumbnail, peak waveform, shot-candidate detection, optional local transcription | arbitrary source formats, reliable progress/cancel protocol |
| `archergate/studio/web.py` | local API routes for source list, source preview, sequence manifest, compare, render, note records, export, optional Claude request; loopback access controls | general remote cloud library, import job queue |
| `archergate/studio/timeline.py` | versioned independent picture and sound tracks, frame/tick timing, clip/source validation, split-edit inspection | full drag-and-drop nonlinear timeline editing |
| `archergate/studio/preview.py` | real FFmpeg picture+sound composition and validated MP4 preview | real-time interactive effects rendering |
| `archergate/studio/reasoning.py` | supported transform/gain edit proposals with explicit consent and rendered revision | unsupported natural-language editing operations; arbitrary provider choice |
| `archergate/studio/memory.py` | persistent film/scene/music/aesthetic/footage/annotation/suggestion records; revision-decision history | automated cross-source semantic indexing |
| `archergate/studio/interchange.py` | restricted Premiere XML plus rendered MP4 | comprehensive effects interchange |
| `src-tauri/src/main.rs` | native multi-file picker, drag-and-drop, local workspace switching, private desktop process, ephemeral session auth | native Windows launch verified in this environment |

## user flow and interaction contracts

**first run:** work opens without disabled technical controls. One primary action opens the materials import sheet and native file picker on Windows. Supported browser development mode permits an absolute local path instead. The sheet collects rights and provenance before importing. A completed import selects the actual source and presents its playable proxy. Starting a sequence calls the real `/api/timelines` and `/api/preview` operations; it never invents media.

**working:** a loaded sequence has the viewer, real timecode, optional original/proposed comparison, visible sound/picture tracks, a primary creative-direction input, and export. Unsupported editing operations are not advertised as completed. Technical manifest editing remains accessible, but it is not treated as the core product.

**research:** Lab is a retrieval surface first. A left-hand indexed collection of references/notes/decisions can be searched, filtered, and selected; the selected note reads at proper type size, with its complete description, evidence reference, time cue, suggestion history, and a button to open linked footage. `new note` and `edit note` open a separate sheet. Scene/annotation timestamps, footage/annotation asset links and suggestion decisions are required only for the relevant record kinds, matching the actual backend validation.

**AI consent:** Lab exposes permissions, not a fake persistent model-settings console. A provider request remains tied to an existing edit and the direction field. It still requires an API key, explicit approval and optional-context selections per request. No key is saved.

## interface rules

- All **interface chrome and action labels** are lowercase. User-authored titles and original source filenames retain their true spelling/case as evidence.
- Real source media takes precedence over ornament. No decorative footage, film quotes, faux surveillance markers or fabricated archive assets.
- IBM Plex Sans replaces the over-gentle previous type treatment: 17px body, 18–22px working titles, 27–48px source/record headlines; small technical information is restricted to actual timecode and supporting metadata. Fonts self-host after a deliberate build-time installation; no runtime CDN.
- Straight, flat surfaces; one raised light primary action for each stage, otherwise native text-style actions. No card collection and no repeated bordered buttons.
- No marketing copy, personality slogans, AI assistant bubbles, generic prompts, or auto-generated editorial statements.
- The contrast between the near-black viewer and unforced typography is an atmospheric choice informed by the user's *Come and See* reference, not a claim to imitate that film's visual material.
- Input, failure and progress states use actionable, concrete language. Do not claim an imported clip is ready until the server responds and its registered source is available.
- Browser viewport at 1366×768 contains Work without vertical document scroll. Source and record lists have dedicated scroll regions. Sheets are keyboard accessible with visible focus; Ctrl+1/2/3 navigates the three views.

## why this is not just a restyle

The previous Lab spent most of its viewport on fields with a blank records column. That is a task-structure mistake, not a CSS mistake. The design now spends its space on **objects already created**; an input form appears only when editing one. Materials uses the same principle: an actionable source list and viewer, with import metadata deferred until selection. Work uses sequence state to hide irrelevant controls instead of showing dead buttons.

The principle follows Nielsen Norman Group's **progressive disclosure** (defer secondary complexity to the relevant task) and workflow expectations (current actions determine what comes next). These sources support the interaction model, not the specific art direction:
- https://www.nngroup.com/articles/progressive-disclosure/
- https://www.nngroup.com/articles/workflow-expectations/
- https://www.adobe.com/learn/premiere-pro/web/get-started-premiere-pro
- https://www.avid.com/products/media-composer-scriptsync-option

## verification and limitations

**Actually performed:** inspected repository tree, AGENTS, desktop Tauri, import backend, web routes, timeline, preview, reasoning, memory database and existing browser test; checked present `main` head; ran static Playwright visual fixtures at 1366×768 and 1920×1080; verified Work document height equals viewport at 1366×768; ran an isolated Playwright synthetic-memory interaction test for creating, editing, reading, searching and opening import; compiled the patch script and checked the note JS snippet syntax. The patch checks Git blob hashes before modifying files and refuses main/dirty worktrees.

**Not performed:** the actual repository build, Python backend regressions, real media import, Tauri/WebView2 runtime test, Windows file-picker/drag test, real Claude request, Premiere XML import, full end-to-end Playwright test with this new UI. The repository's earlier release documentation reports partial Linux evidence for v0.2.0; that is not evidence that this patch passed it. Treat the package as an implementation candidate, not a shipped or proven release.

**Unresolved engineering requirement:** native Windows import reported by the user may fail due to file path, FFmpeg source/codec, permissions, proxy generation, or backend processing; the screenshot does not establish the root cause. Preserve private media; use synthetic tests and instrument real error propagation before claiming it fixed.

**Next implementation slice:** after real-path tests, add source-range selection and cut insertion against the existing independent audiovisual timeline schema, then transcript-to-timecode navigation. The current JSON editor is a genuine advanced fallback, but it is not a substitute for a useful editorial interaction.

## next engineering milestone

See [`visual-clip-editing-next.md`](visual-clip-editing-next.md) for the planned direct-manipulation timeline, canonical operation layer, and acceptance criteria. Workroom 03 contains visual timeline inspection, **not** full visual clip editing.