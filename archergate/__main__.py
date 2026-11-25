"""Command-line entry point. Run python -m archergate --help."""

import argparse
import json
import sys
from pathlib import Path

from . import media
from .fixtures import generate_fixtures


def main():
    parser = argparse.ArgumentParser(description="ArcherGate Experiment 001")
    parser.add_argument("--workspace", type=Path, default=media.ROOT / "workspace")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="generate, register and render all synthetic demo material")
    commands.add_parser("assets", help="show the source registry")
    register = commands.add_parser("register", help="register workspace/sources/ID.mp4 in place")
    register.add_argument("asset")
    register.add_argument("--provenance", required=True)
    register.add_argument("--rights", required=True)
    render = commands.add_parser("render", help="validate and render a JSON manifest")
    render.add_argument("manifest", type=Path)
    inspect = commands.add_parser("inspect", help="show a manifest's computed timeline")
    inspect.add_argument("manifest", type=Path)
    compare = commands.add_parser("compare", help="compare two JSON manifests")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    serve = commands.add_parser("serve", help="start the local browser comparison")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    try:
        match args.command:
            case "demo":
                generate_fixtures(args.workspace)
                result = [media.render(args.workspace, media.MANIFESTS / f"{edit}.json") for edit in media.EDIT_IDS]
            case "assets":
                result = media.read_registry(args.workspace)
            case "register":
                result = media.register(args.workspace, args.asset, args.provenance, args.rights)
            case "render":
                result = media.render(args.workspace, args.manifest)
            case "inspect":
                result = media.timeline(media.load_manifest(args.manifest))
            case "compare":
                result = media.compare(media.load_manifest(args.left), media.load_manifest(args.right))
            case "serve":
                from .web import serve
                serve(args.workspace, args.port)
                return 0
        print(json.dumps(result, indent=2))
    except (media.ExperimentError, OSError) as error:
        print(f"ArcherGate: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
