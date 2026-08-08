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


@dataclass(frozen=True)
class ProviderFacts:
    provider_id: str
    capabilities: frozenset[Capability]
    profile: str
    revision: str
    license_name: str
    requires_access_confirmation: bool


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


class Provider(Protocol):
    facts: ProviderFacts

    def run(self, request: OperationRequest, progress: ProgressCallback, cancelled: Callable[[], bool]) -> dict[str, str]: ...

    def unload(self) -> None: ...
