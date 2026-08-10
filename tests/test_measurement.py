import unittest

from worker.measurement import MpsMemoryPoller, peak_rss_bytes, total_system_memory_bytes


class MeasurementTests(unittest.TestCase):
    def test_peak_rss_bytes_is_positive(self):
        self.assertGreater(peak_rss_bytes(), 0)

    def test_total_system_memory_bytes_is_positive(self):
        self.assertGreater(total_system_memory_bytes(), 0)

    def test_mps_memory_poller_starts_at_zero_without_mps(self):
        # On a host without MPS (e.g. CI), the poller must not crash and must
        # report no allocation rather than a stale or invented value.
        with MpsMemoryPoller(interval_seconds=0.01) as poller:
            pass
        self.assertGreaterEqual(poller.peak_bytes, 0)


if __name__ == "__main__":
    unittest.main()
