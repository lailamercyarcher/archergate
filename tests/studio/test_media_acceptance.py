import json
import shutil
import unittest
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from archergate.media import ExperimentError, sha256
from archergate.studio import fixtures, operations, preview, timeline
from archergate.studio.library import Library, ffmpeg, input_args, probe

from media_acceptance import decoded_audio, decoded_luma, decoded_video_timestamps


class MediaAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        generated = fixtures.make_sources(cls.root / "fixtures")
        unusual = cls.root / "fixtures" / "scene, lumière 01.mp4"
        shutil.copyfile(generated[0], unusual)
        cls.sources = (unusual, *generated[1:])
        cls.source_hashes = {path: sha256(path) for path in generated}
        cls.source_hashes[unusual] = sha256(unusual)

        cls.library = Library(cls.root / "workspace")
        cls.assets = [
            cls.library.import_media(path, "Synthetic acceptance fixture", "Generated locally", profile="compact")
            for path in cls.sources
        ]
        cls.picture, cls.delayed, cls.dialogue, cls.music = cls.assets

    def stored_timeline(self, timeline_id):
        with self.library.connect() as db:
            row = db.execute("SELECT details FROM timelines WHERE id = ?", (timeline_id,)).fetchone()
        return row[0].encode("utf-8")

    def test_import_persists_metadata_and_rejects_unsupported_inputs_safely(self):
        self.assertEqual(self.picture["name"], "scene, lumière 01.mp4")
        self.assertEqual(set(self.picture["source"]["streams"]), {"video"})
        self.assertEqual(set(self.delayed["source"]["streams"]), {"video", "audio"})
        self.assertEqual(self.dialogue["source"]["streams"]["audio"]["channels"], 1)
        self.assertEqual(self.music["source"]["streams"]["audio"]["channels"], 2)
        self.assertEqual(self.delayed["source"]["streams"]["video"]["avg_frame_rate"], "30000/1001")
        self.assertEqual(self.delayed["source"]["streams"]["audio"]["start"], "1/4")

        for path, asset in zip(self.sources, self.assets):
            with self.subTest(filename=path.name):
                digest = self.source_hashes[path]
                self.assertEqual(asset["sha256"], digest)
                self.assertEqual(sha256(path), digest)
                self.assertEqual(sha256(self.library.original(asset)), digest)
                self.assertEqual(self.library.asset(asset["id"]), asset)

        before_ids = {row["id"] for row in self.library.search()}
        before_media = {path.name for path in self.library.path("media").iterdir()}
        unsupported = self.root / "unsupported.mkv"
        shutil.copyfile(self.library.original(self.delayed), unsupported)
        with self.assertRaisesRegex(ExperimentError, "Choose a local MP4, MOV, WAV or MP3 file"):
            self.library.import_media(unsupported, "Synthetic test", "Generated locally")
        self.assertEqual({row["id"] for row in self.library.search()}, before_ids)
        self.assertEqual({path.name for path in self.library.path("media").iterdir()}, before_media)

        malformed = self.root / "malformed.mp4"
        malformed.write_bytes(b"not a media stream")
        with self.assertRaises(ExperimentError) as failure:
            self.library.import_media(malformed, "Synthetic test", "Generated locally")
        self.assertTrue(str(failure.exception), "Malformed supported-extension input should report a useful error.")
        self.assertEqual({row["id"] for row in self.library.search()}, before_ids)
        self.assertEqual({path.name for path in self.library.path("media").iterdir()}, before_media)
        self.assertEqual({path: sha256(path) for path in self.source_hashes}, self.source_hashes)

    def test_fractional_multitrack_insert_renders_expected_picture_audio_and_recovers(self):
        timebase = "1001/30000"
        base = {
            "version": 2,
            "id": "fractional-base",
            "title": "Synthetic fractional-rate acceptance edit",
            "timebase": timebase,
            "fps": "30000/1001",
            "tracks": [
                {"id": "picture", "kind": "video", "clips": [{
                    "id": "fractional-source", "asset": self.delayed["id"],
                    "at": 0, "source_start": 0, "duration": 30,
                }]},
                {"id": "sound", "kind": "audio", "clips": [{
                    "id": "offset-tone", "asset": self.delayed["id"],
                    "at": 0, "source_start": 8, "duration": 15,
                }]},
            ],
        }
        timeline.save(self.library, base)
        original_revision = self.stored_timeline(base["id"])
        revision_count = len(timeline.list_timelines(self.library))

        with self.assertRaisesRegex(ExperimentError, "overlaps clip fractional-source"):
            operations.insert_source_range(
                self.library, base["id"], self.picture["id"], "picture",
                source_start=30, duration=1, at=29, revision_id="rejected-overlap",
            )
        self.assertEqual(self.stored_timeline(base["id"]), original_revision)
        self.assertNotIn("rejected-overlap", [row["id"] for row in timeline.list_timelines(self.library)])
        self.assertEqual(len(timeline.list_timelines(self.library)), revision_count)

        first_result = operations.insert_source_range(
            self.library, base["id"], self.picture["id"], "picture",
            source_start=30, duration=30, at=30, revision_id="fractional-picture",
        )
        self.assertTrue(first_result["valid"], first_result)
        first_revision = self.stored_timeline("fractional-picture")
        final_result = operations.insert_source_range(
            self.library, "fractional-picture", self.music["id"], "sound",
            source_start=0, duration=15, at=30, revision_id="fractional-acceptance",
        )
        self.assertTrue(final_result["valid"], final_result)

        manifest = timeline.load(self.library, "fractional-acceptance")
        self.assertEqual(manifest["fps"], "30000/1001")
        self.assertEqual(manifest["timebase"], timebase)
        self.assertEqual(
            [(clip["at"], clip["source_start"], clip["duration"])
             for clip in manifest["tracks"][0]["clips"]],
            [(0, 0, 30), (30, 30, 30)],
        )
        self.assertEqual(
            [(clip["at"], clip["duration"]) for clip in manifest["tracks"][1]["clips"]],
            [(0, 15), (30, 15)],
        )

        key = preview.render(self.library, "fractional-acceptance")
        rendered = self.library.path("previews", key + ".mp4")
        info = probe(rendered)
        self.assertEqual(set(info["streams"]), {"audio", "video"}, info)
        self.assertEqual(info["streams"]["video"]["r_frame_rate"], "30000/1001", info)
        self.assertEqual(info["streams"]["audio"]["channels"], 2, info)
        self.assertLessEqual(abs(Fraction(info["duration"]) - Fraction(60) * Fraction(timebase)), Fraction(1, 100))
        ffmpeg(*input_args(rendered), "-map", "0", "-f", "null", "-")

        timestamps = decoded_video_timestamps(rendered)
        self.assertEqual(timestamps["rate"], "30000/1001", timestamps)
        self.assertEqual(timestamps["frame_count"], 60, timestamps)
        steps = [right - left for left, right in zip(timestamps["timestamps"], timestamps["timestamps"][1:])]
        self.assertTrue(
            all(abs(step - Fraction(1001, 30000)) <= Fraction(1, 1_000_000) for step in steps),
            timestamps,
        )

        first_picture = decoded_luma(rendered, 0.5)
        inserted_picture = decoded_luma(rendered, 1.5)
        self.assertGreaterEqual(first_picture, 70, f"source picture luma={first_picture}; probe={info!r}")
        self.assertLessEqual(first_picture, 115, f"source picture luma={first_picture}; probe={info!r}")
        self.assertGreater(inserted_picture, 200, f"inserted picture luma={inserted_picture}; probe={info!r}")

        source_tone = decoded_audio(rendered, 0.1, 0.2)
        silence_gap = decoded_audio(rendered, 0.7, 0.2)
        inserted_tone = decoded_audio(rendered, 1.2, 0.2)
        trailing_silence = decoded_audio(rendered, 1.7, 0.2)
        self.assertAlmostEqual(source_tone["frequency_hz"], 440, delta=12, msg=f"decoded metrics: {source_tone}")
        self.assertLess(silence_gap["peak"], 100, f"expected intentional silence, got {silence_gap}")
        self.assertAlmostEqual(inserted_tone["frequency_hz"], 550, delta=12, msg=f"decoded metrics: {inserted_tone}")
        self.assertLess(trailing_silence["peak"], 100, f"expected trailing silence, got {trailing_silence}")

        self.assertEqual(self.stored_timeline(base["id"]), original_revision)
        self.assertEqual(self.stored_timeline("fractional-picture"), first_revision)
        for path, digest in self.source_hashes.items():
            self.assertEqual(sha256(path), digest, f"fixture changed: {path}")
        for asset in self.assets:
            self.assertEqual(sha256(self.library.original(asset)), asset["sha256"], asset["name"])


if __name__ == "__main__":
    unittest.main()
