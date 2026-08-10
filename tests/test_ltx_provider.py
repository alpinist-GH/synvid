import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from worker.providers.base import InsufficientMemoryError
from worker.providers.ltx import LtxMeasuredProfile, LtxProvider, LtxProviderError
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

    def test_video_preprocessing_records_source_and_target_facts(self):
        import numpy

        class Reader:
            def get_meta_data(self):
                return {"fps": 8}

            def __iter__(self):
                return iter([numpy.zeros((40, 80, 3), dtype=numpy.uint8) for _ in range(2)])

            def close(self):
                pass

        with patch("imageio.v2.get_reader", return_value=Reader()):
            frames, facts = LtxProvider._preprocess_video(Path("owned.mp4"), 64, 64, 2, 8, 0.35, lambda: False)

        self.assertEqual([frame.size for frame in frames], [(64, 64), (64, 64)])
        self.assertEqual(facts["source"], {
            "decoded_fps": 8.0,
            "decoded_duration_seconds": 0.25,
            "decoded_width": 80,
            "decoded_height": 40,
            "decoded_frames": 2,
        })
        self.assertEqual(facts["target"], {
            "fps": 8,
            "duration_seconds": 0.25,
            "width": 64,
            "height": 64,
            "frames": 2,
        })
        self.assertEqual(facts["resize_crop_policy"], "center_crop_then_lanczos_resize")
        self.assertEqual(facts["source_conditioning_strength"], 0.65)

    def test_video_preprocessing_rejects_excess_frames(self):
        import numpy

        class Reader:
            def get_meta_data(self):
                return {"fps": 8}

            def __iter__(self):
                return iter([numpy.zeros((64, 64, 3), dtype=numpy.uint8) for _ in range(3)])

            def close(self):
                pass

        with patch("imageio.v2.get_reader", return_value=Reader()):
            with self.assertRaisesRegex(LtxProviderError, "exceeds"):
                LtxProvider._preprocess_video(Path("owned.mp4"), 64, 64, 2, 8, 0.35, lambda: False)

    def test_video_preprocessing_honors_cancellation(self):
        import numpy

        class Reader:
            def get_meta_data(self):
                return {"fps": 8}

            def __iter__(self):
                return iter([numpy.zeros((64, 64, 3), dtype=numpy.uint8)])

            def close(self):
                pass

        with patch("imageio.v2.get_reader", return_value=Reader()):
            with self.assertRaisesRegex(InterruptedError, "cancelled"):
                LtxProvider._preprocess_video(Path("owned.mp4"), 64, 64, 1, 8, 0.35, lambda: True)


class LtxCalibrationTests(unittest.TestCase):
    def _provider(self, temp: str) -> LtxProvider:
        root = Path(temp)
        return LtxProvider(root / "snapshot", root / "measured-profile.json")

    def test_refuses_below_memory_floor_without_touching_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            provider._load = MagicMock()
            with patch("worker.providers.ltx.total_system_memory_bytes", return_value=1024):
                with self.assertRaises(InsufficientMemoryError):
                    provider.calibrate("Balanced", None, lambda *_: None, lambda: False)
            provider._load.assert_not_called()

    def test_calibrate_measures_and_merges_into_existing_recipes(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = self._provider(temp)
            fake_pipeline = MagicMock(return_value=SimpleNamespace(frames=[None]))
            provider._load = MagicMock(return_value=fake_pipeline)
            existing = {"recipes": {"High": {"width": 1}}}
            with patch("worker.providers.ltx.total_system_memory_bytes", return_value=999 * 1024**3), \
                 patch("diffusers.utils.export_to_video"):
                result = provider.calibrate("Balanced", existing, lambda *_: None, lambda: False)
        self.assertEqual(result["recipes"]["High"], {"width": 1})
        balanced = result["recipes"]["Balanced"]
        self.assertEqual((balanced["width"], balanced["height"], balanced["frames"], balanced["steps"]), (256, 256, 49, 8))
        self.assertGreaterEqual(balanced["peak_rss_bytes"], 0)
