"""Local-only LTX Video provider.

The pipeline is imported lazily so protocol, fake-provider, and packaged-worker
startup tests never load PyTorch or contact Hugging Face.  A model must already
exist below SynVid's owned model root and have passed checksum verification.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from typing import Callable

from ..measurement import peak_rss_bytes, total_system_memory_bytes
from ..model_security import ModelSecurityError, verify_tree
from ..models import REGISTRY, ModelSpec
from .base import Capability, InsufficientMemoryError, OperationRequest, ProgressCallback, ProviderFacts


class LtxProviderError(RuntimeError):
    pass


# Quality-approved recipe shapes (docs/measurements/stage7-story-render-compose-2026-08-09.md,
# docs/measurements/ltx-duration-ladder-gate-2026-08-10.md, and earlier LTX
# gates). Only resolution/frames/fps/steps/guidance/dtype are trusted here;
# calibrate() measures the memory/disk numbers fresh.
#
# Square duration ladder: each quality tier (steps) is offered at every frame
# count in DURATION_LADDER_FRAMES so the UI can expose a Duration control
# independent of the Draft/Balanced/High quality control. Frame counts follow
# LTX's 8k+1 latent alignment. The bare quality name ("Draft"/"Balanced"/
# "High") stays pinned to each tier's original single-duration shape so
# existing measured-profile.json entries and any external state stay valid;
# every other frame count is named "{Quality}D{frames}".
DURATION_LADDER_FRAMES: tuple[int, ...] = (9, 17, 25, 33, 41, 49, 57, 65, 73, 81, 89, 97, 105, 113, 121)

# quality -> (steps, default_frames)
_SQUARE_QUALITIES: dict[str, tuple[int, int]] = {"Draft": (4, 9), "Balanced": (8, 49), "High": (12, 9)}


def _square_duration_recipes() -> dict[str, dict[str, object]]:
    recipes: dict[str, dict[str, object]] = {}
    for quality, (steps, default_frames) in _SQUARE_QUALITIES.items():
        for frames in DURATION_LADDER_FRAMES:
            name = quality if frames == default_frames else f"{quality}D{frames}"
            recipes[name] = {
                "width": 256, "height": 256, "frames": frames, "fps": 8,
                "steps": steps, "guidance_scale": 3.0, "dtype": "float16",
            }
    return recipes


# The *Landscape/*Portrait entries remain single-duration only: shape-sanity-
# checked (32px-aligned, exact 16:9/9:16), not yet extended with a duration
# ladder like the square recipes above. They become ladder-eligible once a
# real calibrate() run on a real Mac succeeds and output has been reviewed
# across that aspect's frame counts too.
CALIBRATION_RECIPES: dict[str, dict[str, object]] = {
    **_square_duration_recipes(),
    "DraftLandscape": {"width": 512, "height": 288, "frames": 9, "fps": 8, "steps": 4, "guidance_scale": 3.0, "dtype": "float16"},
    "BalancedLandscape": {"width": 512, "height": 288, "frames": 49, "fps": 8, "steps": 8, "guidance_scale": 3.0, "dtype": "float16"},
    "HighLandscape": {"width": 512, "height": 288, "frames": 9, "fps": 8, "steps": 12, "guidance_scale": 3.0, "dtype": "float16"},
    "DraftPortrait": {"width": 288, "height": 512, "frames": 9, "fps": 8, "steps": 4, "guidance_scale": 3.0, "dtype": "float16"},
    "BalancedPortrait": {"width": 288, "height": 512, "frames": 49, "fps": 8, "steps": 8, "guidance_scale": 3.0, "dtype": "float16"},
    "HighPortrait": {"width": 288, "height": 512, "frames": 9, "fps": 8, "steps": 12, "guidance_scale": 3.0, "dtype": "float16"},
}

# Derived from this model's measured ~28-31 GiB peak RSS across recipes, plus margin.
MIN_SYSTEM_MEMORY_BYTES = 36 * 1024**3

_CALIBRATION_PROMPT = "A yellow flower gently moving in a spring breeze"


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
                if name in CALIBRATION_RECIPES and isinstance(value, dict)
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
        calibration_recipes=frozenset(CALIBRATION_RECIPES),
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
        preprocessing: dict[str, object] | None = None
        if is_edit:
            # LTXConditionPipeline expects one sequence per requested video.
            frames, preprocessing = self._preprocess_video(
                request.source_video, request.width, request.height, request.frames, request.fps,
                request.change_amount, cancelled,
            )
            arguments["video"] = [frames]
            # The condition pipeline defaults to a strength of 1.0, which
            # hard-preserves every supplied source frame.  Pair source
            # conditioning with denoising so this single UI control has the
            # promised preserve-versus-change behavior at both ends.
            arguments["strength"] = 1.0 - request.change_amount
            arguments["denoise_strength"] = request.change_amount
        result = pipeline(**arguments)
        if cancelled():
            raise InterruptedError("generation cancelled")
        video_path = request.output_dir / "video.mp4"
        export_to_video(result.frames[0], str(video_path), fps=request.fps)
        result: dict[str, object] = {"media_file": "video.mp4", "native_fps": str(request.fps)}
        if preprocessing is not None:
            result["preprocessing"] = preprocessing
        return result

    def calibration_reference(self, recipe_name: str) -> dict[str, object] | None:
        """Static shape/memory-floor facts for the UI, before any run starts."""
        shape = CALIBRATION_RECIPES.get(recipe_name)
        if shape is None:
            return None
        return {**shape, "min_system_memory_bytes": MIN_SYSTEM_MEMORY_BYTES}

    def calibrate(self, recipe_name: str, existing_profile_raw: dict | None, progress: ProgressCallback, cancelled: Callable[[], bool]) -> dict[str, object]:
        """Measure a fixed, quality-approved recipe shape on this Mac.

        Returns the complete new measured-profile.json content, merging this
        recipe into any other already-measured recipes.
        """
        shape = CALIBRATION_RECIPES.get(recipe_name)
        if shape is None:
            raise LtxProviderError(f"{recipe_name!r} has no quality-approved calibration recipe for LTX")
        available = total_system_memory_bytes()
        if available < MIN_SYSTEM_MEMORY_BYTES:
            raise InsufficientMemoryError(
                f"this Mac has {available / 1024**3:.1f} GiB of memory; the {recipe_name} "
                f"LTX recipe needs at least {MIN_SYSTEM_MEMORY_BYTES / 1024**3:.1f} GiB"
            )
        if cancelled():
            raise InterruptedError("calibration cancelled before model load")

        progress(0.05, "Loading LTX Video pipeline")
        pipeline = self._load(shape["dtype"])
        import torch
        from diffusers.utils import export_to_video

        generator = torch.Generator(device="cpu").manual_seed(42)

        def on_step_end(_pipe, step, _timestep, callback_kwargs):
            if cancelled():
                raise InterruptedError("calibration cancelled")
            progress(0.05 + 0.85 * (step + 1) / shape["steps"], f"calibration step {step + 1}/{shape['steps']}")
            return callback_kwargs

        result = pipeline(
            prompt=_CALIBRATION_PROMPT, width=shape["width"], height=shape["height"],
            num_frames=shape["frames"], frame_rate=shape["fps"],
            num_inference_steps=shape["steps"], guidance_scale=shape["guidance_scale"],
            generator=generator, callback_on_step_end=on_step_end,
        )
        if cancelled():
            raise InterruptedError("calibration cancelled")
        progress(0.95, "Encoding calibration output")
        with tempfile.TemporaryDirectory() as scratch:
            export_to_video(result.frames[0], f"{scratch}/calibration.mp4", fps=shape["fps"])

        measured = {
            "width": shape["width"], "height": shape["height"], "frames": shape["frames"],
            "fps": shape["fps"], "steps": shape["steps"], "guidance_scale": shape["guidance_scale"],
            "dtype": shape["dtype"],
            "estimated_disk_bytes": sum(path.stat().st_size for path in self._model_root.rglob("*") if path.is_file()),
            "peak_rss_bytes": peak_rss_bytes(),
        }
        recipes = dict(existing_profile_raw.get("recipes", {})) if isinstance(existing_profile_raw, dict) else {}
        recipes[recipe_name] = measured
        return {"recipes": recipes, "schema_version": 2}

    @staticmethod
    def _preprocess_video(
        source: Path, width: int, height: int, frames: int, fps: int, change_amount: float | None,
        cancelled: Callable[[], bool],
    ) -> tuple[list, dict[str, object]]:
        """Decode one exact owned sequence and record its deterministic transform."""
        import imageio.v2 as imageio
        from PIL import Image

        reader = imageio.get_reader(str(source))
        try:
            metadata = reader.get_meta_data()
            source_fps = metadata.get("fps")
            if isinstance(source_fps, bool) or not isinstance(source_fps, (int, float)) or source_fps <= 0:
                raise LtxProviderError("source video has no usable frame rate")
            normalized = []
            source_size: tuple[int, int] | None = None
            for index, frame in enumerate(reader):
                if cancelled():
                    raise InterruptedError("generation cancelled during source preprocessing")
                # V1 deliberately accepts only the exact measured sequence,
                # rather than silently discarding an unbounded source clip.
                if index >= frames:
                    raise LtxProviderError("source video exceeds the measured frame count")
                image = Image.fromarray(frame).convert("RGB")
                if source_size is None:
                    source_size = image.size
                elif image.size != source_size:
                    raise LtxProviderError("source video frames have inconsistent dimensions")
                normalized.append(LtxProvider._resize_center_crop(image, width, height))
        finally:
            reader.close()
        if len(normalized) != frames:
            raise LtxProviderError("source video does not contain the measured frame count")
        assert source_size is not None
        source_width, source_height = source_size
        policy = "identity" if source_size == (width, height) else "center_crop_then_lanczos_resize"
        return normalized, {
            "source": {
                "decoded_fps": float(source_fps),
                "decoded_duration_seconds": len(normalized) / float(source_fps),
                "decoded_width": source_width,
                "decoded_height": source_height,
                "decoded_frames": len(normalized),
            },
            "target": {
                "fps": fps,
                "duration_seconds": frames / fps,
                "width": width,
                "height": height,
                "frames": frames,
            },
            "resize_crop_policy": policy,
            "source_conditioning_strength": 1.0 - change_amount if change_amount is not None else None,
        }

    @staticmethod
    def _resize_center_crop(image, width: int, height: int):
        """Preserve aspect ratio before a deterministic high-quality resize."""
        from PIL import Image

        source_width, source_height = image.size
        source_ratio = source_width / source_height
        target_ratio = width / height
        if source_ratio > target_ratio:
            crop_width = round(source_height * target_ratio)
            left = (source_width - crop_width) // 2
            image = image.crop((left, 0, left + crop_width, source_height))
        elif source_ratio < target_ratio:
            crop_height = round(source_width / target_ratio)
            top = (source_height - crop_height) // 2
            image = image.crop((0, top, source_width, top + crop_height))
        return image.resize((width, height), Image.Resampling.LANCZOS)

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
        if video_editing and self._pipeline.scheduler.config.get("use_dynamic_shifting", False):
            # Diffusers 0.39's LTXConditionPipeline does not calculate and
            # pass the required `mu` value to its dynamic-shift scheduler (the
            # text-to-video pipeline does).  Use its pinned static shift until
            # a measured upgrade supplies the condition-pipeline equivalent.
            self._pipeline.scheduler.register_to_config(use_dynamic_shifting=False)
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
