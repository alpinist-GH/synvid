import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from worker.providers.wan_mlx import WanMlxProvider, WanMlxProviderError


class WanMlxProviderTests(unittest.TestCase):
    def _provider(self, temp: str) -> WanMlxProvider:
        root = Path(temp)
        profile = {
            "width": 1280,
            "height": 704,
            "frames": 41,
            "fps": 24,
            "steps": 40,
            "guidance_scale": 5.0,
            "dtype": "bfloat16",
            "estimated_disk_bytes": 1,
            "peak_rss_bytes": 1,
            "peak_mlx_bytes": 1,
            "wall_time_seconds": 1.0,
        }
        measured = root / "measured-profile.json"
        measured.write_text(json.dumps({"recipes": {"Balanced": profile}}))
        return WanMlxProvider(root / "snapshot", measured)

    def test_measured_balanced_shape_is_the_only_supported_recipe(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            self.assertEqual(provider.facts.calibration_recipes, frozenset({"Balanced"}))
            self.assertEqual(provider.calibration_reference("Balanced")["width"], 1280)
            self.assertIsNone(provider.calibration_reference("Draft"))

    def test_run_accepts_the_measured_shape_and_rejects_other_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            provider._verify = MagicMock()
            provider._generate = MagicMock()
            request = SimpleNamespace(
                recipe="Balanced", prompt="a test", width=1280, height=704,
                frames=41, fps=24, steps=40, guidance_scale=5.0, seed=7,
                output_dir=Path(temp),
            )
            self.assertEqual(provider.run(request, lambda *_: None, lambda: False)["native_fps"], "24")
            request.width = 512
            with self.assertRaises(WanMlxProviderError):
                provider.run(request, lambda *_: None, lambda: False)
