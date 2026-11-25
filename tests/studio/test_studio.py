import json
import os
import unittest
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from archergate.media import ExperimentError, sha256
from archergate.studio.fixtures import make_sources
from archergate.studio.library import Library, binary, input_args, run


class StudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        cls.sources = make_sources(cls.root / "fixtures", os.getenv("ARCHERGATE_TEST_SPEECH_WAV"))
        cls.library = Library(cls.root / "data")
        cls.hashes = [sha256(source) for source in cls.sources]
        cls.assets = [cls.library.import_media(source, "Synthetic fixture", "Locally generated")
                      for source in cls.sources]

    def test_imports_all_formats_and_preserves_sources(self):
        self.assertEqual({source.suffix for source in self.sources}, {".mp4", ".mov", ".wav", ".mp3"})
        for source, digest, asset in zip(self.sources, self.hashes, self.assets):
            self.assertEqual(sha256(source), digest)
            self.assertEqual(sha256(self.library.original(asset)), digest)
            self.assertEqual(asset["sha256"], digest)
            self.assertEqual(asset["transcript"]["state"], "unavailable")
        self.assertEqual([row["name"] for row in Library(self.library.root).search("delayed")],
                         ["Synthetic Delayed Audio.mov"])

    def test_proxy_timing_and_analysis(self):
        picture, delayed, sound, music = self.assets
        self.assertEqual(picture["waveform"]["state"], "unavailable")
        self.assertEqual(picture["shots"]["candidates"][0]["time"], "1")
        self.assertGreater(self.library.path("media", picture["id"], "thumbnail.jpg").stat().st_size, 0)
        self.assertEqual(delayed["source"]["streams"]["audio"]["start"], "1/4")
        self.assertLessEqual(abs(Fraction(delayed["proxy"]["stream_start_deltas"]["audio"])), Fraction(1, 40))
        self.assertEqual(delayed["waveform"]["start"], "1/4")
        self.assertEqual(len(delayed["waveform"]["peaks"]), 20)
        for asset in (sound, music):
            self.assertEqual(asset["proxy"]["file"], "proxy.m4a")
            self.assertEqual(asset["waveform"]["state"], "complete")
            self.assertGreater(max(asset["waveform"]["peaks"]), 0.05)
        for asset in (picture, delayed):
            original = self.timestamps(self.library.original(asset))
            proxy = self.timestamps(self.library.path("media", asset["id"], asset["proxy"]["file"]))
            self.assertEqual(len(original), len(proxy))
            self.assertLessEqual(max(abs(a - b) for a, b in zip(original, proxy)), Fraction(1, 90000))

    def timestamps(self, path):
        data = json.loads(run(binary("ffprobe"), "-v", "error", *input_args(path), "-select_streams", "v",
                              "-show_frames", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json"))
        return [Fraction(frame["best_effort_timestamp_time"]) for frame in data["frames"]]

    def test_rejected_input_and_failed_analysis_are_distinct(self):
        invalid = self.root / "invalid.mov"
        invalid.write_bytes(b"not media")
        with self.assertRaises(ExperimentError):
            self.library.import_media(invalid, "Fixture", "Synthetic")
        self.assertEqual(len(self.library.search()), 4)
        extra = Library(self.root / "failed")
        asset = extra.import_media(self.sources[2], "Fixture", "Synthetic", backend=self.root / "missing", model="model")
        self.assertEqual(asset["transcript"]["state"], "failed")
        self.assertEqual(asset["waveform"]["state"], "complete")

    @unittest.skipUnless(all(os.getenv(name) for name in ("ARCHERGATE_TEST_SPEECH_WAV", "ARCHERGATE_WHISPER", "ARCHERGATE_WHISPER_MODEL")),
                         "Optional local speech executable, model and synthetic voice fixture required")
    def test_real_english_speech(self):
        library = Library(self.root / "transcribed")
        asset = library.import_media(self.sources[2], "Synthetic dialogue", "Locally generated",
                                     os.environ["ARCHERGATE_WHISPER"], os.environ["ARCHERGATE_WHISPER_MODEL"])
        result = asset["transcript"]
        self.assertEqual(result["state"], "complete", result)
        self.assertIn("documentary", " ".join(row["text"].lower() for row in result["segments"]))
        self.assertTrue(all(Fraction(row["end"]) > Fraction(row["start"]) for row in result["segments"]))
        from archergate.studio import timeline

        edit = next(timeline.demo_timelines([self.assets[0], None, asset, self.assets[3]]))
        segment = result["segments"][0]
        middle = (Fraction(segment["start"]) + Fraction(segment["end"])) / 2
        edit["tracks"][1]["clips"][0].update(source_start=int(middle * 24), duration=1)
        edit["tracks"] = [edit["tracks"][1]]
        report = timeline.inspect(library, edit)
        self.assertTrue(report["valid"])
        self.assertIn("possible-speech-interruption", [row["code"] for row in report["issues"]])

    def test_independent_tracks_and_editorial_reviews(self):
        from archergate.studio import timeline

        j, l = timeline.demo_timelines(self.assets)
        self.assertEqual(timeline.inspect(self.library, j)["split_edits"][0]["kinds"], ["J-cut"])
        self.assertEqual(timeline.inspect(self.library, l)["split_edits"][0]["kinds"], ["L-cut"])
        timeline.save(self.library, j)
        timeline.save(self.library, l)
        changes = timeline.compare(self.library, j["id"], l["id"])["changes"]
        self.assertEqual([row["clip"] for row in changes], ["voice"])
        self.assertEqual(changes[0]["right"]["at"], "1")
        before = json.dumps(l)
        self.assertEqual(timeline.inspect(self.library, l)["duration_ticks"], 84)
        self.assertEqual(json.dumps(l), before)
        l["tracks"][1]["clips"][0]["source_start"] = 500
        result = timeline.inspect(self.library, l)
        self.assertFalse(result["valid"])
        self.assertIn("source-range", [row["code"] for row in result["issues"]])
        straight = next(timeline.demo_timelines(self.assets))
        picture = straight["tracks"][0]["clips"][0]
        straight["tracks"][0]["clips"].append({**picture, "id": "next-picture", "at": 60, "duration": 12})
        self.assertTrue(timeline.inspect(self.library, straight)["valid"])
        straight["tracks"][0]["clips"][1]["at"] = 59
        self.assertIn("track-overlap", [row["code"] for row in timeline.inspect(self.library, straight)["issues"]])

    def test_xml_references_originals_and_exact_offsets(self):
        import xml.etree.ElementTree as ET
        from archergate.studio import interchange, timeline

        first, second = timeline.demo_timelines(self.assets)
        for manifest, start in ((first, 0), (second, 24)):
            root = ET.fromstring(interchange.build(self.library, manifest))
            self.assertEqual(root.attrib, {"version": "5"})
            self.assertEqual(root.findtext("./sequence/rate/timebase"), "24")
            audio = root.findall("./sequence/media/audio/track/clipitem")
            video = root.find("./sequence/media/video/track/clipitem")
            self.assertEqual((video.findtext("start"), video.findtext("end")), ("24", "60"))
            self.assertEqual((audio[0].findtext("start"), audio[0].findtext("end")), (str(start), str(start + 48)))
            self.assertEqual(audio[1].findtext("start"), "48")
            self.assertEqual(audio[2].findtext("sourcetrack/trackindex"), "2")
            self.assertEqual(audio[2].findtext("file/pathurl"), None)
            self.assertEqual(audio[0].findtext("file/pathurl"),
                             interchange.path_url(self.library.original(self.assets[2])))
            self.assertIn("Synthetic%20Interview.mp4", video.findtext("file/pathurl"))
        second["fps"] = "30000/1001"
        with self.assertRaisesRegex(ExperimentError, "between frames"):
            interchange.build(self.library, second)

    def test_history_is_durable_and_separate_from_observations(self):
        from archergate.studio import memory

        notes = {"kind": "suggestion", "title": "Hold on the speaker",
                 "description": "Keep the full answer before changing angle.", "decision": "accepted"}
        record = memory.write(self.library, notes)
        memory.write(self.library, {**notes, "id": record["id"], "decision": "rejected"})
        changed = memory.write(self.library, {**notes, "id": record["id"], "decision": "modified",
                                            "revision": "Keep the full answer and let room tone carry the cut."})
        self.assertEqual([row["decision"] for row in changed["history"]], ["accepted", "rejected", "modified"])
        self.assertEqual(memory.records(Library(self.library.root), "suggestion"), [changed])
        self.assertEqual(self.library.asset(self.assets[0]["id"]), self.assets[0])
        editorial = {"kind": "annotation", "title": "Pause", "description": "Leave this breath in.",
                     "timestamp": "1.25", "asset": self.assets[0]["id"]}
        self.assertEqual(memory.write(self.library, editorial)["timestamp"], "5/4")
        with self.assertRaisesRegex(ExperimentError, "outside"):
            memory.write(self.library, {**editorial, "timestamp": "500"})
