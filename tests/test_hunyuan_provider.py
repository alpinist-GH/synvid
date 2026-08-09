import json
import tempfile
import unittest
from pathlib import Path

from worker.providers.hunyuan import HunyuanMeasuredProfile, HunyuanProviderError, HunyuanVideo15Provider


class HunyuanProviderTests(unittest.TestCase):
    def test_missing_profile_uses_explicit_unmeasured_test_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = HunyuanVideo15Provider(
                root / "snapshot",
                root / "measured-profile.json",
                "hunyuan15-480p-t2v",
            )
            profile = provider.measured_profile()
            self.assertTrue(profile.test_only)
            self.assertEqual((profile.width, profile.height, profile.frames, profile.fps), (848, 480, 121, 24))

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
