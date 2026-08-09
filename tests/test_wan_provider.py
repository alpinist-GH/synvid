import json
import tempfile
import unittest
from pathlib import Path

from worker.providers.wan import WanProviderError, WanT2VProvider


class WanProviderTests(unittest.TestCase):
    def test_wan22_provider_uses_its_registry_identity_and_balanced_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "measured-profile.json"
            path.write_text(json.dumps({
                "width": 256, "height": 256, "frames": 9, "fps": 8,
                "steps": 20, "guidance_scale": 5.0, "dtype": "bfloat16",
                "estimated_disk_bytes": 34_000_000_000, "peak_rss_bytes": 1,
                "peak_mps_allocated_bytes": 1,
            }))
            provider = WanT2VProvider(root / "snapshot", path, model_id="wan2.2-ti2v-5b")
            self.assertEqual(provider.facts.provider_id, "wan2.2-ti2v-5b")
            self.assertEqual(provider.measured_recipes().recipes["Balanced"].frames, 9)

    def test_rejects_missing_or_invalid_measured_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = WanT2VProvider(root / "snapshot", root / "measured-profile.json")
            with self.assertRaisesRegex(WanProviderError, "missing or invalid"):
                provider.measured_profile()

            (root / "measured-profile.json").write_text(json.dumps({
                "width": 480, "height": 480, "frames": 9, "fps": 8,
                "steps": 4, "guidance_scale": 0.0, "dtype": "float32",
                "estimated_disk_bytes": 1, "peak_rss_bytes": 1,
            }))
            with self.assertRaisesRegex(WanProviderError, "unsupported strategy"):
                provider.measured_profile()

    def test_accepts_complete_measured_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = {
                "width": 480, "height": 480, "frames": 9, "fps": 8,
                "steps": 4, "guidance_scale": 5.0, "dtype": "bfloat16",
                "estimated_disk_bytes": 1, "peak_rss_bytes": 1,
            }
            path = root / "measured-profile.json"
            path.write_text(json.dumps(profile))
            measured = WanT2VProvider(root / "snapshot", path).measured_profile()
            self.assertEqual(measured.frames, 9)
            self.assertEqual(measured.dtype, "bfloat16")
