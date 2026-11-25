import asyncio
import json
import secrets
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from datetime import timedelta
from fractions import Fraction
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.request import Request, urlopen

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from archergate.media import sha256
from archergate.studio.library import Library, ffmpeg, input_args, probe
from archergate.studio import memory, preview, timeline
from archergate.studio.fixtures import make_sources
from archergate.studio.web import StudioHandler
from media_acceptance import decoded_audio, decoded_video_timestamps


@asynccontextmanager
async def studio_client(library, *, write=False):
    args = ["-m", "archergate.mcp_server", "--studio", str(library.root)]
    if write:
        args.append("--studio-write")
    async with stdio_client(StdioServerParameters(command=sys.executable, args=args)) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as client:
            yield client, await client.initialize()


class StudioMCP(unittest.TestCase):
    def test_real_client_calls_all_studio_tools(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            library = Library(root / "studio")
            assets = [library.import_media(source, "Synthetic", "Local fixture")
                      for source in make_sources(root / "fixtures")]
            edit = next(timeline.demo_timelines(assets))
            timeline.save(library, edit)
            memory.write(library, {"kind": "aesthetic", "title": "Room tone", "description": "Keep the pauses."})
            asyncio.run(self.exercise(library, edit))

    async def exercise(self, library, edit):
        settings = StdioServerParameters(command=sys.executable,
                                         args=["-m", "archergate.mcp_server", "--studio", str(library.root)])
        async with stdio_client(settings) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as client:
                await client.initialize()
                tools = {tool.name for tool in (await client.list_tools()).tools}
                self.assertTrue({"inspect_assets", "search_assets", "inspect_transcript", "creative_preferences",
                                 "validate_timeline", "export_timeline"} <= tools)

                async def call(tool, **arguments):
                    result = await client.call_tool(tool, arguments)
                    self.assertFalse(result.isError, result.content)
                    return result.structuredContent

                result = await call("search_assets", query="interview")
                self.assertEqual(len(result["assets"]), 1)
                asset = result["assets"][0]["id"]
                self.assertEqual((await call("inspect_transcript", asset=asset))["state"], "unavailable")
                self.assertEqual((await call("creative_preferences", kind="aesthetic"))["records"][0]["title"], "Room tone")
                self.assertTrue((await call("validate_timeline", manifest=edit))["valid"])
                output = await call("export_timeline", name=edit["id"])
                self.assertEqual(ET.parse(library.root / output["file"]).getroot().get("version"), "5")
                self.assertTrue((await client.call_tool("export_timeline", {"name": "../escape"})).isError)

    def test_editorial_round_trip_matches_http_and_decodes_media(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = make_sources(root / "fixtures")
            source_hashes = [sha256(path) for path in paths]
            library = Library(root / "mcp")
            assets = [library.import_media(path, "Synthetic editorial fixture", "Generated locally") for path in paths]
            picture, _, voice, music = assets
            base = {
                "version": 2, "id": "base", "title": "Synthetic sequence", "fps": "24", "timebase": "1/24",
                "tracks": [
                    {"id": "picture", "kind": "video", "clips": [
                        {"id": "opening", "asset": picture["id"], "at": 0, "source_start": 0, "duration": 24}]},
                    {"id": "sound", "kind": "audio", "clips": [
                        {"id": "dialogue", "asset": voice["id"], "at": 0, "source_start": 0, "duration": 24}]},
                ],
            }
            timeline.save(library, base)
            http_library = Library(copytree(library.root, root / "http"))
            token = secrets.token_hex(32)
            handler = partial(StudioHandler, library=http_library, token=token.encode())
            with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()

                def http(path, payload=None):
                    request = Request(f"http://127.0.0.1:{server.server_port}{path}",
                                      data=json.dumps(payload).encode() if payload is not None else None,
                                      headers={"Cookie": f"archergate_session={token}", "Content-Type": "application/json",
                                               "X-ArcherGate-Workspace": "1"})
                    with urlopen(request, timeout=10) as response:
                        return json.load(response)

                try:
                    asyncio.run(self.exercise_editorial(library, base, picture, music, http))
                finally:
                    server.shutdown()
                    thread.join()

            for path, asset, expected in zip(paths, assets, source_hashes):
                self.assertEqual(sha256(path), expected)
                for workspace in (library, http_library):
                    self.assertEqual(sha256(workspace.original(asset)), expected)
            print("Fixture and managed original SHA-256 matched in both workspaces: "
                  + ", ".join(source_hashes), flush=True)

    async def exercise_editorial(self, library, base, picture, music, http):
        async def call(client, tool, **arguments):
            result = await client.call_tool(tool, arguments)
            self.assertFalse(result.isError, result.content)
            return result.structuredContent

        async def reject(client, tool, message, **arguments):
            result = await client.call_tool(tool, arguments)
            self.assertTrue(result.isError, result)
            self.assertIn(message, " ".join(item.text for item in result.content))

        async def revisions(client, base_id):
            detail = await call(client, "inspect_revision", revision_id=base_id)
            self.assertEqual(timeline.canonical(detail),
                             timeline.canonical(http(f"/api/timeline/revision?id={base_id}")))
            return detail

        async def submit(client, operation, route, command):
            draft = await call(client, "prepare_edit", operation=operation, command=command)
            self.assertTrue(draft["validation"]["valid"])
            await reject(client, "inspect_revision", "Unknown timeline", revision_id=command["revision_id"])
            result = await call(client, "commit_edit", operation=operation, command=command)
            self.assertEqual(result, draft["validation"])
            self.assertEqual(await call(client, "commit_edit", operation=operation, command=command), result)
            self.assertEqual(http(route, command), result)
            saved = await revisions(client, command["revision_id"])
            self.assertEqual(saved, draft)
            self.assertEqual((saved["parent_id"], saved["parent_sha256"]),
                             (command["base_id"], command["parent_sha256"]))
            return saved

        insert_op = "insert_source_range"
        async with studio_client(library) as (client, _):
            discovered = {tool.name for tool in (await client.list_tools()).tools}
            self.assertTrue({"inspect_revision", "inspect_source", "prepare_edit"} <= discovered)
            self.assertNotIn("commit_edit", discovered)
            start = await revisions(client, base["id"])
            self.assertEqual(start["manifest"], base)
            self.assertIsNone(start["parent_id"])
            source = await call(client, "inspect_source", asset_id=picture["id"])
            browser_source = http(f"/api/asset/{picture['id']}")
            self.assertEqual(source, {key: browser_source[key] for key in source})
            self.assertEqual(source["sha256"], picture["sha256"])
            stream = source["source"]["streams"]["video"]
            self.assertEqual((Fraction(stream["start"]), Fraction(stream["duration"])), (0, 3))
            insert = {"base_id": base["id"], "parent_sha256": start["sha256"], "revision_id": "picture-insert",
                      "asset_id": picture["id"], "track_id": "picture", "source_start": 24, "duration": 24, "at": 24}
            draft = await call(client, "prepare_edit", operation=insert_op, command=insert)
            self.assertEqual(await call(client, "prepare_edit", operation=insert_op, command=insert), draft)
            await reject(client, "commit_edit", "Unknown tool", operation=insert_op, command=insert)
            await reject(client, "inspect_revision", "Unknown timeline", revision_id=insert["revision_id"])

        request = library.root.parent / "edit-request.json"
        request.write_text(json.dumps(insert), encoding="utf-8")
        cli = [sys.executable, "-m", "archergate.studio", "--workspace", str(library.root)]
        self.assertEqual(json.loads(subprocess.check_output([*cli, "prepare-edit", insert_op, str(request)])), draft)

        async with studio_client(library, write=True) as (client, initialized):
            self.assertIn("commit_edit", {tool.name for tool in (await client.list_tools()).tools})
            picture_edit = await submit(client, insert_op, "/api/timeline/insert", insert)
            self.assertEqual(picture_edit, draft)
            clip = picture_edit["manifest"]["tracks"][0]["clips"][-1]
            self.assertEqual(clip, {"id": clip["id"], "asset": picture["id"], "at": 24,
                                    "source_start": 24, "duration": 24})
            self.assertEqual(picture_edit["validation"]["duration_ticks"], 48)
            self.assertEqual(picture_edit["manifest"]["tracks"][1], base["tracks"][1])
            await reject(client, "prepare_edit", "Timeline ID already exists", operation=insert_op, command=insert)

            invalid = [
                ({"base_id": picture_edit["manifest"]["id"]}, "parent revision changed"),
                ({"parent_sha256": None}, "supplied fingerprint"),
                ({"asset_id": "0" * 32}, "Unknown asset"),
                ({"source_start": 72}, "falls outside"),
                ({"at": 12}, "overlaps clip"),
                ({"at": True}, "integer tick"),
                ({"duration": 24.0}, "integer tick"),
                ({"track_id": "unknown"}, "Unknown track"),
                ({"track_id": "../sound"}, "Use an identifier"),
                ({"workspace": "."}, "requires"),
            ]
            for i, (changes, message) in enumerate(invalid):
                command = {**insert, "revision_id": f"invalid-{i}", **changes}
                for tool in ("prepare_edit", "commit_edit"):
                    await reject(client, tool, message, operation=insert_op, command=command)
            await reject(client, "commit_edit", "parent_sha256", operation=insert_op,
                         command={key: value for key, value in insert.items() if key != "parent_sha256"})
            await reject(client, "commit_edit", "different command", operation=insert_op,
                         command={**insert, "source_start": 12})
            await reject(client, "prepare_edit", "ripple", operation="ripple", command=insert)

            original = library.original(picture)
            contents = original.read_bytes()
            checked_insert = {**insert, "revision_id": "bad-original"}
            await call(client, "prepare_edit", operation=insert_op, command=checked_insert)
            try:
                original.write_bytes(contents + b"tampered fixture")
                for tool in ("prepare_edit", "commit_edit"):
                    await reject(client, tool, "Missing or changed original", operation=insert_op,
                                 command=checked_insert)
            finally:
                original.write_bytes(contents)

            sound = {**insert, "base_id": picture_edit["manifest"]["id"], "parent_sha256": picture_edit["sha256"],
                     "revision_id": "sound-insert", "asset_id": music["id"], "track_id": "sound", "source_start": 0}
            sound_edit = await submit(client, insert_op, "/api/timeline/insert", sound)
            trim = {"base_id": "sound-insert", "parent_sha256": sound_edit["sha256"], "revision_id": "picture-trim",
                    "clip_id": clip["id"], "edge": "out", "amount": 1}
            final = await submit(client, "trim_clip", "/api/timeline/trim", trim)
            self.assertEqual(final["validation"]["duration_ticks"], 48)
            picture_track, audio_track = final["manifest"]["tracks"]
            self.assertEqual((picture_track["kind"], audio_track["kind"]), ("video", "audio"))
            self.assertEqual(picture_track["clips"], [base["tracks"][0]["clips"][0], {**clip, "duration": 23}])
            self.assertEqual(audio_track, sound_edit["manifest"]["tracks"][1])
            self.assertEqual({k: v for k, v in audio_track["clips"][-1].items() if k != "id"},
                             {"asset": music["id"], "at": 24, "source_start": 0, "duration": 24})
            self.assertEqual(await revisions(client, "base"), start)
            self.assertEqual(await revisions(client, "picture-insert"), picture_edit)
            self.assertEqual(await revisions(client, "sound-insert"), sound_edit)
            split = await submit(client, "split_clip", "/api/timeline/split", {
                "base_id": "picture-trim", "parent_sha256": final["sha256"], "revision_id": "picture-split",
                "clip_id": clip["id"], "at": 36,
            })
            left, right = split["manifest"]["tracks"][0]["clips"][-2:]
            self.assertEqual(left, {**clip, "duration": 12})
            self.assertEqual((right["at"], right["source_start"], right["duration"]), (36, 36, 11))
            move = {"base_id": "picture-split", "parent_sha256": split["sha256"], "revision_id": "picture-move",
                    "clip_id": right["id"], "at": 48}
            await reject(client, "commit_edit", "simultaneous clips", operation="move_clip",
                         command={**move, "at": 35})
            moved = await submit(client, "move_clip", "/api/timeline/move", move)
            self.assertEqual(moved["manifest"]["tracks"][0]["clips"][-1], {**right, "at": 48})
            self.assertEqual(await revisions(client, "picture-trim"), final)
            print(f"Studio MCP protocol {initialized.protocolVersion}: read-only and write-enabled sessions "
                  "verified deterministic insert, trim, split and move, HTTP parity and invalid command rejection.", flush=True)

        self.assertEqual(json.loads(subprocess.check_output([*cli, "inspect-revision", "picture-trim"])), final)
        self.assertEqual(json.loads(subprocess.check_output([*cli, "commit-edit", insert_op, str(request)])),
                         picture_edit["validation"])
        async with studio_client(Library(library.root)) as (client, _):
            for snapshot in (start, picture_edit, sound_edit, final, split, moved):
                self.assertEqual(await call(client, "inspect_revision", revision_id=snapshot["manifest"]["id"]),
                                 snapshot)
        with library.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM timeline_commands").fetchone()[0], 5)
            self.assertEqual(db.execute("SELECT count(*) FROM timelines").fetchone()[0], 6)

        rendered = library.path("previews", preview.render(library, "picture-trim") + ".mp4")
        info = probe(rendered)
        self.assertEqual(Fraction(info["duration"]), 2)
        self.assertEqual({kind: stream["codec"] for kind, stream in info["streams"].items()},
                         {"video": "h264", "audio": "aac"})
        stamps = decoded_video_timestamps(rendered)
        self.assertEqual((stamps["frame_count"], stamps["rate"]), (48, "24/1"))
        raw = ffmpeg(*input_args(rendered), "-vf", "scale=1:1,format=gray", "-f", "rawvideo", "-")
        self.assertEqual(len(raw), 48)
        self.assertTrue(all(value < 40 for value in raw[:24]), list(raw))
        self.assertTrue(all(value > 210 for value in raw[24:47]), list(raw))
        self.assertLess(raw[47], 40)
        voice = decoded_audio(rendered, 0.25, 0.5)
        music_tone = decoded_audio(rendered, 1.25, 0.5)
        self.assertAlmostEqual(voice["frequency_hz"], 330, delta=8)
        self.assertAlmostEqual(music_tone["frequency_hz"], 550, delta=8)
        ffmpeg(*input_args(rendered), "-map", "0", "-f", "null", "-")
        print(f"Decoded 48 H.264 frames / 2 s (black, white, black at frame 47) with "
              f"{voice['frequency_hz']:.1f} Hz and {music_tone['frequency_hz']:.1f} Hz AAC samples.", flush=True)
