import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from worker.providers.base import InsufficientMemoryError
from worker.providers.qwen_image_edit import QwenImageEditError, QwenImageEditProvider


class QwenImageEditProviderTests(unittest.TestCase):
    def test_measured_profile_requires_complete_positive_mps_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = QwenImageEditProvider(root / "snapshot", root / "measured-profile.json")
            with self.assertRaisesRegex(QwenImageEditError, "missing or invalid"):
                provider.measured_profile()
            path = root / "measured-profile.json"
            path.write_text(json.dumps({
                "width": 512, "height": 512, "steps": 4, "guidance_scale": 1.0,
                "true_cfg_scale": 1.0, "max_sequence_length": 512, "dtype": "bfloat16",
                "estimated_disk_bytes": 1, "peak_rss_bytes": 1, "wall_seconds": 1.0,
            }))
            measured = provider.measured_profile()
            self.assertEqual(measured.width, 512)
            self.assertEqual(measured.dtype, "bfloat16")


class QwenImageEditCalibrationTests(unittest.TestCase):
    def _provider(self, temp: str) -> QwenImageEditProvider:
        root = Path(temp)
        return QwenImageEditProvider(root / "snapshot", root / "measured-profile.json")

    def test_refuses_below_memory_floor_without_touching_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            provider._load = MagicMock()
            with patch("worker.providers.qwen_image_edit.total_system_memory_bytes", return_value=1024):
                with self.assertRaises(InsufficientMemoryError):
                    provider.calibrate("Balanced", None, lambda *_: None, lambda: False)
            provider._load.assert_not_called()

    def test_calibrate_measures_the_fixed_recipe_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            fake_image = MagicMock()
            fake_pipeline = MagicMock(return_value=MagicMock(images=[fake_image]))
            provider._load = MagicMock(return_value=fake_pipeline)
            with patch("worker.providers.qwen_image_edit.total_system_memory_bytes", return_value=999 * 1024**3):
                result = provider.calibrate("Balanced", None, lambda *_: None, lambda: False)
        self.assertEqual((result["width"], result["height"], result["steps"]), (512, 512, 4))
        self.assertGreaterEqual(result["wall_seconds"], 0)
        fake_image.save.assert_called_once()
