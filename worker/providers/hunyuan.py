"""Local HunyuanVideo 1.5 480p providers.

The T2V and I2V checkpoints are separate reviewed Diffusers snapshots.  This
adapter deliberately remains unusable until a real device-measured profile is
written beside the verified snapshot; model presence alone never enables it.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import time
from typing import Callable

from ..measurement import MpsMemoryPoller, peak_rss_bytes, total_system_memory_bytes
from ..model_security import ModelSecurityError, verify_tree
from ..models import REGISTRY, ModelSpec
from .base import Capability, InsufficientMemoryError, OperationRequest, ProgressCallback, ProviderFacts


class HunyuanProviderError(RuntimeError):
    pass


# Quality-approved recipe shapes, established by a human-inspected MPS gate
# (docs/measurements/hunyuan15-480p-t2v-mps-gate-2026-08-09.md). I2V has
# never been gated and has no recipe here, so it stays uncalibratable.
CALIBRATION_RECIPES: dict[str, dict[str, dict[str, object]]] = {
    "hunyuan15-480p-t2v": {
        "Balanced": {
            "width": 848, "height": 480, "frames": 25, "fps": 24,
            "steps": 20, "guidance_scale": 6.0, "dtype": "bfloat16",
        },
    },
}

# Derived from the 34.3 GiB peak MPS allocation measured for the Balanced
# recipe on the gate's reference Mac, plus a safety margin. A 61-frame
# candidate at the same resolution thrashed that Mac's unified memory, so
# this floor is intentionally conservative rather than the bare minimum.
MIN_SYSTEM_MEMORY_BYTES: dict[str, int] = {
    "hunyuan15-480p-t2v": 40 * 1024**3,
}

_CALIBRATION_PROMPT = "A yellow flower gently moving in a spring breeze"


@dataclass(frozen=True)
class HunyuanMeasuredProfile:
    width: int
    height: int
    frames: int
    fps: int
    steps: int
    guidance_scale: float
    dtype: str
    estimated_disk_bytes: int
    peak_rss_bytes: int
    peak_mps_allocated_bytes: int = 0
    wall_time_seconds: float = 0.0

    @classmethod
    def from_json(cls, path: Path) -> "HunyuanMeasuredProfile":
        try:
            raw = json.loads(path.read_text())
            if "recipes" in raw:
                raw = raw["recipes"].get("Balanced")
            if not isinstance(raw, dict):
                raise ValueError("Balanced profile must be an object")
            profile = cls(**raw)
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            raise HunyuanProviderError("missing or invalid measured Hunyuan profile") from error
        required = (
            profile.width, profile.height, profile.frames, profile.fps,
            profile.steps, profile.estimated_disk_bytes, profile.peak_rss_bytes,
        )
        if min(required) <= 0:
            raise HunyuanProviderError("measured Hunyuan profile contains an invalid value")
        if profile.dtype not in {"float16", "bfloat16"} or profile.guidance_scale < 0:
            raise HunyuanProviderError("measured Hunyuan profile contains an unsupported strategy")
        return profile


@dataclass(frozen=True)
class HunyuanMeasuredRecipes:
    recipes: dict[str, HunyuanMeasuredProfile]


class HunyuanVideo15Provider:
    def __init__(self, model_root: Path, measured_profile: Path, model_id: str):
        if model_id not in {"hunyuan15-480p-t2v", "hunyuan15-480p-i2v"}:
            raise ValueError("unknown HunyuanVideo 1.5 model")
        self._model_id = model_id
        spec = REGISTRY[model_id]
        self.facts = ProviderFacts(
            provider_id=model_id,
            capabilities=frozenset({Capability.VIDEO_GENERATION}),
            profile=spec.profile,
            revision=spec.revision,
            license_name=spec.license_name,
            requires_access_confirmation=spec.requires_access_confirmation,
            calibration_recipes=frozenset(CALIBRATION_RECIPES.get(model_id, {})),
        )
        self._model_root = model_root
        self._measured_profile_path = measured_profile
        self._pipeline = None

    @property
    def spec(self) -> ModelSpec:
        return REGISTRY[self._model_id]

    def measured_recipes(self) -> HunyuanMeasuredRecipes:
        return HunyuanMeasuredRecipes({"Balanced": self.measured_profile()})

    def measured_profile(self) -> HunyuanMeasuredProfile:
        return HunyuanMeasuredProfile.from_json(self._measured_profile_path)

    def run(
        self,
        request: OperationRequest,
        progress: ProgressCallback,
        cancelled: Callable[[], bool],
    ) -> dict[str, str]:
        if request.capability != Capability.VIDEO_GENERATION:
            raise HunyuanProviderError("HunyuanVideo supports video generation only")
        is_i2v = self._model_id.endswith("-i2v")
        if is_i2v != (request.source_image is not None):
            raise HunyuanProviderError("selected Hunyuan model does not support this generation mode")
        profile = self.measured_profile()
        actual = (
            request.width, request.height, request.frames, request.fps,
            request.steps, request.guidance_scale,
        )
        expected = (
            profile.width, profile.height, profile.frames, profile.fps,
            profile.steps, profile.guidance_scale,
        )
        if actual != expected:
            raise HunyuanProviderError("generation settings are not in the measured Hunyuan profile")
        if cancelled():
            raise InterruptedError("generation cancelled before model load")

        pipeline = self._load(profile.dtype)
        import torch
        from diffusers.utils import export_to_video

        generator = torch.Generator(device="cpu").manual_seed(request.seed)
        progress(0.05, "Loading HunyuanVideo 1.5 pipeline")
        arguments = {
            "prompt": request.prompt,
            "num_frames": request.frames,
            "num_inference_steps": request.steps,
            "generator": generator,
        }
        if is_i2v:
            from PIL import Image, ImageOps

            with Image.open(request.source_image) as source:
                arguments["image"] = ImageOps.fit(
                    source.convert("RGB"),
                    (request.width, request.height),
                    method=Image.Resampling.LANCZOS,
                )
        else:
            arguments.update({"width": request.width, "height": request.height})

        # Diffusers 0.39.0 does not expose callback_on_step_end for these
        # pipelines. Cancellation is therefore checked at the safe boundaries
        # until a measured callback-compatible version is selected.
        result = pipeline(**arguments)
        if cancelled():
            raise InterruptedError("generation cancelled")
        progress(0.95, "Encoding HunyuanVideo output")
        export_to_video(result.frames[0], str(request.output_dir / "video.mp4"), fps=request.fps)
        return {"media_file": "video.mp4", "native_fps": str(request.fps)}

    def calibration_reference(self, recipe_name: str) -> dict[str, object] | None:
        """Static shape/memory-floor facts for the UI, before any run starts."""
        shape = CALIBRATION_RECIPES.get(self._model_id, {}).get(recipe_name)
        if shape is None:
            return None
        return {**shape, "min_system_memory_bytes": MIN_SYSTEM_MEMORY_BYTES[self._model_id]}

    def calibrate(
        self,
        recipe_name: str,
        existing_profile_raw: dict | None,
        progress: ProgressCallback,
        cancelled: Callable[[], bool],
    ) -> dict[str, object]:
        """Measure a fixed, quality-approved recipe shape on this Mac.

        Only the resolution/frames/steps/dtype/guidance shape is trusted from
        CALIBRATION_RECIPES (established by a human-inspected gate); every
        number in the returned profile is freshly measured on this machine.
        Returns the complete new measured-profile.json content (merging this
        recipe into any other already-measured recipes), so the caller can
        write it without knowing this provider's on-disk schema.
        """
        shape = CALIBRATION_RECIPES.get(self._model_id, {}).get(recipe_name)
        if shape is None:
            raise HunyuanProviderError(f"{recipe_name!r} has no quality-approved calibration recipe for this model")
        minimum = MIN_SYSTEM_MEMORY_BYTES[self._model_id]
        available = total_system_memory_bytes()
        if available < minimum:
            raise InsufficientMemoryError(
                f"this Mac has {available / 1024**3:.1f} GiB of memory; the {recipe_name} "
                f"recipe for {self._model_id} needs at least {minimum / 1024**3:.1f} GiB"
            )
        if cancelled():
            raise InterruptedError("calibration cancelled before model load")

        progress(0.05, "Loading HunyuanVideo 1.5 pipeline")
        pipeline = self._load(shape["dtype"])
        import torch
        from diffusers.utils import export_to_video

        generator = torch.Generator(device="cpu").manual_seed(42)
        arguments = {
            "prompt": _CALIBRATION_PROMPT,
            "width": shape["width"],
            "height": shape["height"],
            "num_frames": shape["frames"],
            "num_inference_steps": shape["steps"],
            "generator": generator,
        }
        started = time.monotonic()
        with MpsMemoryPoller() as poller:
            result = pipeline(**arguments)
            if cancelled():
                raise InterruptedError("calibration cancelled")
            wall_time_seconds = time.monotonic() - started
            progress(0.9, "Encoding calibration output")
            with tempfile.TemporaryDirectory() as scratch:
                export_to_video(result.frames[0], f"{scratch}/calibration.mp4", fps=shape["fps"])

        measured = {
            "width": shape["width"],
            "height": shape["height"],
            "frames": shape["frames"],
            "fps": shape["fps"],
            "steps": shape["steps"],
            "guidance_scale": shape["guidance_scale"],
            "dtype": shape["dtype"],
            "estimated_disk_bytes": self._tree_size(),
            "peak_rss_bytes": peak_rss_bytes(),
            "peak_mps_allocated_bytes": poller.peak_bytes,
            "wall_time_seconds": wall_time_seconds,
        }
        recipes = dict(existing_profile_raw.get("recipes", {})) if isinstance(existing_profile_raw, dict) else {}
        recipes[recipe_name] = measured
        return {"recipes": recipes, "schema_version": 2}

    def _tree_size(self) -> int:
        return sum(path.stat().st_size for path in self._model_root.rglob("*") if path.is_file())

    def _load(self, dtype_name: str):
        if self._pipeline is not None:
            return self._pipeline
        manifest_path = self._model_root.parent / f"{self._model_id}.sha256.json"
        try:
            manifest = json.loads(manifest_path.read_text())
            if not isinstance(manifest, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in manifest.items()
            ):
                raise ValueError("manifest is not a string map")
            verify_tree(self._model_root, self.spec, manifest)
        except (OSError, TypeError, ValueError, json.JSONDecodeError, ModelSecurityError) as error:
            raise HunyuanProviderError("Hunyuan model is not a verified SynVid install") from error

        import torch
        if self._model_id.endswith("-i2v"):
            from diffusers import HunyuanVideo15ImageToVideoPipeline as Pipeline
        else:
            from diffusers import HunyuanVideo15Pipeline as Pipeline
        dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16}[dtype_name]
        self._pipeline = Pipeline.from_pretrained(
            str(self._model_root),
            torch_dtype=dtype,
            local_files_only=True,
            trust_remote_code=False,
        ).to("mps")
        if hasattr(self._pipeline.vae, "enable_tiling"):
            self._pipeline.vae.enable_tiling()
        self._pipeline.set_progress_bar_config(disable=True)
        return self._pipeline

    def unload(self) -> None:
        self._pipeline = None
        import gc

        gc.collect()
        try:
            import torch

            if torch.backends.mps.is_available():
                torch.mps.synchronize()
                torch.mps.empty_cache()
        except (ImportError, RuntimeError):
            pass
