# ArcherGate engineering

- Work on a dedicated branch. Preserve existing work and seek approval before pushing, merging or deploying.
- Keep source media immutable. Record content hashes, provenance and rights information before rendering. Keep media, credentials and generated outputs out of Git.
- Keep editorial data, media processing, MCP and presentation separate. Reuse the same processing code across interfaces.
- Validate identifiers, paths and manifests at their boundaries. Use argument lists for subprocesses and keep command execution out of public tool interfaces.
- Use synthetic fixtures in tests. Verify actual rendered media and client behavior before claiming a milestone is complete. Record observed results and limitations.
- Keep setup repeatable and reviewable. Ask before adding paid services or system-level dependencies. Keep development ports private.

## Project context

- This repository is an early-stage media production lab. Read `README` and `docs/experiment-001-plan.md` before making architectural decisions; the plan records intended behavior, not proof that a milestone has been implemented.
- For the planned experiment, keep cuts as versioned JSON referencing asset IDs and integer frame ranges. Keep media processing reusable between the CLI and local stdio MCP server; the browser is read-only and serves only the comparison and fixed exports.
- Keep working media and renders outside Git. Verify ignore rules before generating files, and never modify source media during processing.

## Changes and verification

- Inspect the current repository and reuse its established tools and patterns. Do not assume a framework, dependency, test command, or completed feature that is not present.
- For media-pipeline changes, verify source hashes remain unchanged and inspect rendered streams and sampled content; successful command completion alone is not sufficient.
- For boundary-sensitive changes, test invalid manifests, asset references, paths, and changed hashes as applicable. Preserve an existing valid export if a render fails.
- For MCP or browser changes, exercise real client behavior, including tool discovery/calls or playback/seeking and byte-range requests, as applicable. Report what was actually verified and any limitations.

## Engineering discipline

- Prefer the smallest implementation that satisfies verified
  acceptance criteria. Do not introduce speculative abstractions.
- Never weaken tests, suppress failures, or substitute mocked
  behavior for required end-to-end verification.
- Preserve reusable production capabilities, asset provenance,
  and reproducible experiment records across experiments.
