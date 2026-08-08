"""Pinned, local-only FLUX.1-schnell text-to-image provider."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable

from ..model_security import ModelSecurityError, verify_tree
from ..models import REGISTRY, ModelSpec
from .base import Capability, OperationRequest, ProgressCallback, ProviderFacts


class FluxProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class FluxMeasuredProfile:
    width: int
    height: int
    steps: int
    guidance_scale: float
    max_sequence_length: int
    dtype: str
    estimated_disk_bytes: int
    peak_rss_bytes: int
    peak_mps_allocated_bytes: int = 0

    @classmethod
    def from_json(cls, path: Path) -> "FluxMeasuredProfile":
        try:
            profile = cls(**json.loads(path.read_text()))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise FluxProviderError("missing or invalid measured FLUX profile") from error
        if min(profile.width, profile.height, profile.steps, profile.max_sequence_length, profile.estimated_disk_bytes, profile.peak_rss_bytes) <= 0:
            raise FluxProviderError("measured FLUX profile contains an invalid value")
        if profile.dtype not in {"float16", "bfloat16"} or profile.guidance_scale < 0 or profile.peak_mps_allocated_bytes < 0:
            raise FluxProviderError("measured FLUX profile contains an unsupported strategy")
        return profile


class FluxSchnellProvider:
    facts = ProviderFacts(
        provider_id="flux-schnell",
        capabilities=frozenset({Capability.IMAGE_GENERATION}),
        profile="shareable",
        revision=REGISTRY["flux-schnell"].revision,
        license_name=REGISTRY["flux-schnell"].license_name,
        requires_access_confirmation=True,
    )

    def __init__(self, model_root: Path, measured_profile: Path):
        self._model_root = model_root
        self._measured_profile_path = measured_profile
        self._pipeline = None

    @property
    def spec(self) -> ModelSpec:
        return REGISTRY["flux-schnell"]

    def measured_profile(self) -> FluxMeasuredProfile:
        return FluxMeasuredProfile.from_json(self._measured_profile_path)

    def run(
        self,
        request: OperationRequest,
        progress: ProgressCallback,
        cancelled: Callable[[], bool],
    ) -> dict[str, str]:
        if request.capability != Capability.IMAGE_GENERATION:
            raise FluxProviderError("FLUX supports only image generation")
        profile = self.measured_profile()
        expected = (profile.width, profile.height, profile.steps, profile.guidance_scale)
        actual = (request.width, request.height, request.steps, request.guidance_scale)
        if actual != expected:
            raise FluxProviderError("generation settings are not in the measured FLUX profile")
        if cancelled():
            raise InterruptedError("generation cancelled before model load")

        pipeline = self._load(profile.dtype)
        import torch

        generator = torch.Generator(device="cpu").manual_seed(request.seed)

        def on_step_end(_pipe, step, _timestep, callback_kwargs):
            if cancelled():
                raise InterruptedError("generation cancelled")
            progress((step + 1) / request.steps, f"denoising step {step + 1}/{request.steps}")
            return callback_kwargs

        image = pipeline(
            prompt=request.prompt,
            width=request.width,
            height=request.height,
            guidance_scale=request.guidance_scale,
            num_inference_steps=request.steps,
            max_sequence_length=profile.max_sequence_length,
            generator=generator,
            callback_on_step_end=on_step_end,
        ).images[0]
        if cancelled():
            raise InterruptedError("generation cancelled")
        image.save(request.output_dir / "image.png", format="PNG")
        return {"media_file": "image.png", "media_type": "image/png"}

    def _load(self, dtype_name: str):
        if self._pipeline is not None:
            return self._pipeline
        manifest_path = self._model_root.parent / "flux-schnell.sha256.json"
        try:
            manifest = json.loads(manifest_path.read_text())
            if not isinstance(manifest, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in manifest.items()):
                raise ValueError("manifest is not a string map")
            verify_tree(self._model_root, self.spec, manifest)
        except (OSError, ValueError, json.JSONDecodeError, ModelSecurityError) as error:
            raise FluxProviderError("FLUX model is not a verified SynVid install") from error
        import torch
        from diffusers import FluxPipeline

        dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16}[dtype_name]
        self._pipeline = FluxPipeline.from_pretrained(
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
