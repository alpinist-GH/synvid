"""Deterministic fake provider used by lifecycle and protocol tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .base import Capability, OperationRequest, ProgressCallback, ProviderFacts


@dataclass
class FakeProvider:
    mode: str = "success"
    facts: ProviderFacts = ProviderFacts(
        provider_id="fake-video",
        capabilities=frozenset({Capability.VIDEO_GENERATION}),
        profile="shareable",
        revision="fixture-v1",
        license_name="test-only",
        requires_access_confirmation=False,
    )
    unloaded: bool = False

    def run(self, request: OperationRequest, progress: ProgressCallback, cancelled: Callable[[], bool]) -> dict[str, str]:
        progress(0.0, "starting")
        if self.mode == "failure":
            raise RuntimeError("fixture provider failed")
        for step in range(1, 4):
            if cancelled():
                raise InterruptedError("fixture provider cancelled")
            progress(step / 3, f"step {step}")
        if self.mode == "death":
            raise SystemExit("fixture worker death")
        (request.output_dir / "tiny.mp4").write_bytes(b"synvid fixture media")
        return {"media_file": "tiny.mp4", "operation_id": request.operation_id}

    def unload(self) -> None:
        self.unloaded = True
