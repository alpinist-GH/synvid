import json
import tempfile
import unittest
from pathlib import Path

from worker.providers.ltx import LtxMeasuredProfile, LtxProviderError
from worker.resources import AdmissionError, Estimate


class LtxProviderTests(unittest.TestCase):
    def test_measured_profile_is_explicit_and_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profile.json"
            path.write_text(json.dumps({
                "width": 512, "height": 512, "frames": 9, "fps": 8,
                "steps": 4, "guidance_scale": 1.0, "dtype": "float16",
                "estimated_disk_bytes": 1024, "peak_rss_bytes": 2048,
            }))
            self.assertEqual(LtxMeasuredProfile.from_json(path).dtype, "float16")
            path.write_text("{}")
            with self.assertRaises(LtxProviderError):
                LtxMeasuredProfile.from_json(path)

    def test_unmeasured_disk_estimate_cannot_admit_work(self):
        with self.assertRaises(AdmissionError):
            Estimate(1024, False).require_measured()
