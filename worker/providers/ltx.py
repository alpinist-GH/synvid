"""Local-only LTX Video provider.

The pipeline is imported lazily so protocol, fake-provider, and packaged-worker
startup tests never load PyTorch or contact Hugging Face.  A model must already
exist below SynVid's owned model root and have passed checksum verification.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable

from ..model_security import ModelSecurityError, verify_tree
from ..models import REGISTRY, ModelSpec
from .base import Capability, OperationRequest, ProgressCallback, ProviderFacts


class LtxProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class LtxMeasuredProfile:
    """Only device-measured combinations may be submitted by the worker."""

    width: int
    height: int
    frames: int
    fps: int
    steps: int
    guidance_scale: float
    dtype: str
    estimated_disk_bytes: int
    peak_rss_bytes: int

    @classmethod
    def from_json(cls, path: Path) -> "LtxMeasuredProfile":
        try:
            raw = json.loads(path.read_text())
            return cls(**raw)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise LtxProviderError("missing or invalid measured LTX profile") from error


@dataclass(frozen=True)
class LtxMeasuredRecipes:
    """Named real-device recipes; the worker never accepts invented values."""

    recipes: dict[str, LtxMeasuredProfile]

    @classmethod
    def from_json(cls, path: Path) -> "LtxMeasuredRecipes":
        try:
            raw = json.loads(path.read_text())
            if "recipes" not in raw:
                return cls({"Balanced": LtxMeasuredProfile(**raw)})
            recipes = {
                name: LtxMeasuredProfile(**value)
                for name, value in raw["recipes"].items()
                if name in {"Draft", "Balanced", "High"} and isinstance(value, dict)
            }
            if not recipes or "Balanced" not in recipes:
                raise ValueError("measured recipes must include Balanced")
            return cls(recipes)
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            raise LtxProviderError("missing or invalid measured LTX recipes") from error


class LtxProvider:
    facts = ProviderFacts(
        provider_id="ltx-video",
        capabilities=frozenset({Capability.VIDEO_GENERATION, Capability.VIDEO_EDITING}),
        profile="shareable",
        revision=REGISTRY["ltx-video"].revision,
        license_name=REGISTRY["ltx-video"].license_name,
        requires_access_confirmation=True,
    )

    def __init__(self, model_root: Path, measured_profile: Path):
        self._model_root = model_root
        self._measured_profile_path = measured_profile
        self._pipeline = None

    @property
    def spec(self) -> ModelSpec:
        return REGISTRY["ltx-video"]

    def measured_recipes(self) -> LtxMeasuredRecipes:
        return LtxMeasuredRecipes.from_json(self._measured_profile_path)

    def run(self, request: OperationRequest, progress: ProgressCallback, cancelled: Callable[[], bool]) -> dict[str, str]:
        recipes = self.measured_recipes().recipes
        try:
            profile = recipes[request.recipe]
        except KeyError as error:
            raise LtxProviderError("generation recipe is not measured for LTX") from error
        actual = (request.width, request.height, request.frames, request.fps, request.steps, request.guidance_scale)
        expected = (profile.width, profile.height, profile.frames, profile.fps, profile.steps, profile.guidance_scale)
        if actual != expected:
            raise LtxProviderError("generation settings are not in the measured LTX profile")
        if cancelled():
            raise InterruptedError("generation cancelled before model load")
        is_edit = request.capability == Capability.VIDEO_EDITING
        if is_edit and request.source_video is None:
            raise LtxProviderError("video editing requires an owned source video")
        pipeline = self._load(profile.dtype, image_to_video=request.source_image is not None, video_editing=is_edit)
        import torch
        from diffusers.utils import export_to_video

        generator = torch.Generator(device="cpu").manual_seed(request.seed)
        def on_step_end(_pipe, _step, timestep, callback_kwargs):
            if cancelled():
                raise InterruptedError("generation cancelled")
            progress((_step + 1) / request.steps, f"denoising step {_step + 1}/{request.steps}")
            return callback_kwargs

        arguments = {
            "prompt": request.prompt, "width": request.width, "height": request.height,
            "num_frames": request.frames, "frame_rate": request.fps,
            "num_inference_steps": request.steps, "guidance_scale": request.guidance_scale,
            "generator": generator, "callback_on_step_end": on_step_end,
        }
        if request.source_image is not None:
            from PIL import Image
            with Image.open(request.source_image) as source:
                arguments["image"] = source.convert("RGB").copy()
        if is_edit:
            # LTXConditionPipeline expects one sequence per requested video.
            arguments["video"] = [self._preprocess_video(request.source_video, request.width, request.height, request.frames)]
            arguments["denoise_strength"] = request.change_amount
        result = pipeline(**arguments)
        if cancelled():
            raise InterruptedError("generation cancelled")
        video_path = request.output_dir / "video.mp4"
        export_to_video(result.frames[0], str(video_path), fps=request.fps)
        return {"media_file": "video.mp4", "native_fps": str(request.fps)}

    @staticmethod
    def _preprocess_video(source: Path, width: int, height: int, frames: int):
        """Decode a bounded, normalized frame sequence without exposing paths."""
        import imageio.v2 as imageio
        from PIL import Image

        reader = imageio.get_reader(str(source))
        try:
            normalized = []
            for index, frame in enumerate(reader):
                if index >= frames:
                    break
                normalized.append(Image.fromarray(frame).convert("RGB").resize((width, height), Image.Resampling.LANCZOS))
        finally:
            reader.close()
        if len(normalized) != frames:
            raise LtxProviderError("source video does not contain the measured frame count")
        return normalized

    def _load(self, dtype_name: str, image_to_video: bool = False, video_editing: bool = False):
        if self._pipeline is not None and getattr(self._pipeline, "_synvid_i2v", False) == image_to_video and getattr(self._pipeline, "_synvid_v2v", False) == video_editing:
            return self._pipeline
        if self._pipeline is not None:
            self.unload()
        manifest_path = self._model_root.parent / "ltx-video.sha256.json"
        try:
            manifest = json.loads(manifest_path.read_text())
            if not isinstance(manifest, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in manifest.items()):
                raise ValueError("manifest is not a string map")
            verify_tree(self._model_root, self.spec, manifest)
        except (OSError, ValueError, json.JSONDecodeError, ModelSecurityError) as error:
            raise LtxProviderError("LTX model is not a verified SynVid install") from error
        import torch
        from diffusers import LTXConditionPipeline, LTXImageToVideoPipeline, LTXPipeline

        dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16}.get(dtype_name)
        if dtype is None:
            raise LtxProviderError("measured LTX dtype is unsupported")
        # MPS CPU offload is deliberately not enabled: it is a CUDA-oriented
        # optimization and must not be assumed safe on Apple Silicon.
        pipeline_type = LTXConditionPipeline if video_editing else LTXImageToVideoPipeline if image_to_video else LTXPipeline
        self._pipeline = pipeline_type.from_pretrained(
            str(self._model_root), torch_dtype=dtype, local_files_only=True, trust_remote_code=False
        ).to("mps")
        self._pipeline._synvid_i2v = image_to_video
        self._pipeline._synvid_v2v = video_editing
        # The sidecar reserves stdout for JSON-lines. Diffusers' progress bar
        # would otherwise corrupt the protocol during a real frozen-worker job.
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
