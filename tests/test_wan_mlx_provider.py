import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from worker.providers.wan_mlx import WanMlxProvider, WanMlxProviderError


class WanMlxProviderTests(unittest.TestCase):
    def _provider(self, temp: str, recipe_name: str = "Balanced", mode: str = "text") -> WanMlxProvider:
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
            "mode": mode,
        }
        measured = root / "measured-profile.json"
        recipes = {"Balanced": profile}
        if recipe_name != "Balanced":
            recipes[recipe_name] = {**profile, "mode": mode}
        measured.write_text(json.dumps({"recipes": recipes}))
        return WanMlxProvider(root / "snapshot", measured)

    def test_candidate_recipes_are_available_for_preparation_but_not_measured_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            self.assertIn("Balanced", provider.facts.calibration_recipes)
            self.assertIn("DraftLandscape", provider.facts.calibration_recipes)
            self.assertIn("HighLandscape", provider.facts.calibration_recipes)
            self.assertIn("BalancedSquare", provider.facts.calibration_recipes)
            self.assertIn("BalancedPortrait", provider.facts.calibration_recipes)
            self.assertIn("BalancedI2V", provider.facts.calibration_recipes)
            self.assertEqual(provider.calibration_reference("Balanced")["width"], 1280)
            self.assertEqual(provider.calibration_reference("BalancedI2V")["mode"], "image")

    def test_run_accepts_the_measured_shape_and_rejects_other_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            provider._verify = MagicMock()
            provider._generate = MagicMock()
            request = SimpleNamespace(
                recipe="Balanced", prompt="a test", width=1280, height=704,
                frames=41, fps=24, steps=40, guidance_scale=5.0, seed=7,
                output_dir=Path(temp), source_image=None,
            )
            self.assertEqual(provider.run(request, lambda *_: None, lambda: False)["native_fps"], "24")
            request.width = 512
            with self.assertRaises(WanMlxProviderError):
                provider.run(request, lambda *_: None, lambda: False)

    def test_run_rejects_unmeasured_candidate_recipe(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            provider._verify = MagicMock()
            with self.assertRaises(WanMlxProviderError):
                provider.run(SimpleNamespace(
                    recipe="DraftLandscape", prompt="a test", width=1280, height=704,
                    frames=41, fps=24, steps=20, guidance_scale=5.0, seed=7,
                    output_dir=Path(temp), source_image=None,
                ), lambda *_: None, lambda: False)

    def test_i2v_profile_passes_source_image_to_vendor(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp, "BalancedI2V", "image")
            provider._verify = MagicMock()
            provider._generate = MagicMock()
            source = Path(temp) / "source.png"
            source.write_bytes(b"fixture")
            request = SimpleNamespace(
                recipe="BalancedI2V", prompt="animate it", width=1280, height=704,
                frames=41, fps=24, steps=40, guidance_scale=5.0, seed=7,
                output_dir=Path(temp), source_image=source,
            )
            provider.run(request, lambda *_: None, lambda: False)
            self.assertIs(provider._generate.call_args.kwargs["image"], source)

    def test_text_profile_rejects_image_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            provider._verify = MagicMock()
            request = SimpleNamespace(
                recipe="Balanced", prompt="animate it", width=1280, height=704,
                frames=41, fps=24, steps=40, guidance_scale=5.0, seed=7,
                output_dir=Path(temp), source_image=Path(temp) / "source.png",
            )
            with self.assertRaisesRegex(WanMlxProviderError, "generation mode"):
                provider.run(request, lambda *_: None, lambda: False)
