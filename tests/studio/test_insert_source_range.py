import json
import struct
import unittest
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from archergate.media import ExperimentError, sha256
from archergate.studio import operations, preview, timeline
from archergate.studio.fixtures import make_sources
from archergate.studio.library import Library, ffmpeg, input_args, probe
from archergate.studio.web import StudioHandler


class InsertSourceRangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        root = Path(temporary.name)
        cls.sources = make_sources(root / "fixtures")
        cls.source_hashes = [sha256(path) for path in cls.sources]
        cls.library = Library(root / "workspace")
        cls.assets = [
            cls.library.import_media(path, "Synthetic insert fixture", "Generated locally")
            for path in cls.sources
        ]
        cls.picture, _, cls.dialogue, cls.music = cls.assets
        cls.base = {
            "version": 2,
            "id": "base",
            "title": "Synthetic insert test",
            "timebase": "1/24",
            "fps": "24",
            "tracks": [
                {"id": "picture", "kind": "video", "clips": [{
                    "id": "picture-before", "asset": cls.picture["id"],
                    "at": 0, "source_start": 0, "duration": 24,
                }]},
                {"id": "sound", "kind": "audio", "clips": [{
                    "id": "sound-before", "asset": cls.dialogue["id"],
                    "at": 0, "source_start": 0, "duration": 24,
                }]},
            ],
        }
        timeline.save(cls.library, cls.base)

    def stored_timeline(self, timeline_id):
        with self.library.connect() as db:
            row = db.execute("SELECT details FROM timelines WHERE id = ?", (timeline_id,)).fetchone()
        return row[0].encode("utf-8")

    def timeline_count(self):
        with self.library.connect() as db:
            return db.execute("SELECT count(*) FROM timelines").fetchone()[0]

    def assert_sources_unchanged(self):
        self.assertEqual([sha256(path) for path in self.sources], self.source_hashes)
        for asset, digest in zip(self.assets, self.source_hashes):
            self.assertEqual(sha256(self.library.original(asset)), digest)

    def insert(self, **overrides):
        arguments = {
            "base_id": "base",
            "asset_id": self.picture["id"],
            "track_id": "picture",
            "source_start": 24,
            "duration": 24,
            "at": 24,
            "revision_id": "picture-insert",
        }
        arguments.update(overrides)
        return operations.insert_source_range(self.library, **arguments)

    def test_video_and_audio_inserts_render_expected_picture_and_tone_timing(self):
        original_bytes = self.stored_timeline("base")
        picture_result = self.insert()
        self.assertTrue(picture_result["valid"])
        picture_bytes = self.stored_timeline("picture-insert")

        audio_result = operations.insert_source_range(
            self.library, "picture-insert", self.music["id"], "sound",
            source_start=0, duration=24, at=24, revision_id="audiovisual-insert",
        )
        self.assertTrue(audio_result["valid"])
        self.assertEqual(self.stored_timeline("base"), original_bytes)
        self.assertEqual(self.stored_timeline("picture-insert"), picture_bytes)

        manifest = timeline.load(self.library, "audiovisual-insert")
        picture_clips = manifest["tracks"][0]["clips"]
        sound_clips = manifest["tracks"][1]["clips"]
        self.assertEqual([(clip["at"], clip["source_start"], clip["duration"])
                          for clip in picture_clips], [(0, 0, 24), (24, 24, 24)])
        self.assertEqual([(clip["at"], clip["source_start"], clip["duration"])
                          for clip in sound_clips], [(0, 0, 24), (24, 0, 24)])

        key = preview.render(self.library, "audiovisual-insert")
        rendered = self.library.path("previews", key + ".mp4")
        self.assertEqual(Fraction(probe(rendered)["duration"]), Fraction(2))
        ffmpeg(*input_args(rendered), "-map", "0", "-f", "null", "-")

        first_picture = ffmpeg(
            "-ss", "0.5", *input_args(rendered), "-frames:v", "1",
            "-vf", "scale=1:1,format=gray", "-f", "rawvideo", "-",
        )
        inserted_picture = ffmpeg(
            "-ss", "1.5", *input_args(rendered), "-frames:v", "1",
            "-vf", "scale=1:1,format=gray", "-f", "rawvideo", "-",
        )
        self.assertLess(first_picture[0], 40)
        self.assertGreater(inserted_picture[0], 210)

        def tone_frequency(start):
            raw = ffmpeg("-ss", str(start), *input_args(rendered), "-t", "0.5", "-vn",
                         "-ac", "1", "-ar", "48000", "-f", "s16le", "-")
            samples = [sample for (sample,) in struct.iter_unpack("<h", raw)]
            crossings = sum((left < 0) != (right < 0) for left, right in zip(samples, samples[1:]))
            return crossings / (2 * (len(samples) - 1) / 48000)

        self.assertAlmostEqual(tone_frequency(0.25), 330, delta=8)
        self.assertAlmostEqual(tone_frequency(1.25), 550, delta=8)
        self.assertEqual(self.stored_timeline("base"), original_bytes)
        self.assert_sources_unchanged()

    def test_insertion_preserves_intentional_gaps_without_ripple(self):
        original_bytes = self.stored_timeline("base")
        result = self.insert(
            asset_id=self.music["id"], track_id="sound", source_start=24,
            duration=12, at=48, revision_id="gap-insert",
        )
        self.assertTrue(result["valid"])
        sound_clips = timeline.load(self.library, "gap-insert")["tracks"][1]["clips"]
        self.assertEqual([(clip["at"], clip["duration"]) for clip in sound_clips], [(0, 24), (48, 12)])
        self.assertEqual(self.stored_timeline("base"), original_bytes)
        self.assert_sources_unchanged()

    def test_one_frame_trim_commits_parented_revision_and_renders_changed_cut(self):
        picture_result = self.insert(revision_id="trim-source-picture")
        audiovisual = operations.insert_source_range(
            self.library, "trim-source-picture", self.music["id"], "sound",
            source_start=0, duration=24, at=24, revision_id="trim-source-av",
        )
        self.assertTrue(audiovisual["valid"])
        before = self.stored_timeline("trim-source-av")
        manifest = timeline.load(self.library, "trim-source-av")
        inserted = next(clip for clip in manifest["tracks"][0]["clips"] if clip["at"] == 24)

        trimmed = operations.trim_clip(
            self.library, "trim-source-av", inserted["id"], "out", 1, "trimmed-source-av",
        )
        self.assertTrue(trimmed["valid"])
        self.assertEqual(timeline.parent_revision(self.library, "trimmed-source-av"), "trim-source-av")
        self.assertEqual(self.stored_timeline("trim-source-av"), before)
        clips = timeline.load(self.library, "trimmed-source-av")["tracks"][0]["clips"]
        changed = next(clip for clip in clips if clip["id"] == inserted["id"])
        self.assertEqual((changed["at"], changed["source_start"], changed["duration"]), (24, 24, 23))

        original_key = preview.render(self.library, "trim-source-av")
        trimmed_key = preview.render(self.library, "trimmed-source-av")
        original = self.library.path("previews", original_key + ".mp4")
        rendered = self.library.path("previews", trimmed_key + ".mp4")

        def final_frame(path):
            return ffmpeg(*input_args(path), "-vf", r"select=eq(n\,47),scale=1:1,format=gray",
                          "-fps_mode", "vfr", "-frames:v", "1", "-f", "rawvideo", "-")

        self.assertGreater(final_frame(original)[0], 210)
        self.assertLess(final_frame(rendered)[0], 40)
        ffmpeg(*input_args(rendered), "-map", "0", "-f", "null", "-")
        self.assert_sources_unchanged()

    def test_invalid_trim_does_not_create_revision(self):
        original = self.stored_timeline("base")
        cases = [
            ("missing-clip", "out", 1, "Unknown clip"),
            ("picture-before", "side", 1, "trim the clip in-point or out-point"),
            ("picture-before", "out", 24, "leave at least one frame"),
            ("picture-before", "out", 0, "integer tick count"),
        ]
        for index, (clip_id, edge, amount, error) in enumerate(cases):
            with self.subTest(edge=edge, amount=amount):
                count = self.timeline_count()
                with self.assertRaisesRegex(ExperimentError, error):
                    operations.trim_clip(self.library, "base", clip_id, edge, amount, f"invalid-trim-{index}")
                self.assertEqual(self.stored_timeline("base"), original)
                self.assertEqual(self.timeline_count(), count)
        self.assert_sources_unchanged()

    def test_duplicate_and_concurrent_submissions_return_one_committed_revision(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.insert(revision_id="retryable-insert"), range(2)))

        self.assertEqual(results[0], results[1])
        manifest = timeline.load(self.library, "retryable-insert")
        self.assertEqual(len(manifest["tracks"][0]["clips"]), 2)
        with self.library.connect() as db:
            rows = db.execute(
                "SELECT count(*) FROM timeline_commands WHERE revision_id = ?",
                ("retryable-insert",),
            ).fetchone()[0]
        self.assertEqual(rows, 1)

        original = self.library.original(self.picture)
        original_bytes = original.read_bytes()
        try:
            original.write_bytes(original_bytes + b"changed after commit")
            self.assertEqual(self.insert(revision_id="retryable-insert"), results[0])
        finally:
            original.write_bytes(original_bytes)

        with self.assertRaisesRegex(ExperimentError, "different command"):
            self.insert(source_start=36, revision_id="retryable-insert")
        self.assertEqual(len(timeline.load(self.library, "retryable-insert")["tracks"][0]["clips"]), 2)

    def test_changed_original_rejects_revision_without_changing_parent(self):
        original = self.library.original(self.picture)
        original_bytes = original.read_bytes()
        before = self.stored_timeline("base")
        try:
            original.write_bytes(original_bytes + b"changed")
            with self.assertRaisesRegex(ExperimentError, "Missing or changed original"):
                self.insert(revision_id="changed-source-insert")
            self.assertEqual(self.stored_timeline("base"), before)
            self.assertNotIn("changed-source-insert", [row["id"] for row in timeline.list_timelines(self.library)])
        finally:
            original.write_bytes(original_bytes)
        self.assert_sources_unchanged()

    def test_parent_change_before_commit_rejects_stale_command(self):
        stored = self.stored_timeline("base").decode("utf-8")
        original_commit = timeline.commit_revision

        def mutate_parent_then_commit(library, manifest, **kwargs):
            changed = json.loads(stored)
            changed["title"] = "Concurrent parent revision"
            with library.connect() as db:
                db.execute("UPDATE timelines SET details = ? WHERE id = ?", (json.dumps(changed), "base"))
            return original_commit(library, manifest, **kwargs)

        try:
            with patch.object(timeline, "commit_revision", side_effect=mutate_parent_then_commit):
                with self.assertRaisesRegex(ExperimentError, "parent revision changed"):
                    self.insert(revision_id="stale-parent-insert")
            self.assertNotIn("stale-parent-insert", [row["id"] for row in timeline.list_timelines(self.library)])
        finally:
            with self.library.connect() as db:
                db.execute("UPDATE timelines SET details = ? WHERE id = ?", (stored, "base"))
        self.assertEqual(self.stored_timeline("base"), stored.encode("utf-8"))

    def test_rejected_operations_leave_original_revision_unchanged(self):
        original_bytes = self.stored_timeline("base")
        cases = [
            ({"at": 12}, "overlaps clip sound-before|overlaps clip picture-before"),
            ({"source_start": 72, "duration": 1}, "outside the asset's video stream"),
            ({"asset_id": "0" * 32}, "Unknown asset"),
            ({"track_id": "unknown"}, "Unknown track"),
            ({"asset_id": self.music["id"]}, "has no video stream"),
            ({"at": True}, "Timeline insertion position must be an integer"),
            ({"revision_id": "base"}, "distinct from the source timeline"),
        ]
        for index, (arguments, error) in enumerate(cases):
            with self.subTest(arguments=arguments):
                before_count = self.timeline_count()
                with self.assertRaisesRegex(ExperimentError, error):
                    operation_arguments = {"revision_id": f"failed-{index}", **arguments}
                    self.insert(**operation_arguments)
                self.assertEqual(self.stored_timeline("base"), original_bytes)
                self.assertEqual(self.timeline_count(), before_count)
        self.assert_sources_unchanged()

    def test_authenticated_local_api_inserts_and_returns_validation(self):
        token = b"ab" * 32
        handler = partial(StudioHandler, library=self.library, token=token, media_lock=Lock())
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/api/timeline/insert"
        original_bytes = self.stored_timeline("base")
        payload = {
            "base_id": "base",
            "asset_id": self.picture["id"],
            "track_id": "picture",
            "source_start": 24,
            "duration": 12,
            "at": 24,
            "revision_id": "api-insert",
        }
        try:
            unauthorized = Request(url, data=json.dumps(payload).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "X-ArcherGate-Workspace": "1"})
            with self.assertRaises(HTTPError) as denied:
                urlopen(unauthorized)
            self.assertEqual(denied.exception.code, 403)
            self.assertEqual(self.stored_timeline("base"), original_bytes)

            headers = {
                "Content-Type": "application/json",
                "Cookie": f"archergate_session={token.decode()}",
                "X-ArcherGate-Workspace": "1",
            }
            request = Request(url, data=json.dumps(payload).encode(), method="POST", headers=headers)
            with urlopen(request) as response:
                result = json.loads(response.read())
                self.assertEqual(response.status, 200)
            self.assertTrue(result["valid"])

            parent_request = Request(
                f"http://127.0.0.1:{server.server_port}/api/timeline/parent?id=api-insert",
                headers={"Cookie": f"archergate_session={token.decode()}"},
            )
            with urlopen(parent_request) as response:
                self.assertEqual(json.loads(response.read()), {"parent_id": "base"})

            inserted = timeline.load(self.library, "api-insert")["tracks"][0]["clips"][-1]
            trim_payload = {
                "base_id": "api-insert", "clip_id": inserted["id"], "edge": "out", "amount": 1,
                "revision_id": "api-trim",
            }
            trim_request = Request(
                f"http://127.0.0.1:{server.server_port}/api/timeline/trim",
                data=json.dumps(trim_payload).encode(), method="POST", headers=headers,
            )
            with urlopen(trim_request) as response:
                self.assertTrue(json.loads(response.read())["valid"])
            self.assertEqual(result["id"], "api-insert")
            self.assertEqual(len(timeline.load(self.library, "api-insert")["tracks"][0]["clips"]), 2)
            self.assertEqual(self.stored_timeline("base"), original_bytes)

            invalid_payload = {**payload, "source_start": 72, "revision_id": "api-failed"}
            invalid = Request(url, data=json.dumps(invalid_payload).encode(), method="POST", headers=headers)
            with self.assertRaises(HTTPError) as rejected:
                urlopen(invalid)
            self.assertEqual(rejected.exception.code, 400)
            self.assertIn("outside the asset's video stream", rejected.exception.read().decode())
            self.assertEqual(self.stored_timeline("base"), original_bytes)
            self.assertNotIn("api-failed", [row["id"] for row in timeline.list_timelines(self.library)])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assert_sources_unchanged()


if __name__ == "__main__":
    unittest.main()
