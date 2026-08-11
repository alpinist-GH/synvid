"""Local-only Wan2.2 TI2V-5B provider, via a vendored, patched MLX port.

The pipeline is imported lazily so protocol, fake-provider, and packaged-worker
startup tests never load MLX. A model must already exist below SynVid's owned
model root and have passed checksum verification.

Distinct from the retired "wan2.2-ti2v-5b" Diffusers/MPS attempt
(worker/models.py RETIRED_MODELS): this runs the same upstream weights
through an Apple-native MLX port (worker/vendor/mlx_video_wan2) instead of
Diffusers' WanPipeline. See docs/measurements/wan2.2-ti2v-5b-mlx-gate-2026-08-10.md.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import time
import traceback
from typing import Callable

from ..measurement import peak_rss_bytes, total_system_memory_bytes
from ..model_security import ModelSecurityError, verify_tree
from ..models import REGISTRY, ModelSpec
from .base import Capability, InsufficientMemoryError, OperationRequest, ProgressCallback, ProviderFacts


class WanMlxProviderError(RuntimeError):
    pass


# Balanced text-to-video is the only recipe measured so far. The additional
# entries below are explicit calibration candidates: they are visible in
# Preparation, but the UI and worker refuse generation until each one has a
# real measured-profile entry. This keeps adding an option separate from
# claiming that the option is already quality-approved.
CALIBRATION_RECIPES: dict[str, dict[str, object]] = {
    "Balanced": {
        "width": 1280, "height": 704, "frames": 41, "fps": 24,
        "steps": 40, "guidance_scale": 5.0, "dtype": "bfloat16", "mode": "text",
    },
    "DraftLandscape": {
        "width": 1280, "height": 704, "frames": 41, "fps": 24,
        "steps": 20, "guidance_scale": 5.0, "dtype": "bfloat16", "mode": "text",
    },
    "HighLandscape": {
        "width": 1280, "height": 704, "frames": 41, "fps": 24,
        "steps": 60, "guidance_scale": 5.0, "dtype": "bfloat16", "mode": "text",
    },
    "BalancedLandscapeD25": {
        "width": 1280, "height": 704, "frames": 25, "fps": 24,
        "steps": 40, "guidance_scale": 5.0, "dtype": "bfloat16", "mode": "text",
    },
    "BalancedLandscapeD49": {
        "width": 1280, "height": 704, "frames": 49, "fps": 24,
        "steps": 40, "guidance_scale": 5.0, "dtype": "bfloat16", "mode": "text",
    },
    "BalancedSquare": {
        "width": 704, "height": 704, "frames": 41, "fps": 24,
        "steps": 40, "guidance_scale": 5.0, "dtype": "bfloat16", "mode": "text",
    },
    "BalancedPortrait": {
        "width": 704, "height": 1280, "frames": 41, "fps": 24,
        "steps": 40, "guidance_scale": 5.0, "dtype": "bfloat16", "mode": "text",
    },
    "BalancedI2V": {
        "width": 1280, "height": 704, "frames": 41, "fps": 24,
        "steps": 40, "guidance_scale": 5.0, "dtype": "bfloat16", "mode": "image",
    },
}

# Real measured mx.get_peak_memory() during a production calibrate() run on
# this 48 GiB Mac was ~40.16 GiB (docs/measurements/wan2.2-ti2v-5b-mlx-gate-2026-08-10.md) —
# notably higher than resource.getrusage().ru_maxrss for the same run
# (~12 GiB): MLX's unified-memory buffers are not fully reflected in classic
# process RSS the way this Mac's other, PyTorch/MPS-backed providers'
# allocations are, so calibrate() records mx.get_peak_memory() as the
# meaningful number, not peak_rss_bytes(). Floor set to leave real margin
# above that peak while still admitting this Mac (48 GiB), the only one this
# has been measured on.
MIN_SYSTEM_MEMORY_BYTES = 46 * 1024**3

_CALIBRATION_PROMPT = "A lighthouse on a rocky coastline at sunset, waves crashing, cinematic lighting"


@dataclass(frozen=True)
class WanMlxMeasuredProfile:
    width: int
    height: int
    frames: int
    fps: int
    steps: int
    guidance_scale: float
    dtype: str
    estimated_disk_bytes: int
    peak_rss_bytes: int
    peak_mlx_bytes: int
    wall_time_seconds: float
    mode: str = "text"

    @classmethod
    def from_json(cls, path: Path) -> "WanMlxMeasuredProfile":
        try:
            raw = json.loads(path.read_text())
            return cls(**raw)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise WanMlxProviderError("missing or invalid measured Wan MLX profile") from error


@dataclass(frozen=True)
class WanMlxMeasuredRecipes:
    recipes: dict[str, WanMlxMeasuredProfile]

    @classmethod
    def from_json(cls, path: Path) -> "WanMlxMeasuredRecipes":
        try:
            raw = json.loads(path.read_text())
            recipes = {
                name: WanMlxMeasuredProfile(**value)
                for name, value in raw.get("recipes", {}).items()
                if name in CALIBRATION_RECIPES and isinstance(value, dict)
            }
            if not recipes:
                raise ValueError("measured recipes must include at least one recipe")
            return cls(recipes)
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            raise WanMlxProviderError("missing or invalid measured Wan MLX recipes") from error


class WanMlxProvider:
    facts = ProviderFacts(
        provider_id="wan2.2-ti2v-5b-mlx",
        capabilities=frozenset({Capability.VIDEO_GENERATION}),
        profile="personal-research",
        revision=REGISTRY["wan2.2-ti2v-5b-mlx"].revision,
        license_name=REGISTRY["wan2.2-ti2v-5b-mlx"].license_name,
        requires_access_confirmation=True,
        calibration_recipes=frozenset(CALIBRATION_RECIPES),
    )

    def __init__(self, model_root: Path, measured_profile: Path):
        self._model_root = model_root
        self._measured_profile_path = measured_profile

    @property
    def spec(self) -> ModelSpec:
        return REGISTRY["wan2.2-ti2v-5b-mlx"]

    def measured_recipes(self) -> WanMlxMeasuredRecipes:
        return WanMlxMeasuredRecipes.from_json(self._measured_profile_path)

    def run(self, request: OperationRequest, progress: ProgressCallback, cancelled: Callable[[], bool]) -> dict[str, str]:
        recipes = self.measured_recipes().recipes
        try:
            profile = recipes[request.recipe]
        except KeyError as error:
            raise WanMlxProviderError("generation recipe is not measured for Wan MLX") from error
        actual = (request.width, request.height, request.frames, request.fps, request.steps, request.guidance_scale)
        expected = (profile.width, profile.height, profile.frames, profile.fps, profile.steps, profile.guidance_scale)
        if actual != expected:
            raise WanMlxProviderError("generation settings are not in the measured Wan MLX profile")
        requested_mode = "image" if request.source_image is not None else "text"
        if profile.mode != requested_mode:
            raise WanMlxProviderError("generation mode is not in the measured Wan MLX profile")
        if cancelled():
            raise InterruptedError("generation cancelled before model load")
        self._verify()
        video_path = request.output_dir / "video.mp4"
        self._generate(
            prompt=request.prompt, width=request.width, height=request.height,
            num_frames=request.frames, steps=request.steps, guide_scale=request.guidance_scale,
            seed=request.seed, output_path=video_path, image=request.source_image,
            progress=progress, cancelled=cancelled,
        )
        if cancelled():
            raise InterruptedError("generation cancelled")
        return {"media_file": "video.mp4", "native_fps": str(request.fps)}

    def calibration_reference(self, recipe_name: str) -> dict[str, object] | None:
        shape = CALIBRATION_RECIPES.get(recipe_name)
        if shape is None:
            return None
        return {**shape, "min_system_memory_bytes": MIN_SYSTEM_MEMORY_BYTES}

    def calibrate(self, recipe_name: str, existing_profile_raw: dict | None, progress: ProgressCallback, cancelled: Callable[[], bool]) -> dict[str, object]:
        shape = CALIBRATION_RECIPES.get(recipe_name)
        if shape is None:
            raise WanMlxProviderError(f"{recipe_name!r} has no quality-approved calibration recipe for Wan MLX")
        available = total_system_memory_bytes()
        if available < MIN_SYSTEM_MEMORY_BYTES:
            raise InsufficientMemoryError(
                f"this Mac has {available / 1024**3:.1f} GiB of memory; the {recipe_name} "
                f"Wan MLX recipe needs at least {MIN_SYSTEM_MEMORY_BYTES / 1024**3:.1f} GiB"
            )
        if cancelled():
            raise InterruptedError("calibration cancelled before model load")
        self._verify()

        import mlx.core as mx

        mx.reset_peak_memory()
        start = time.monotonic()
        with tempfile.TemporaryDirectory() as scratch:
            image = None
            if shape["mode"] == "image":
                from PIL import Image, ImageDraw

                image = Path(scratch) / "calibration-source.png"
                canvas = Image.new("RGB", (shape["width"], shape["height"]), (24, 52, 82))
                draw = ImageDraw.Draw(canvas)
                draw.ellipse((shape["width"] // 3, shape["height"] // 4, shape["width"] * 2 // 3, shape["height"] * 3 // 4), fill=(230, 174, 62))
                canvas.save(image)
            self._generate(
                prompt=_CALIBRATION_PROMPT, width=shape["width"], height=shape["height"],
                num_frames=shape["frames"], steps=shape["steps"], guide_scale=shape["guidance_scale"],
                seed=42, output_path=Path(scratch) / "calibration.mp4", image=image,
                progress=progress, cancelled=cancelled,
            )
        wall_time = time.monotonic() - start
        if cancelled():
            raise InterruptedError("calibration cancelled")

        measured = {
            "width": shape["width"], "height": shape["height"], "frames": shape["frames"],
            "fps": shape["fps"], "steps": shape["steps"], "guidance_scale": shape["guidance_scale"],
            "dtype": shape["dtype"],
            "estimated_disk_bytes": sum(path.stat().st_size for path in self._model_root.rglob("*") if path.is_file()),
            "peak_rss_bytes": peak_rss_bytes(),
            "peak_mlx_bytes": mx.get_peak_memory(),
            "wall_time_seconds": wall_time,
            "mode": shape["mode"],
        }
        recipes = dict(existing_profile_raw.get("recipes", {})) if isinstance(existing_profile_raw, dict) else {}
        recipes[recipe_name] = measured
        return {"recipes": recipes, "schema_version": 1}

    def _verify(self) -> None:
        manifest_path = self._model_root.parent / "wan2.2-ti2v-5b-mlx.sha256.json"
        try:
            manifest = json.loads(manifest_path.read_text())
            if not isinstance(manifest, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in manifest.items()):
                raise ValueError("manifest is not a string map")
            verify_tree(self._model_root, self.spec, manifest)
        except (OSError, ValueError, json.JSONDecodeError, ModelSecurityError) as error:
            raise WanMlxProviderError("Wan MLX model is not a verified SynVid install") from error

    def _generate(
        self, *, prompt: str, width: int, height: int, num_frames: int, steps: int,
        guide_scale: float, seed: int, output_path: Path, image: Path | None,
        progress: ProgressCallback,
        cancelled: Callable[[], bool],
    ) -> None:
        try:
            from ..vendor.mlx_video_wan2.wan_2 import generate as generate_module
        except Exception as error:
            # Keep the protocol error concise, but print the chained native
            # import failure to the bounded worker diagnostics. PyInstaller
            # and MLX otherwise reduce this to the opaque extension message.
            traceback.print_exc()
            raise WanMlxProviderError(
                f"Wan MLX runtime initialization failed: {type(error).__name__}: {error}"
            ) from error

        original_print = generate_module.__dict__.get("print")
        # This worker's stdout is the JSON-lines protocol Rust parses; the
        # vendored module's informational prints must never reach it (see
        # worker/vendor/mlx_video_wan2/NOTICE.md). Rebinding `print` only
        # inside this module's own namespace leaves builtins.print and
        # sys.stdout untouched, so concurrent protocol replies from other
        # threads (e.g. a status poll during this ~10-minute call) are safe.
        generate_module.print = lambda *args, **kwargs: None
        try:
            generate_module.generate_video(
                model_dir=str(self._model_root),
                prompt=prompt,
                image=str(image) if image is not None else None,
                width=width, height=height, num_frames=num_frames,
                steps=steps, guide_scale=guide_scale, seed=seed,
                output_path=str(output_path),
                scheduler="unipc", tiling="auto",
                tokenizer_path=str(self._model_root / "tokenizer"),
                progress=progress, cancelled=cancelled,
            )
        except InterruptedError:
            raise
        except Exception as error:
            traceback.print_exc()
            raise WanMlxProviderError(
                f"Wan MLX generation failed: {type(error).__name__}: {error}"
            ) from error
        finally:
            if original_print is None:
                del generate_module.print
            else:
                generate_module.print = original_print

    def unload(self) -> None:
        # generate_video() loads and releases every model on each call; there
        # is no persistent pipeline object to drop, unlike the PyTorch/MPS
        # providers. Still release MLX's own cached buffers explicitly.
        import gc

        gc.collect()
        try:
            import mlx.core as mx
            mx.clear_cache()
        except ImportError:
            pass
