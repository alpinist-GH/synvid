import json
import tempfile
import unittest
from pathlib import Path

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
