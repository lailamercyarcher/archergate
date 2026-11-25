"""Decode the actual exports, plus exercise manifest and source errors."""

import array
import json
import math
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from archergate import media
from archergate.fixtures import generate_fixtures

ORDERS = {
    "edit-a": ("signal", "transit", "echo"),
    "edit-b": ("echo", "signal", "transit"),
}
COLORS = {"signal": (185, 70, 50), "transit": (36, 99, 79), "echo": (48, 94, 156)}
TONES = {"signal": 330, "transit": 440, "echo": 550}


def inspect_export(path, manifest, hashes):
    info = media.validate_export(path, 144)
    if float(info["format"]["duration"]) != 6:
        raise AssertionError("Expected six seconds of MP4")
    receipt = json.loads(info["format"]["tags"]["comment"])
    if receipt["manifest"] != manifest or receipt["sources"] != hashes:
        raise AssertionError("Embedded manifest or source hashes differ")
    media.run_tool("ffmpeg", "-v", "error", "-xerror", "-i", path, "-map", "0", "-f", "null", "-")
    visual, audio = [], []
    for center in (1, 3, 5):
        pixel = media.run_tool(
            "ffmpeg", "-v", "error", "-ss", str(center), "-i", path,
            "-frames:v", "1", "-vf", "crop=2:2:606:334,scale=1:1",
            "-pix_fmt", "rgb24", "-f", "rawvideo", "-"
        )
        candidates = sorted((math.dist(pixel, rgb), asset) for asset, rgb in COLORS.items())
        if candidates[0][0] >= 10:
            raise AssertionError(f"Unexpected slate color {tuple(pixel)}")
        visual.append(candidates[0][1])
        pcm = media.run_tool(
            "ffmpeg", "-v", "error", "-ss", str(center), "-i", path,
            "-t", "0.1", "-vn", "-f", "s16le", "-"
        )
        samples = array.array("h", pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        crossings = [index - a / (b - a) for index, (a, b) in enumerate(zip(samples, samples[1:])) if a <= 0 < b]
        audio.append(round((len(crossings) - 1) * media.SAMPLE_RATE / (crossings[-1] - crossings[0])))
    expected = ORDERS[manifest["id"]]
    if tuple(visual) != expected or tuple(audio) != tuple(TONES[asset] for asset in expected):
        raise AssertionError(f"Decoded {visual} and {audio} Hz, expected {expected}")
    return visual, audio


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.workspace = Path(temp.name)
        generate_fixtures(cls.workspace)
        cls.originals = media.read_registry(cls.workspace)
        cls.hashes = {asset: media.sha256(cls.workspace / row["path"]) for asset, row in cls.originals.items()}
        cls.outputs = [
            media.render(cls.workspace, media.MANIFESTS / f"{edit}.json") for edit in ORDERS
        ]

    def test_both_outputs_decode_in_distinct_orders_and_preserve_sources(self):
        self.assertEqual(len(self.originals), 3)
        for output in self.outputs:
            path = self.workspace / output["path"]
            self.assertEqual(media.sha256(path), output["sha256"])
            order, tones = inspect_export(path, output["manifest"], self.hashes)
            print(f"{path.name}: 144 frames, 6.000 s, {', '.join(order)}, tones {tones} Hz", flush=True)
        self.assertNotEqual(self.outputs[0]["sha256"], self.outputs[1]["sha256"])
        for asset, record in self.originals.items():
            self.assertIn("synthetic fixture", record["provenance"])
            self.assertTrue(record["rights"])
            self.assertEqual(record["video"]["frames"], 72)
            self.assertEqual(record["sha256"], self.hashes[asset])
            self.assertEqual(media.sha256(self.workspace / record["path"]), self.hashes[asset])
        generate_fixtures(self.workspace)
        self.assertEqual(media.read_registry(self.workspace), self.originals)

    def test_comparison_reports_actual_timeline_and_source_trim(self):
        result = media.compare(*(media.named_manifest(edit) for edit in ORDERS))
        self.assertTrue(result["same_assets"] and result["same_asset_counts"] and result["order_changed"])
        for side, order in zip(("left", "right"), ORDERS.values()):
            cuts = result[side]["cuts"]
            self.assertEqual([row["asset"] for row in cuts], list(order))
            self.assertEqual([row["timeline_start"] for row in cuts], [0, 48, 96])
        self.assertEqual(result["left"]["cuts"][2]["start"], 24)
        self.assertEqual(result["right"]["cuts"][0]["start"], 12)

    def test_invalid_manifests_report_specific_errors(self):
        baseline = media.named_manifest("edit-a")
        path = self.workspace / "invalid.json"
        for content, error in (
            ("{broken", "Invalid JSON"),
            ('{"id":"a","id":"b"}', "duplicate JSON field"),
            (json.dumps({**baseline, "fps": 30}), "Manifest fps"),
            (json.dumps({**baseline, "id": "../escape"}), "identifier"),
            (json.dumps({**baseline, "cuts": []}), "1–24 cuts"),
            (json.dumps({**baseline, "cuts": [{"asset": "echo", "start": True, "end": 20}]}), "integer frames"),
            (json.dumps({**baseline, "cuts": [{"asset": "echo", "start": 20, "end": 20}]}), "integer frames"),
            (json.dumps({**baseline, "cuts": [{"asset": "echo", "start": 0, "end": 73}]}), "beyond"),
            (json.dumps({**baseline, "cuts": [{"asset": "unknown", "start": 0, "end": 20}]}), "Unknown asset"),
        ):
            with self.subTest(error=error):
                path.write_text(content)
                result = self.cli("render", path)
                self.assertEqual(result.returncode, 1)
                self.assertIn(error, result.stderr)

    def test_missing_changed_and_linked_sources_preserve_previous_export(self):
        source = self.workspace / "sources" / "signal.mp4"
        moved = source.with_suffix(".saved")
        output = self.workspace / "renders" / "edit-a.mp4"
        digest = media.sha256(output)
        path = media.MANIFESTS / "edit-a.json"
        source.rename(moved)
        try:
            result = self.cli("render", path)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Missing source signal", result.stderr)
            source.symlink_to(moved)
            try:
                self.assertIn("Symbolic links are unsupported", self.cli("render", path).stderr)
            finally:
                source.unlink()
            source.write_bytes(b"changed")
            self.assertIn("no longer matches its registered SHA-256", self.cli("render", path).stderr)
        finally:
            moved.replace(source)
        self.assertEqual(media.sha256(output), digest)

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "archergate", "--workspace", self.workspace, *args],
            capture_output=True, text=True, timeout=15,
        )
