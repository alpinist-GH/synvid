import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from worker.providers.base import InsufficientMemoryError
from worker.providers.hunyuan import HunyuanMeasuredProfile, HunyuanProviderError, HunyuanVideo15Provider


class HunyuanProviderTests(unittest.TestCase):
    def test_missing_profile_is_unavailable_not_guessed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = HunyuanVideo15Provider(
                root / "snapshot",
                root / "measured-profile.json",
                "hunyuan15-480p-t2v",
            )
            with self.assertRaisesRegex(HunyuanProviderError, "missing or invalid"):
                provider.measured_profile()

    def test_accepts_only_measured_float_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "measured-profile.json"
            path.write_text(json.dumps({
                "width": 848, "height": 480, "frames": 13, "fps": 24,
                "steps": 8, "guidance_scale": 1.0, "dtype": "bfloat16",
                "estimated_disk_bytes": 53_400_000_000, "peak_rss_bytes": 1,
            }))
            profile = HunyuanMeasuredProfile.from_json(path)
            self.assertEqual(profile.width, 848)
            self.assertEqual(profile.dtype, "bfloat16")

    def test_rejects_invalid_dtype(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "measured-profile.json"
            path.write_text(json.dumps({
                "width": 848, "height": 480, "frames": 13, "fps": 24,
                "steps": 8, "guidance_scale": 1.0, "dtype": "float32",
                "estimated_disk_bytes": 1, "peak_rss_bytes": 1,
            }))
            with self.assertRaisesRegex(HunyuanProviderError, "unsupported strategy"):
                HunyuanMeasuredProfile.from_json(path)


class HunyuanCalibrationTests(unittest.TestCase):
    def _provider(self, temp: str) -> HunyuanVideo15Provider:
        root = Path(temp)
        return HunyuanVideo15Provider(root / "snapshot", root / "measured-profile.json", "hunyuan15-480p-t2v")

    def test_i2v_has_no_calibration_recipe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = HunyuanVideo15Provider(root / "snapshot", root / "measured-profile.json", "hunyuan15-480p-i2v")
            self.assertEqual(provider.facts.calibration_recipes, frozenset())
            self.assertIsNone(provider.calibration_reference("Balanced"))

    def test_refuses_below_memory_floor_without_touching_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            provider._load = MagicMock()
            with patch("worker.providers.hunyuan.total_system_memory_bytes", return_value=1024):
                with self.assertRaises(InsufficientMemoryError):
                    provider.calibrate("Balanced", None, lambda *_: None, lambda: False)
            provider._load.assert_not_called()

    def test_calibrate_measures_and_merges_into_existing_recipes(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            fake_pipeline = MagicMock(return_value=SimpleNamespace(frames=[None]))
            provider._load = MagicMock(return_value=fake_pipeline)
            existing = {"recipes": {"Draft": {"width": 1}}}
            with patch("worker.providers.hunyuan.total_system_memory_bytes", return_value=999 * 1024**3), \
                 patch("diffusers.utils.export_to_video"):
                result = provider.calibrate("Balanced", existing, lambda *_: None, lambda: False)
        self.assertEqual(result["recipes"]["Draft"], {"width": 1})
        balanced = result["recipes"]["Balanced"]
        self.assertEqual((balanced["width"], balanced["height"], balanced["frames"], balanced["steps"]), (848, 480, 25, 20))
        self.assertGreater(balanced["peak_rss_bytes"], 0)
        self.assertGreaterEqual(balanced["wall_time_seconds"], 0)
