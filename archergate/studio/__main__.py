"""Portable command-line entry point for Experiment 002."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from archergate.media import ExperimentError, read_json

from . import fixtures, interchange, memory, operations, timeline
from .library import DEFAULT_WORKSPACE, Library, binary, run


def main():
    parser = argparse.ArgumentParser(description="ArcherGate cinematic workspace")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("demo")
    ingest = sub.add_parser("import")
    ingest.add_argument("source")
    ingest.add_argument("--provenance", required=True)
    ingest.add_argument("--rights", required=True)
    search = sub.add_parser("search")
    search.add_argument("query", nargs="?", default="")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("manifest", type=Path)
    save = sub.add_parser("save")
    save.add_argument("manifest", type=Path)
    proposal = sub.add_parser("apply-operations")
    proposal.add_argument("base")
    proposal.add_argument("proposal", type=Path)
    sub.add_parser("inspect-revision").add_argument("revision")
    sub.add_parser("inspect-source").add_argument("asset")
    for name in ("prepare-edit", "commit-edit"):
        edit = sub.add_parser(name)
        edit.add_argument("operation", choices=operations.EDITS)
        edit.add_argument("request", type=Path)
    compare = sub.add_parser("compare")
    compare.add_argument("left")
    compare.add_argument("right")
    export = sub.add_parser("export")
    export.add_argument("timeline")
    direction = sub.add_parser("memory")
    direction.add_argument("record", type=Path, nargs="?")
    for name in ("serve", "launch"):
        server = sub.add_parser(name)
        server.add_argument("--port", type=int, default=8020)
        server.add_argument("--desktop", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        library = Library(args.workspace)
        backend, model = os.getenv("ARCHERGATE_WHISPER"), os.getenv("ARCHERGATE_WHISPER_MODEL")
        match args.command:
            case "doctor":
                result = {name: run(binary(name), "-version", timeout=15).decode(errors="replace").splitlines()[0]
                          for name in ("ffmpeg", "ffprobe")}
                result["speech_configured"] = bool(backend and model)
            case "demo":
                if timeline.list_timelines(library):
                    raise ExperimentError("Demo needs a workspace without timelines. Select another --workspace.")
                sources = fixtures.make_sources(library.path("synthetic"), os.getenv("ARCHERGATE_TEST_SPEECH_WAV"))
                assets = [library.import_media(path, "ArcherGate labeled synthetic fixture", "Generated locally")
                          for path in sources]
                result = [timeline.save(library, edit) for edit in timeline.demo_timelines(assets)]
            case "import":
                result = library.import_media(args.source, args.provenance, args.rights, backend, model)
            case "search":
                result = library.search(args.query)
            case "inspect":
                result = timeline.inspect(library, read_json(args.manifest))
            case "save":
                result = timeline.save(library, read_json(args.manifest))
            case "apply-operations":
                result = operations.apply(library, args.base, read_json(args.proposal))
            case "inspect-revision":
                result = timeline.inspect_revision(library, args.revision)
            case "inspect-source":
                result = library.source_metadata(args.asset)
            case "prepare-edit" | "commit-edit":
                result = operations.edit(library, args.operation, read_json(args.request),
                                         prepare=args.command == "prepare-edit")
            case "compare":
                result = timeline.compare(library, args.left, args.right)
            case "export":
                result = interchange.export(library, args.timeline)
            case "memory":
                result = memory.write(library, read_json(args.record)) if args.record else memory.records(library)
            case "serve" | "launch":
                from .web import serve
                serve(library, args.port, open_browser=args.command == "launch", desktop=args.desktop)
                return 0
        print(json.dumps(result, indent=2))
    except (ExperimentError, OSError, sqlite3.Error) as error:
        print(f"ArcherGate: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
