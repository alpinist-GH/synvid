import json
import tempfile
import unittest
from pathlib import Path

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
