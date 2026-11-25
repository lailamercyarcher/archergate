import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from archergate.media import ExperimentError
from archergate.studio import fixtures, memory, reasoning, timeline
from archergate.studio.library import Library


class ReasoningTests(unittest.TestCase):
    def test_consent_precedes_access_and_cost(self):
        with self.assertRaisesRegex(ExperimentError, "Approve sending"):
            reasoning.generate(None, {"key": "irrelevant"})

    def test_optional_context_is_selected_and_bounded(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            library = Library(root / "work")
            assets = [library.import_media(path, "Local source path should remain private", "Synthetic")
                      for path in fixtures.make_sources(root / "fixtures")]
            edit = next(timeline.demo_timelines(assets))
            memory.write(library, {"kind": "aesthetic", "title": "Quiet tension", "description": "Hold a quiet breath."})
            disabled = {"send": True, "frames": False, "dialogue": False, "memory": False}
            context, images = reasoning.context_for(library, edit, "Make this feel quietly wrong", disabled)
            self.assertEqual(images, [])
            self.assertEqual(context["director_preferences"], [])
            self.assertEqual(context["estimated_dialogue"], [])
            self.assertNotIn("remain private", str(context))
            enabled = reasoning.check_consent({**disabled, "frames": True, "memory": True})
            expanded, images = reasoning.context_for(library, edit, "Make this feel quietly wrong", enabled)
            self.assertEqual(len(images), 1)
            self.assertTrue(images[0][1].startswith(b"\xff\xd8"))
            self.assertEqual(expanded["director_preferences"][0]["title"], "Quiet tension")
            self.assertEqual(expanded["estimated_dialogue"], [])
