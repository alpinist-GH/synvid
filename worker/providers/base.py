"""Provider contracts; orchestration never switches on a model name."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, Protocol


class Capability(StrEnum):
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"
    VIDEO_EDITING = "video_editing"
    IMAGE_EDITING = "image_editing"
    NARRATION = "narration"


ProgressCallback = Callable[[float, str], None]


class InsufficientMemoryError(RuntimeError):
    """Raised by calibrate() when this Mac does not meet a recipe's memory floor.

    Raised before the pipeline is touched, so a calibration attempt on an
    under-provisioned Mac never risks the memory-thrashing a real generation
    run could cause.
    """


@dataclass(frozen=True)
class ProviderFacts:
    provider_id: str
    capabilities: frozenset[Capability]
    profile: str
    revision: str
    license_name: str
    requires_access_confirmation: bool
    # Recipe names (e.g. "Draft"/"Balanced"/"High") this provider can
    # calibrate on-device. Empty for providers with no quality-approved
    # recipe shape (e.g. Wan, whose quality gate failed) — calibrate() is
    # only present on providers that populate this.
    calibration_recipes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class OperationRequest:
    operation_id: str
    capability: Capability
    prompt: str
    output_dir: Path
    seed: int
    width: int
    height: int
    frames: int
    fps: int
    steps: int
    guidance_scale: float
    recipe: str = "Balanced"
    source_image: Path | None = None
    source_video: Path | None = None
    source_output_id: str | None = None
    change_amount: float | None = None


class Provider(Protocol):
    facts: ProviderFacts

    def run(self, request: OperationRequest, progress: ProgressCallback, cancelled: Callable[[], bool]) -> dict[str, object]: ...

    def unload(self) -> None: ...
