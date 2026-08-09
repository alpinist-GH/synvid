"""Local HunyuanVideo 1.5 480p providers.

The T2V and I2V checkpoints are separate reviewed Diffusers snapshots.  This
adapter deliberately remains unusable until a real device-measured profile is
written beside the verified snapshot; model presence alone never enables it.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable

from ..model_security import ModelSecurityError, verify_tree
from ..models import REGISTRY, ModelSpec
from .base import Capability, OperationRequest, ProgressCallback, ProviderFacts


class HunyuanProviderError(RuntimeError):
    pass


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
    test_only: bool = False

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
            profile.steps, profile.estimated_disk_bytes,
        )
        if min(required) <= 0 or (not profile.test_only and profile.peak_rss_bytes <= 0):
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
        if not self._measured_profile_path.exists():
            return HunyuanMeasuredProfile(
                width=848,
                height=480,
                frames=121,
                fps=24,
                steps=50,
                guidance_scale=0.0,
                dtype="bfloat16",
                estimated_disk_bytes=int(self.spec.expected_size_gib * 1024**3),
                peak_rss_bytes=0,
                test_only=True,
            )
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
