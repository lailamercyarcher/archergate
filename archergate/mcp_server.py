"""Local MCP interface to the registered sources and the two named edits."""

import argparse
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from . import media

Edit = Literal["edit-a", "edit-b"]


def make_server(workspace):
    server = FastMCP("ArcherGate Experiment 001")

    @server.tool()
    def inspect_assets() -> dict[str, Any]:
        """Show registered sources, SHA-256 hashes, provenance, rights notes and media metadata."""
        return {"assets": media.read_registry(workspace)}

    @server.tool()
    def inspect_edit(edit: Edit) -> dict[str, Any]:
        """Show a named edit's source frame ranges, timeline positions and duration."""
        return media.timeline(media.named_manifest(edit))

    @server.tool()
    def compare_edits(left: Edit, right: Edit) -> dict[str, Any]:
        """Compare the source usage, cut order and frame ranges of two named edits."""
        return media.compare(media.named_manifest(left), media.named_manifest(right))

    @server.tool()
    def render_edit(edit: Edit) -> dict[str, Any]:
        """Render a named edit to the workspace and return its MP4 path, manifest and SHA-256."""
        return media.render(workspace, media.named_manifest(edit))

    return server


def main():
    parser = argparse.ArgumentParser(description="ArcherGate Experiment 001 MCP server (stdio)")
    parser.add_argument("--workspace", type=Path, default=media.ROOT / "workspace")
    parser.add_argument("--studio", type=Path, help="enable the cinematic workspace tools for this local directory")
    parser.add_argument("--studio-write", action="store_true", help="allow explicit studio editorial revision commits")
    args = parser.parse_args()
    if args.studio_write and not args.studio:
        parser.error("--studio-write requires --studio")
    server = make_server(media.data_root(args.workspace))
    if args.studio:
        from .studio.library import Library
        from .studio.mcp import add_tools

        add_tools(server, Library(args.studio), write=args.studio_write)
    server.run()


if __name__ == "__main__":
    main()
