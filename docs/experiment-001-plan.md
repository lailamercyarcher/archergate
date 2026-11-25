# Experiment 001 implementation and verification plan

## Environment

The starting repository had one tracked file, `README`, and a clean `main` at `f1da39f`. The Codespace reports Ubuntu 24.04 on x86-64, Python 3.14.2 and Node 24.22.2. Codex CLI reports a ChatGPT login and lists the enabled OpenAI developer documentation MCP. FFmpeg, ffprobe and Chromium were absent from PATH. No paid service, GPU or system-level install is planned.

## Decisions

- Generate three labeled synthetic audiovisual clips locally. Register each with its SHA-256, provenance, rights note and probed media metadata. Keep working media outside Git.
- Store cuts as a versioned JSON list of asset IDs and integer start/end frames. The experiment uses hard cuts at 24 fps and a fixed output profile. FFmpeg handles decoding, trimming, concatenation and encoding. Each export embeds the manifest, source hashes and tool version. The render command reports the output hash.
- Share Python processing functions across the CLI and a local stdio MCP server. Keep the browser read-only and serve only the comparison, its page assets and two fixed exports. Support byte ranges for video seeking.
- Install pinned dependencies into the workspace. Exercise playback with a real headless browser and tool discovery and calls with the official MCP client.

## Milestones and acceptance tests

| Milestone | Expected behavior and verification |
| --- | --- |
| 1. Media pipeline | A single demo command generates and registers three local audiovisual fixtures, then exports two H.264/AAC MP4s. Probe video/audio streams and durations, decode both exports and inspect sampled scene frames and tone frequencies to confirm actual cut order. Check source hashes before and after both exports. |
| 2. Editorial inspection | Validate manifest schemas, ranges and asset references. Compare both orders and time ranges. Exercise malformed manifests, path traversal, missing media and changed hashes. Confirm a failed render preserves an existing good export. |
| 3. MCP | Start the server over stdio with the official SDK client. Discover tools, inspect assets and sequence, compare edits, render an export and validate it. Exercise invalid tool arguments. |
| 4. Browser and reproduction | Launch the server on loopback and use a real browser to load both videos, advance playback and seek. Check HTTP range handling and path restrictions. Run one verification command in a fresh environment and document results and limitations. |

The fixtures use uniform dimensions, rate and audio settings so cut order can be measured from the rendered streams. Pin tooling and record the build because encoded bytes can vary by FFmpeg build and CPU. Source lookup and output publishing must guard against escaped paths and partial writes. Browser codec support is a test requirement.

## Verified milestones

- Media and editorial inspection passed four automated checks with FFmpeg and ffprobe 7.0.2-static. Each export contains 144 H.264 frames and mono 48 kHz AAC, runs 6.000 seconds and decodes to end of file. Sampled picture colors and tones identify Signal / Transit / Echo (330 / 440 / 550 Hz) in A and Echo / Signal / Transit (550 / 330 / 440 Hz) in B. All three registered SHA-256 values were unchanged. The sampled frames show the synthetic labels and animated markers.
- Invalid JSON, duplicate keys, wrong frame rate, empty cuts, invalid frame ranges, unknown assets and escaping identifiers return specific errors. Missing, linked and changed media are rejected, and the previous export retains its SHA-256.
- The official Python MCP SDK client negotiated protocol 2025-11-25 over stdio, discovered and called all four tools, decoded the MP4 it requested and checked its embedded manifest and source hashes. The invalid render ID was rejected without writing an extra file.
- Chrome for Testing 159.0.8154.6 played and sought both clips on loopback. The test sampled decoded pixels before and after seeking, confirmed both 6.000-second timelines, verified exact HTTP 206 bytes and HTTP 416 for out-of-range reads, and observed HTTP 404 for source files, the registry and a traversal attempt.
- Clean reproduction passed on the same Codespace in a source-only copy with no preinstalled project dependencies or generated media. The documented setup completed, pip reported no broken requirements, and `./scripts/verify` passed all five Python tests plus the real browser checks. The documented demo command also produced both six-second H.264/AAC files in the main worktree.
