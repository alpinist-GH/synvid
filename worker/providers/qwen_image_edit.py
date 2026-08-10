"""Pinned Qwen image-edit provider for SynVid's shareable profile."""

from __future__ import annotations

from dataclasses import dataclass
import gc
import json
from pathlib import Path
import tempfile
import time
from typing import Callable

from ..measurement import MpsMemoryPoller, peak_rss_bytes, total_system_memory_bytes
from ..model_security import ModelSecurityError, verify_tree
from ..models import REGISTRY, ModelSpec
from .base import Capability, InsufficientMemoryError, OperationRequest, ProgressCallback, ProviderFacts


class QwenImageEditError(RuntimeError):
    pass


# Quality-approved recipe shape (docs/measurements/stage6-qwen-image-edit-gate-2026-08-08.md).
CALIBRATION_RECIPES: dict[str, dict[str, object]] = {
    "Balanced": {
        "width": 512, "height": 512, "steps": 4, "guidance_scale": 1.0,
        "true_cfg_scale": 1.0, "max_sequence_length": 512, "dtype": "bfloat16",
    },
}

# Derived from the ~57.7 GiB peak MPS allocation measured for this recipe, plus margin.
MIN_SYSTEM_MEMORY_BYTES = 64 * 1024**3

_CALIBRATION_PROMPT = "Make the flower a deeper shade of orange"


@dataclass(frozen=True)
class QwenImageEditMeasuredProfile:
    width: int
    height: int
    steps: int
    guidance_scale: float
    true_cfg_scale: float
    max_sequence_length: int
    dtype: str
    estimated_disk_bytes: int
    peak_rss_bytes: int
    peak_mps_allocated_bytes: int = 0
    wall_seconds: float = 0.0

    @classmethod
    def from_json(cls, path: Path) -> "QwenImageEditMeasuredProfile":
        try:
            profile = cls(**json.loads(path.read_text()))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise QwenImageEditError("missing or invalid measured Qwen Image Edit profile") from error
        if min(profile.width, profile.height, profile.steps, profile.max_sequence_length, profile.estimated_disk_bytes, profile.peak_rss_bytes) <= 0:
            raise QwenImageEditError("measured Qwen Image Edit profile contains an invalid value")
        if profile.dtype not in {"float16", "bfloat16"} or profile.guidance_scale < 0 or profile.true_cfg_scale < 0 or profile.peak_mps_allocated_bytes < 0 or profile.wall_seconds <= 0:
            raise QwenImageEditError("measured Qwen Image Edit profile contains an unsupported strategy")
        return profile


class QwenImageEditProvider:
    facts = ProviderFacts(
        "qwen-image-edit", frozenset({Capability.IMAGE_EDITING}), "shareable",
        REGISTRY["qwen-image-edit"].revision, "Apache-2.0", True,
        calibration_recipes=frozenset(CALIBRATION_RECIPES),
    )

    def __init__(self, model_root: Path, measured_profile: Path):
        self._model_root = model_root
        self._measured_profile_path = measured_profile
        self._pipeline = None

    @property
    def spec(self) -> ModelSpec:
        return REGISTRY["qwen-image-edit"]

    def measured_profile(self) -> QwenImageEditMeasuredProfile:
        return QwenImageEditMeasuredProfile.from_json(self._measured_profile_path)

    def run(self, request: OperationRequest, progress: ProgressCallback, cancelled: Callable[[], bool]) -> dict[str, str]:
        if request.capability != Capability.IMAGE_EDITING or request.source_image is None:
            raise QwenImageEditError("Qwen Image Edit requires an owned source image")
        profile = self.measured_profile()
        if (request.width, request.height, request.steps, request.guidance_scale) != (profile.width, profile.height, profile.steps, profile.guidance_scale):
            raise QwenImageEditError("image edit settings are not in the measured Qwen profile")
        if cancelled():
            raise InterruptedError("image edit cancelled before model load")
        pipeline = self._load(profile.dtype)
        import torch
        from PIL import Image

        generator = torch.Generator(device="cpu").manual_seed(request.seed)
        source = Image.open(request.source_image).convert("RGB")

        def on_step_end(_pipe, step, _timestep, callback_kwargs):
            if cancelled():
                raise InterruptedError("image edit cancelled")
            progress((step + 1) / request.steps, f"editing step {step + 1}/{request.steps}")
            return callback_kwargs

        image = pipeline(
            image=source, prompt=request.prompt, width=request.width, height=request.height,
            num_inference_steps=request.steps, guidance_scale=request.guidance_scale,
            true_cfg_scale=profile.true_cfg_scale, max_sequence_length=profile.max_sequence_length,
            generator=generator, callback_on_step_end=on_step_end,
        ).images[0]
        if cancelled():
            raise InterruptedError("image edit cancelled")
        image.save(request.output_dir / "image.png", format="PNG")
        return {"media_file": "image.png", "media_type": "image/png"}

    def calibration_reference(self, recipe_name: str) -> dict[str, object] | None:
        """Static shape/memory-floor facts for the UI, before any run starts."""
        shape = CALIBRATION_RECIPES.get(recipe_name)
        if shape is None:
            return None
        return {**shape, "min_system_memory_bytes": MIN_SYSTEM_MEMORY_BYTES}

    def calibrate(self, recipe_name: str, existing_profile_raw: dict | None, progress: ProgressCallback, cancelled: Callable[[], bool]) -> dict[str, object]:
        """Measure a fixed, quality-approved recipe shape on this Mac.

        Edits a synthetic neutral-gray placeholder image at the recipe's
        resolution: calibration measures resource use, not output quality
        (already established by the developer's own gate), so no real
        source image is needed. Qwen Image Edit's on-disk profile is a
        single flat object (only one recipe exists), so the new measurement
        simply replaces it.
        """
        shape = CALIBRATION_RECIPES.get(recipe_name)
        if shape is None:
            raise QwenImageEditError(f"{recipe_name!r} has no quality-approved calibration recipe for Qwen Image Edit")
        available = total_system_memory_bytes()
        if available < MIN_SYSTEM_MEMORY_BYTES:
            raise InsufficientMemoryError(
                f"this Mac has {available / 1024**3:.1f} GiB of memory; the {recipe_name} "
                f"Qwen Image Edit recipe needs at least {MIN_SYSTEM_MEMORY_BYTES / 1024**3:.1f} GiB"
            )
        if cancelled():
            raise InterruptedError("calibration cancelled before model load")

        progress(0.05, "Loading Qwen Image Edit pipeline")
        pipeline = self._load(shape["dtype"])
        import torch
        from PIL import Image

        generator = torch.Generator(device="cpu").manual_seed(42)
        source = Image.new("RGB", (shape["width"], shape["height"]), color=(128, 128, 128))

        def on_step_end(_pipe, step, _timestep, callback_kwargs):
            if cancelled():
                raise InterruptedError("calibration cancelled")
            progress(0.05 + 0.85 * (step + 1) / shape["steps"], f"calibration step {step + 1}/{shape['steps']}")
            return callback_kwargs

        started = time.monotonic()
        with MpsMemoryPoller() as poller:
            image = pipeline(
                image=source, prompt=_CALIBRATION_PROMPT, width=shape["width"], height=shape["height"],
                num_inference_steps=shape["steps"], guidance_scale=shape["guidance_scale"],
                true_cfg_scale=shape["true_cfg_scale"], max_sequence_length=shape["max_sequence_length"],
                generator=generator, callback_on_step_end=on_step_end,
            ).images[0]
        wall_seconds = time.monotonic() - started
        if cancelled():
            raise InterruptedError("calibration cancelled")
        progress(0.95, "Saving calibration output")
        with tempfile.TemporaryDirectory() as scratch:
            image.save(f"{scratch}/calibration.png", format="PNG")

        return {
            "width": shape["width"], "height": shape["height"], "steps": shape["steps"],
            "guidance_scale": shape["guidance_scale"], "true_cfg_scale": shape["true_cfg_scale"],
            "max_sequence_length": shape["max_sequence_length"], "dtype": shape["dtype"],
            "estimated_disk_bytes": sum(path.stat().st_size for path in self._model_root.rglob("*") if path.is_file()),
            "peak_rss_bytes": peak_rss_bytes(),
            "peak_mps_allocated_bytes": poller.peak_bytes,
            "wall_seconds": wall_seconds,
        }

    def _load(self, dtype_name: str):
        if self._pipeline is not None:
            return self._pipeline
        try:
            manifest = json.loads((self._model_root.parent / "qwen-image-edit.sha256.json").read_text())
            if not isinstance(manifest, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in manifest.items()):
                raise ValueError("manifest is not a string map")
            verify_tree(self._model_root, self.spec, manifest)
        except (OSError, ValueError, json.JSONDecodeError, ModelSecurityError) as error:
            raise QwenImageEditError("Qwen Image Edit is not a verified SynVid install") from error
        import torch
        from diffusers import QwenImageEditPipeline

        dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16}[dtype_name]
        self._pipeline = QwenImageEditPipeline.from_pretrained(str(self._model_root), torch_dtype=dtype, local_files_only=True, trust_remote_code=False).to("mps")
        self._pipeline.set_progress_bar_config(disable=True)
        return self._pipeline

    def unload(self) -> None:
        self._pipeline = None
        gc.collect()
        try:
            import torch
            if torch.backends.mps.is_available():
                torch.mps.synchronize(); torch.mps.empty_cache()
        except (ImportError, RuntimeError):
            pass
