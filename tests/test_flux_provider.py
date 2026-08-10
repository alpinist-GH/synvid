import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from worker.providers.base import InsufficientMemoryError
from worker.providers.flux import FluxProviderError, FluxSchnellProvider


class FluxProviderTests(unittest.TestCase):
    def test_rejects_missing_or_invalid_measured_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = FluxSchnellProvider(root / "snapshot", root / "measured-profile.json")
            with self.assertRaisesRegex(FluxProviderError, "missing or invalid"):
                provider.measured_profile()

            (root / "measured-profile.json").write_text(json.dumps({
                "width": 0, "height": 512, "steps": 4, "guidance_scale": 0.0,
                "max_sequence_length": 256, "dtype": "bfloat16",
                "estimated_disk_bytes": 1, "peak_rss_bytes": 1,
                "peak_mps_allocated_bytes": 0,
            }))
            with self.assertRaisesRegex(FluxProviderError, "invalid value"):
                provider.measured_profile()

    def test_accepts_complete_measured_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = {
                "width": 512, "height": 512, "steps": 4, "guidance_scale": 0.0,
                "max_sequence_length": 256, "dtype": "bfloat16",
                "estimated_disk_bytes": 1, "peak_rss_bytes": 1,
            }
            path = root / "measured-profile.json"
            path.write_text(json.dumps(profile))
            measured = FluxSchnellProvider(root / "snapshot", path).measured_profile()
            self.assertEqual(measured.width, 512)
            self.assertEqual(measured.dtype, "bfloat16")
            self.assertEqual(measured.peak_mps_allocated_bytes, 0)


class FluxCalibrationTests(unittest.TestCase):
    def _provider(self, temp: str) -> FluxSchnellProvider:
        root = Path(temp)
        return FluxSchnellProvider(root / "snapshot", root / "measured-profile.json")

    def test_refuses_below_memory_floor_without_touching_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            provider._load = MagicMock()
            with patch("worker.providers.flux.total_system_memory_bytes", return_value=1024):
                with self.assertRaises(InsufficientMemoryError):
                    provider.calibrate("Balanced", None, lambda *_: None, lambda: False)
            provider._load.assert_not_called()

    def test_calibrate_measures_the_fixed_recipe_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            fake_image = MagicMock()
            fake_pipeline = MagicMock(return_value=MagicMock(images=[fake_image]))
            provider._load = MagicMock(return_value=fake_pipeline)
            with patch("worker.providers.flux.total_system_memory_bytes", return_value=999 * 1024**3):
                result = provider.calibrate("Balanced", None, lambda *_: None, lambda: False)
        self.assertEqual((result["width"], result["height"], result["steps"]), (512, 512, 4))
        self.assertGreaterEqual(result["peak_rss_bytes"], 0)
        fake_image.save.assert_called_once()
