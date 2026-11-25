import cmath
import math
import os
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from archergate.media import ExperimentError, sha256
from archergate.studio import fixtures, operations, preview, timeline
from archergate.studio.library import Library, binary, ffmpeg, input_args, probe


class PreviewTests(unittest.TestCase):
    def test_fades_crossfades_and_automation_at_decoded_boundaries(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = Library(root / "work")
            assets = []
            for name, colour, tone in (("lower", "0x404040", 330), ("upper", "white", 550)):
                path = root / f"{name}.mp4"
                ffmpeg("-f", "lavfi", "-i", f"color={colour}:s=160x90:r=24:d=4",
                       "-f", "lavfi", "-i", f"sine=f={tone}:r=48000:d=4",
                       "-c:v", "libx264", "-c:a", "aac", path)
                assets.append(library.import_media(path, "Synthetic boundary fixture", "Generated locally"))
            tracks = []
            for name, kind, index, at, duration in (
                ("lower-video", "video", 0, 0, 48), ("upper-video", "video", 1, 24, 72),
                ("old-audio", "audio", 0, 0, 60), ("new-audio", "audio", 1, 36, 60),
            ):
                tracks.append({"id": name, "kind": kind, "clips": [
                    {"id": name, "asset": assets[index]["id"], "at": at, "source_start": 0, "duration": duration},
                ]})
            base = {"version": 2, "id": "base", "title": "Boundary fixture", "timebase": "1/24",
                    "fps": "24", "tracks": tracks}
            timeline.save(library, base)
            operations.apply(library, "base", {"id": "fades", "title": "Fades and automation", "operations": [
                {"clip": "upper-video", "effects": {"fade_in": 24, "fade_out": 24}},
                {"clip": "old-audio", "effects": {"fade_out": 24}},
                {"clip": "new-audio", "effects": {"fade_in": 24, "volume_points": [
                    {"tick": 0, "gain_db": 0}, {"tick": 24, "gain_db": 0},
                    {"tick": 48, "gain_db": -12}, {"tick": 60, "gain_db": -12},
                ]}},
            ]})
            unchanged = library.path("previews", preview.render(library, "base") + ".mp4")
            path = library.path("previews", preview.render(library, "fades") + ".mp4")
            self.assertEqual(probe(path)["duration"], "4")
            def luma(file):
                return ffmpeg(*input_args(file), "-an", "-vf", "extractplanes=y,crop=1:1:0:0",
                              "-f", "rawvideo", "-")

            original_luma, luminance = luma(unchanged), luma(path)
            self.assertEqual(len(luminance), 96)
            self.assertEqual(luminance[:24], original_luma[:24])
            self.assertAlmostEqual(luminance[24], luminance[23], delta=2)
            self.assertAlmostEqual(luminance[36], (luminance[23] + luminance[48]) / 2, delta=2)
            self.assertEqual(luminance[48:73], original_luma[48:73])
            self.assertAlmostEqual(luminance[84], (luminance[72] + 16) / 2, delta=2)
            self.assertTrue(16 <= luminance[95] <= 27, luminance[95])

            def tones(file, time):
                raw = ffmpeg("-ss", str(time), *input_args(file), "-t", "0.1",
                             "-vn", "-ac", "1", "-ar", "48000", "-f", "s16le", "-")
                samples = [value for (value,) in struct.iter_unpack("<h", raw)]
                self.assertEqual(len(samples), 4800)
                def amplitude(hz):
                    step = 2j * math.pi * hz / 48000
                    return abs(sum(sample * cmath.exp(step * index) for index, sample in enumerate(samples)))
                return [amplitude(hz) for hz in (330, 550)]

            before, no_audio_transition_yet, middle, after, automated, settled = (
                tones(path, t) for t in (0.45, 1.2, 1.95, 2.5, 2.95, 3.65))
            for actual in (before, no_audio_transition_yet):
                self.assertAlmostEqual(actual[0] / before[0], 1, delta=0.08)
                self.assertLess(actual[1] / before[0], 0.03)
            self.assertAlmostEqual(before[0] / tones(unchanged, 0.45)[0], 1, delta=0.04)
            self.assertAlmostEqual(middle[0] / before[0], 0.5, delta=0.08)
            self.assertAlmostEqual(middle[1] / before[0], 0.5, delta=0.08)
            self.assertLess(after[0] / after[1], 0.03)
            self.assertAlmostEqual(automated[1] / before[0], 10 ** (-6 / 20), delta=0.08)
            self.assertAlmostEqual(settled[1] / before[0], 10 ** (-12 / 20), delta=0.04)
            self.assertEqual(timeline.load(library, "base"), base)
            for asset in assets:
                self.assertEqual(sha256(library.verify_original(asset)), asset["sha256"])
            for effect in ({"fade_in": True}, {"fade_in": 48, "fade_out": 25}, {"volume_points": []}):
                with self.assertRaises(ExperimentError):
                    operations.check_effects("audio", 72, effect)

    def test_failed_render_preserves_verified_outputs(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = Library(root / "work")
            assets = [library.import_media(path, "Synthetic preview", "Generated locally")
                      for path in fixtures.make_sources(root / "fixtures")]
            for edit in timeline.demo_timelines(assets):
                timeline.save(library, edit)
            key = preview.render(library, "interview-j")
            folder = library.path("previews")
            good_files = {path.name: sha256(path) for path in folder.iterdir()}
            # Invoke a real binary that rejects FFmpeg's encode arguments.
            with patch.dict(os.environ, {"ARCHERGATE_FFMPEG": str(binary("ffprobe"))}):
                with self.assertRaises(ExperimentError):
                    preview.render(library, "interview-l")
            self.assertEqual({path.name: sha256(path) for path in folder.iterdir()}, good_files)
            with preview.open_cached(library, key) as stream:
                self.assertIsNotNone(stream)
            for asset in assets:
                self.assertEqual(sha256(library.original(asset)), asset["sha256"])

    def test_two_composed_revisions_play_and_preserve_sources(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = Library(root / "work")
            assets = [library.import_media(path, "Synthetic preview", "Local fixture")
                      for path in fixtures.make_sources(root / "fixtures")]
            paths = []
            for edit in timeline.demo_timelines(assets):
                timeline.save(library, edit)
                key = preview.render(library, edit["id"])
                path = library.path("previews", key + ".mp4")
                self.assertEqual(preview.render(library, edit["id"]), key)
                info = probe(path)
                self.assertEqual(set(info["streams"]), {"audio", "video"})
                self.assertEqual(info["duration"], "7/2")
                ffmpeg(*input_args(path), "-map", "0", "-f", "null", "-")
                paths.append(path)
            peaks = []
            for path in paths:
                raw = ffmpeg("-ss", "0.25", *input_args(path), "-t", "0.10", "-vn", "-ac", "1", "-f", "s16le", "-")
                peaks.append(max(abs(value) for (value,) in struct.iter_unpack("<h", raw)))
            self.assertGreater(peaks[0], 1_000)
            self.assertEqual(peaks[1], 0)
            for asset in assets:
                self.assertEqual(sha256(library.original(asset)), asset["sha256"])

    def test_structured_push_in_renders_and_cannot_be_silently_exported(self):
        from archergate.media import ExperimentError
        from archergate.studio import interchange, operations

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Synthetic moving pattern.mp4"
            ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=2",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", source)
            library = Library(root / "work")
            asset = library.import_media(source, "Synthetic", "Generated locally")
            base = {"id": "base", "title": "Base", "version": 2, "timebase": "1/24", "fps": "24",
                    "tracks": [{"id": "video", "kind": "video", "clips": [
                        {"id": "picture", "asset": asset["id"], "at": 0, "source_start": 0, "duration": 48}]}]}
            timeline.save(library, base)
            proposal = {"id": "push-in", "title": "Push in", "operations": [{
                "clip": "picture", "effects": {"transform": [
                    {"tick": 0, "scale": 1, "x": 0.5, "y": 0.5, "ease": "smoothstep"},
                    {"tick": 48, "scale": 1.4, "x": 0.2, "y": 0.5, "ease": "linear"},
                ]}}]}
            operations.apply(library, "base", proposal)
            self.assertEqual(timeline.load(library, "base"), base)
            files = [library.path("previews", preview.render(library, name) + ".mp4") for name in ("base", "push-in")]
            images = [ffmpeg("-ss", "1.75", *input_args(path), "-frames:v", "1", "-vf", "scale=16:9",
                              "-pix_fmt", "gray", "-f", "rawvideo", "-") for path in files]
            self.assertGreater(sum(abs(a - b) for a, b in zip(*images)), 800)
            with self.assertRaisesRegex(ExperimentError, "cannot carry cinematic effects"):
                interchange.build(library, timeline.load(library, "push-in"))
            proposal["operations"][0]["effects"] = {"command": "unsafe"}
            with self.assertRaisesRegex(ExperimentError, "Unsupported"):
                operations.apply(library, "base", proposal)
            self.assertEqual(sha256(source), asset["sha256"])

    def test_effect_revision_retry_is_idempotent(self):
        from archergate.studio import operations

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Synthetic moving pattern.mp4"
            ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=2",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", source)
            library = Library(root / "work")
            asset = library.import_media(source, "Synthetic", "Generated locally")
            base = {"id": "base", "title": "Base", "version": 2, "timebase": "1/24", "fps": "24",
                    "tracks": [{"id": "video", "kind": "video", "clips": [
                        {"id": "picture", "asset": asset["id"], "at": 0, "source_start": 0, "duration": 48}]}]}
            timeline.save(library, base)
            proposal = {"id": "push-in", "title": "Push in", "operations": [{
                "clip": "picture", "effects": {"transform": [
                    {"tick": 0, "scale": 1, "x": 0.5, "y": 0.5, "ease": "smoothstep"},
                    {"tick": 48, "scale": 1.4, "x": 0.2, "y": 0.5, "ease": "linear"},
                ]}}]}

            first = operations.apply(library, "base", proposal)
            retry = operations.apply(library, "base", proposal)
            self.assertEqual(first, retry)
            self.assertEqual(timeline.load(library, "base"), base)
            with self.assertRaisesRegex(ExperimentError, "different command"):
                operations.apply(library, "base", {**proposal, "title": "Different edit"})
