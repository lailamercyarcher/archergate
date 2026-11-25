"""Register the scoped studio tools on the existing stdio transport."""

from typing import Any, Literal

from . import interchange, memory, operations, timeline

Edit = Literal["insert_source_range", "trim_clip", "move_clip", "split_clip", "insert_passage"]


def add_tools(server, library, *, write=False):
    @server.tool()
    def search_assets(query: str = "") -> dict[str, Any]:
        """Search private local assets by title, provenance or rights note."""
        return {"assets": [
            {key: row[key] for key in ("id", "name", "provenance", "rights", "sha256", "source")}
            for row in library.search(query)
        ]}

    @server.tool()
    def inspect_transcript(asset: str) -> dict[str, Any]:
        """Return timestamped speech segments or an explicit analysis status."""
        return library.asset(asset)["transcript"]

    @server.tool()
    def creative_preferences(kind: str = "") -> dict[str, Any]:
        """Retrieve user-authored creative references, preferences and suggestion decisions."""
        return {"records": memory.records(library, kind)}

    @server.tool()
    def validate_timeline(manifest: dict[str, Any]) -> dict[str, Any]:
        """Check a proposed v2 edit and explain review suggestions without modifying it."""
        return timeline.inspect(library, manifest)

    @server.tool()
    def export_timeline(name: str) -> dict[str, Any]:
        """Export a saved timeline to a fresh editable FCP7 XML in the local workspace."""
        return interchange.export(library, name)

    @server.tool()
    def inspect_revision(revision_id: str) -> dict[str, Any]:
        """Read a saved manifest, its SHA-256 identity, duration and parent revision."""
        return timeline.inspect_revision(library, revision_id)

    @server.tool()
    def inspect_source(asset_id: str) -> dict[str, Any]:
        """Read registered source metadata and exact stream starts and durations in rational seconds."""
        return library.source_metadata(asset_id)

    @server.tool()
    def prepare_edit(operation: Edit, command: dict[str, Any]) -> dict[str, Any]:
        """Validate an edit and return the proposed manifest without saving it.

        Commands require base_id, parent_sha256 from inspect_revision, and a new revision_id.
        insert_source_range also needs asset_id, track_id, source_start, duration and at.
        trim_clip also needs clip_id, edge ("in" or "out") and amount.
        move_clip and split_clip also need clip_id and at.
        insert_passage also needs asset_id, track_id, segment (zero-based transcript row), and at.
        Source start, duration, position and trim amount are integer ticks in the base timebase.
        """
        return operations.edit(library, operation, command, prepare=True)

    if write:
        @server.tool()
        def commit_edit(operation: Edit, command: dict[str, Any]) -> dict[str, Any]:
            """Revalidate and save the edit specified with the same command fields as prepare_edit.

            Writes a new revision. Reuse its revision_id only to retry the identical command.
            Use inspect_revision to retrieve the saved manifest and parent.
            """
            return operations.edit(library, operation, command)
