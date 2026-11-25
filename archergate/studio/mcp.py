"""Register the Directive 002 tools on the existing stdio transport."""

from typing import Any

from . import interchange, memory, timeline


def add_tools(server, library):
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
