import tempfile
import unittest
from pathlib import Path

from worker.paths import AppPaths
from worker.providers.fake import FakeProvider
from worker.resources import Estimate
from worker.service import GenerationService


class StageTwoServiceTests(unittest.TestCase):
    def test_recovery_preview_and_action_only_touch_partial_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            service = GenerationService(AppPaths.under(Path(temp)), FakeProvider(), Estimate(1, True))
            stranded = service.paths.outputs / ".partial" / "stranded"
            stranded.mkdir(parents=True)
            (stranded / "incomplete.mp4").write_bytes(b"partial")
            final = service.paths.outputs / "immutable-output"
            final.mkdir()
            (final / "video.mp4").write_bytes(b"final")

            self.assertEqual(service.recovery_preview()["partial_output_count"], 1)
            result = service.recover()

            self.assertTrue(result["recovered"])
            self.assertFalse(stranded.exists())
            self.assertTrue((final / "video.mp4").is_file())
