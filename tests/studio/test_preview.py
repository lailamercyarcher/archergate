import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from archergate.media import ExperimentError, sha256
from archergate.studio import fixtures, preview, timeline
from archergate.studio.library import Library, ffmpeg, input_args, probe


class PreviewTests(unittest.TestCase):
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
