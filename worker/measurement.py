"""Shared on-device measurement helpers for calibration and dev gate scripts.

Extracted from the ad hoc peak-RSS/peak-MPS-memory logic duplicated across
scripts/*_smoke_test.py so the in-app calibration path and the developer gate
scripts measure a model the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
import resource
import subprocess
import sys
import threading


def peak_rss_bytes() -> int:
    # macOS reports ru_maxrss in bytes; Linux reports KiB. SynVid's v1 target
    # is macOS, but retain a portable conversion for host-only test runs.
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def total_system_memory_bytes() -> int:
    """This Mac's total unified/physical memory, used for a pre-flight safety check."""
    output = subprocess.run(
        ["sysctl", "-n", "hw.memsize"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    return int(output)


class MpsMemoryPoller:
    """Samples torch.mps.current_allocated_memory() on a background thread.

    Some pipelines (HunyuanVideo15Pipeline as of diffusers 0.39.0) do not
    support callback_on_step_end, so peak memory cannot be sampled from an
    in-pipeline step callback the way other providers do it.
    """

    def __init__(self, interval_seconds: float = 0.5):
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_bytes = 0

    def _poll(self) -> None:
        import torch

        while not self._stop.is_set():
            if torch.backends.mps.is_available():
                self.peak_bytes = max(self.peak_bytes, torch.mps.current_allocated_memory())
            self._stop.wait(self._interval_seconds)

    def __enter__(self) -> "MpsMemoryPoller":
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc_info) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()


@dataclass(frozen=True)
class CalibrationResult:
    """The numbers a calibration run actually measures.

    Each provider's schema differs (LTX/Hunyuan carry frames+fps, Flux/Qwen
    carry max_sequence_length, only Qwen carries true_cfg_scale, and field
    names for wall time are not consistent across existing measured-profile
    schemas). Rather than force one generic dict shape, each provider's
    calibrate() merges these measured numbers into its own profile dict
    alongside its fixed CALIBRATION_RECIPES shape and a freshly computed
    estimated_disk_bytes.
    """

    peak_rss_bytes: int
    peak_mps_allocated_bytes: int
    wall_time_seconds: float
