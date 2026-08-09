"""Pinned, local-only Wan2.1 text-to-video provider."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable

from ..model_security import ModelSecurityError, verify_tree
from ..models import REGISTRY, ModelSpec
from .base import Capability, OperationRequest, ProgressCallback, ProviderFacts


class WanProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class WanMeasuredProfile:
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
    def from_json(cls, path: Path) -> "WanMeasuredProfile":
        try:
            raw = json.loads(path.read_text())
            if not isinstance(raw, dict):
                raise ValueError("profile must be an object")
            fields = {name: raw[name] for name in cls.__dataclass_fields__ if name in raw}
            profile = cls(**fields)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise WanProviderError("missing or invalid measured Wan profile") from error
        if min(profile.width, profile.height, profile.frames, profile.fps, profile.steps, profile.estimated_disk_bytes, profile.peak_rss_bytes) <= 0:
            raise WanProviderError("measured Wan profile contains an invalid value")
        if profile.dtype not in {"float16", "bfloat16"} or profile.guidance_scale < 0:
            raise WanProviderError("measured Wan profile contains an unsupported strategy")
        return profile


@dataclass(frozen=True)
class WanMeasuredRecipes:
    recipes: dict[str, WanMeasuredProfile]


class WanT2VProvider:
    def __init__(self, model_root: Path, measured_profile: Path, model_id: str = "wan2.1-1.3b"):
        if model_id not in REGISTRY:
            raise ValueError("unknown Wan model")
        self._model_id = model_id
        spec = REGISTRY[model_id]
        self.facts = ProviderFacts(
            provider_id=model_id,
            capabilities=frozenset({Capability.VIDEO_GENERATION}),
            profile=spec.profile,
            revision=spec.revision,
            license_name=spec.license_name,
            requires_access_confirmation=spec.requires_access_confirmation,
        )
        self._model_root = model_root
        self._measured_profile_path = measured_profile
        self._pipeline = None

    @property
    def spec(self) -> ModelSpec:
        return REGISTRY[self._model_id]

    def measured_recipes(self):
        """Expose the single measured test profile as the Balanced recipe."""
        return WanMeasuredRecipes({"Balanced": self.measured_profile()})

    def measured_profile(self) -> WanMeasuredProfile:
        return WanMeasuredProfile.from_json(self._measured_profile_path)

    def run(
        self,
        request: OperationRequest,
        progress: ProgressCallback,
        cancelled: Callable[[], bool],
    ) -> dict[str, str]:
        if request.capability != Capability.VIDEO_GENERATION or request.source_image is not None:
            raise WanProviderError("this Wan provider supports text-to-video only")
        profile = self.measured_profile()
        expected = (profile.width, profile.height, profile.frames, profile.fps, profile.steps, profile.guidance_scale)
        actual = (request.width, request.height, request.frames, request.fps, request.steps, request.guidance_scale)
        if actual != expected:
            raise WanProviderError("generation settings are not in the measured Wan profile")
        if cancelled():
            raise InterruptedError("generation cancelled before model load")

        pipeline = self._load(profile.dtype)
        import torch
        from diffusers.utils import export_to_video

        generator = torch.Generator(device="cpu").manual_seed(request.seed)

        def on_step_end(_pipe, step, _timestep, callback_kwargs):
            if cancelled():
                raise InterruptedError("generation cancelled")
            progress((step + 1) / request.steps, f"denoising step {step + 1}/{request.steps}")
            return callback_kwargs

        result = pipeline(
            prompt=request.prompt,
            width=request.width,
            height=request.height,
            num_frames=request.frames,
            num_inference_steps=request.steps,
            guidance_scale=request.guidance_scale,
            generator=generator,
            callback_on_step_end=on_step_end,
        )
        if cancelled():
            raise InterruptedError("generation cancelled")
        export_to_video(result.frames[0], str(request.output_dir / "video.mp4"), fps=request.fps)
        return {"media_file": "video.mp4", "native_fps": str(request.fps)}

    def _load(self, dtype_name: str):
        if self._pipeline is not None:
            return self._pipeline
        manifest_path = self._model_root.parent / f"{self._model_id}.sha256.json"
        try:
            manifest = json.loads(manifest_path.read_text())
            if not isinstance(manifest, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in manifest.items()):
                raise ValueError("manifest is not a string map")
            verify_tree(self._model_root, self.spec, manifest)
        except (OSError, ValueError, json.JSONDecodeError, ModelSecurityError) as error:
            raise WanProviderError("Wan model is not a verified SynVid install") from error
        import torch
        from diffusers import WanPipeline

        dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16}[dtype_name]
        self._pipeline = WanPipeline.from_pretrained(
            str(self._model_root),
            torch_dtype=dtype,
            local_files_only=True,
            trust_remote_code=False,
        ).to("mps")
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
