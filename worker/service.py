"""Stage 1 generation orchestration, independent of a concrete provider."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import re
from typing import Callable

from .jobs import BusyError, Job, JobController, JobState, TERMINAL_STATES
from .model_download import download_model
from .models import REGISTRY, RETIRED_MODEL_IDS, RETIRED_MODELS
from .narration import NarrationError, Narrator, pad_or_reject_wav, replace_audio, synthesize_segmented, write_srt
from .outputs import OutputPaths, allocate, promote, resolve_owned_file
from .paths import AppPaths
from .providers.base import Capability, OperationRequest, Provider
from .resources import Estimate, ReservationBook
from .store import Store
from .stories import StoryError, StoryStore
from .story_planner import QwenStoryPlanner
from .story_render import StoryRenderer
from .story_compose import StoryComposeError, compose_hard_cuts


class GenerationError(ValueError):
    pass


_SOURCE_IMAGE_ID = re.compile(r"^[a-z0-9-]{16,128}$")
_OUTPUT_ID = re.compile(r"^[0-9a-f-]{36}$")

# Fallback recipe-name vocabulary for providers that declare no calibration
# catalog of their own (e.g. test fixtures with no real measured registry).
# Providers with a real registry (e.g. LtxProvider) are validated against
# their own provider.facts.calibration_recipes instead.
_LEGACY_RECIPE_NAMES = frozenset({"Draft", "Balanced", "High"})


def _required(payload: dict, name: str, typ: type):
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, typ):
        raise GenerationError(f"{name} is required")
    return value


class GenerationService:
    """Owns all app roots; requests can never choose arbitrary paths."""

    def __init__(
        self,
        paths: AppPaths,
        provider: Provider,
        estimate: Estimate,
        *,
        additional_providers: tuple[Provider, ...] = (),
        estimates: dict[str, Estimate] | None = None,
        narrator: Narrator | None = None,
    ):
        self.paths = paths
        self.paths.create()
        self.provider = provider
        self.estimate = estimate
        self._providers = {provider.facts.provider_id: provider}
        self._providers.update({item.facts.provider_id: item for item in additional_providers})
        self._estimates = {provider.facts.provider_id: estimate}
        if estimates:
            self._estimates.update(estimates)
        self.jobs = JobController()
        self.reservations = ReservationBook(paths.root)
        self.store = Store(paths.database)
        self.stories = StoryStore(paths.stories, paths.outputs)
        self.story_planner = QwenStoryPlanner(paths.models / "qwen-story-planner")
        # Qwen2.5-1.5B has a verified local snapshot but did not pass the
        # Stage 7 adversarial structured-output gate. Keep the optional
        # product feature unavailable until a replacement candidate passes.
        self.story_planner_enabled = False
        self._job_outputs: dict[str, str] = {}
        self._job_results: dict[str, dict] = {}
        self.narrator = narrator

    def submit(self, payload: dict, on_progress: Callable[[Job], None], on_terminal: Callable[[Job, dict | None], None]) -> Job:
        provider = self._provider_for(payload)
        request_values = self._parse_request(payload, provider)
        return self._submit(provider, request_values, on_progress, on_terminal)

    def submit_video_edit(self, payload: dict, on_progress: Callable[[Job], None], on_terminal: Callable[[Job, dict | None], None]) -> Job:
        """Create an immutable video-edit descendant from an owned output ID."""
        provider = self._provider_for(payload)
        if Capability.VIDEO_EDITING not in provider.facts.capabilities:
            raise GenerationError("selected model does not support video editing")
        request_values = self._parse_video_edit_request(payload, provider)
        return self._submit(provider, request_values, on_progress, on_terminal)

    def submit_image_edit(self, payload: dict, on_progress: Callable[[Job], None], on_terminal: Callable[[Job, dict | None], None]) -> Job:
        """Create an immutable image-edit descendant from an owned image output."""
        provider = self._provider_for(payload)
        if Capability.IMAGE_EDITING not in provider.facts.capabilities:
            raise GenerationError("selected model does not support image editing")
        request_values = self._parse_image_edit_request(payload, provider)
        return self._submit(provider, request_values, on_progress, on_terminal)

    def submit_narration(self, payload: dict, on_progress: Callable[[Job], None], on_terminal: Callable[[Job, dict | None], None]) -> Job:
        if self.narrator is None:
            raise GenerationError("narration is not available")
        text = _required(payload, "text", str).strip()
        if not text or len(text) > 4_000:
            raise GenerationError("narration text must contain 1 to 4000 characters")
        source_output_id = _required(payload, "source_output_id", str)
        if not _OUTPUT_ID.fullmatch(source_output_id):
            raise GenerationError("source output is invalid")
        source = self.paths.outputs / source_output_id / "video.mp4"
        metadata_path = self.paths.outputs / source_output_id / "metadata.json"
        if not source.is_file() or not metadata_path.is_file():
            raise GenerationError("source video is unavailable")
        try:
            source_metadata = json.loads(metadata_path.read_text())
            request = source_metadata["request"]
            if request.get("capability") not in {Capability.VIDEO_GENERATION.value, Capability.VIDEO_EDITING.value, Capability.NARRATION.value}:
                raise ValueError()
            frames, fps = request["frames"], request["fps"]
            if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (frames, fps)):
                raise ValueError()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise GenerationError("source video metadata is invalid") from error
        return self._submit_narration(source_output_id, source, text, frames / fps, request, on_progress, on_terminal)

    def _submit_narration(self, source_output_id: str, source: Path, text: str, video_duration: float, source_request: dict, on_progress: Callable[[Job], None], on_terminal: Callable[[Job, dict | None], None]) -> Job:
        output_paths: OutputPaths | None = None
        job_ready = __import__("threading").Event()
        job: Job | None = None
        narrator = self.narrator
        assert narrator is not None

        def runner(progress, cancelled) -> None:
            nonlocal output_paths
            job_ready.wait(); assert job is not None
            try:
                # Diffusion models are substantially larger than the narrator.
                # Do not co-reside without a measured combined-memory budget.
                for provider in self._providers.values():
                    provider.unload()
                output_paths = allocate(self.paths.outputs)
                wav = output_paths.partial_dir / "narration.wav"
                self._on_progress(job, 0.05, "Synthesizing narration", on_progress)
                narrator.synthesize(text, wav, cancelled)
                if cancelled(): raise InterruptedError("narration cancelled")
                facts = pad_or_reject_wav(wav, video_duration)
                self._on_progress(job, 0.75, "Replacing audio track", on_progress)
                replace_audio(source, wav, output_paths.partial_dir / "video.mp4", video_duration)
                wav.unlink(missing_ok=True)
                request = {"capability": Capability.NARRATION, "text": text, "source_output_id": source_output_id,
                           "width": source_request["width"], "height": source_request["height"],
                           "frames": source_request["frames"], "fps": source_request["fps"],
                           "seed": 0, "steps": 0, "guidance_scale": 0.0, "recipe": "Narration"}
                self._write_metadata(output_paths, request, {"media_file": "video.mp4", "media_type": "video/mp4", **facts}, None)
                final_dir = promote(output_paths); self._index_output(final_dir / "metadata.json")
                self._job_outputs[job.job_id] = output_paths.output_id
                self._on_progress(job, 1.0, "Narration saved", on_progress)
            except BaseException:
                if output_paths is not None and output_paths.partial_dir.exists(): shutil.rmtree(output_paths.partial_dir)
                raise
            finally:
                # The real memory policy is recorded only after the Stage 5
                # measurement. Until then do not assume TTS can co-reside.
                narrator.unload()

        job = self.jobs.submit(runner); job_ready.set(); self._watch_terminal(job, on_terminal)
        return job

    def _submit(self, provider: Provider, request_values: dict, on_progress: Callable[[Job], None], on_terminal: Callable[[Job, dict | None], None]) -> Job:
        output_paths: OutputPaths | None = None
        job_ready = __import__("threading").Event()
        job: Job | None = None

        def runner(progress, cancelled) -> None:
            nonlocal output_paths
            job_ready.wait()
            assert job is not None
            try:
                # Each provider caches its pipeline until explicitly unloaded, so
                # switching models mid-session (e.g. a generate followed by an
                # edit_image on a different provider) would otherwise try to hold
                # both resident at once. Measured: flux-schnell's ~31 GiB plus
                # qwen-image-edit's ~58 GiB exceeds the ~64 GiB MPS ceiling.
                for other_id, other_provider in self._providers.items():
                    if other_id != provider.facts.provider_id:
                        other_provider.unload()
                if self.narrator is not None:
                    self.narrator.unload()
                with self.reservations.hold(self._estimates[provider.facts.provider_id]):
                    output_paths = allocate(self.paths.outputs)
                    request = OperationRequest(operation_id=output_paths.output_id, output_dir=output_paths.partial_dir, **request_values)
                    result = provider.run(request, lambda fraction, text: self._on_progress(job, fraction, text, on_progress), cancelled)
                    media_file = result.get("media_file")
                    if not isinstance(media_file, str):
                        raise GenerationError("provider did not identify a media file")
                    media_path = resolve_owned_file(self.paths.outputs / ".partial", output_paths.output_id, media_file)
                    if not media_path.is_file():
                        raise GenerationError("provider did not produce the declared media file")
                    self._write_metadata(output_paths, request_values, result, provider)
                    final_dir = promote(output_paths)
                    self._index_output(final_dir / "metadata.json")
                    self._job_outputs[job.job_id] = output_paths.output_id
            except BaseException:
                if output_paths is not None and output_paths.partial_dir.exists():
                    shutil.rmtree(output_paths.partial_dir)
                raise

        # The lambda closes over job after submit creates it, but runner cannot
        # run until that creation is complete under JobController's lock.
        job = self.jobs.submit(runner)
        job_ready.set()
        self._watch_terminal(job, on_terminal)
        return job

    def _provider_for(self, payload: dict) -> Provider:
        model_id = payload.get("model_id", self.provider.facts.provider_id)
        if not isinstance(model_id, str) or model_id not in self._providers:
            raise GenerationError("selected model is not available")
        if model_id not in self._estimates:
            raise GenerationError("selected model has no measured disk estimate")
        return self._providers[model_id]

    def _parse_request(self, payload: dict, provider: Provider) -> dict:
        prompt = _required(payload, "prompt", str).strip()
        if not prompt or len(prompt) > 4_000:
            raise GenerationError("prompt must contain 1 to 4000 characters")
        if provider.facts.capabilities == frozenset({Capability.IMAGE_GENERATION}):
            return self._parse_image_request(payload, prompt, provider)
        recipe = payload.get("recipe", "Balanced")
        if recipe not in (provider.facts.calibration_recipes or _LEGACY_RECIPE_NAMES):
            raise GenerationError("recipe is not available")
        try:
            profile = provider.measured_recipes().recipes[recipe]
        except AttributeError:
            profile = None
        except (KeyError, ValueError, OSError, RuntimeError) as error:
            raise GenerationError("requested recipe is not measured for the selected model") from error
        values = {
            "capability": Capability.VIDEO_GENERATION,
            "prompt": prompt,
            "seed": _required(payload, "seed", int),
            "width": profile.width if profile else _required(payload, "width", int),
            "height": profile.height if profile else _required(payload, "height", int),
            "frames": profile.frames if profile else _required(payload, "frames", int),
            "fps": profile.fps if profile else _required(payload, "fps", int),
            "steps": profile.steps if profile else _required(payload, "steps", int),
            "guidance_scale": profile.guidance_scale if profile else float(_required(payload, "guidance_scale", (int, float))),
            "recipe": recipe,
        }
        source_image_id = payload.get("source_image_id")
        supported_modes = getattr(getattr(provider, "spec", None), "supported_modes", frozenset({"text", "image"}))
        if source_image_id is None and "text" not in supported_modes:
            raise GenerationError("selected model requires an image-to-video source")
        if source_image_id is not None and "image" not in supported_modes:
            raise GenerationError("selected model supports text-to-video only")
        if source_image_id is not None:
            if not isinstance(source_image_id, str) or not _SOURCE_IMAGE_ID.fullmatch(source_image_id):
                raise GenerationError("source image is invalid")
            source_image = self.paths.temporary / "imports" / source_image_id
            if not source_image.is_file():
                raise GenerationError("selected source image is unavailable")
            values["source_image"] = source_image
        if any(values[key] <= 0 for key in ("width", "height", "frames", "fps", "steps")):
            raise GenerationError("generation dimensions, frames, FPS, and steps must be positive")
        return values

    def _parse_image_request(self, payload: dict, prompt: str, provider: Provider) -> dict:
        if payload.get("source_image_id") is not None:
            raise GenerationError("the selected image provider does not support source images")
        try:
            profile = provider.measured_profile()
        except (AttributeError, ValueError, OSError, RuntimeError) as error:
            raise GenerationError("selected image generation settings are not measured") from error
        return {
            "capability": Capability.IMAGE_GENERATION,
            "prompt": prompt,
            "seed": _required(payload, "seed", int),
            "width": profile.width,
            "height": profile.height,
            "frames": 1,
            "fps": 1,
            "steps": profile.steps,
            "guidance_scale": profile.guidance_scale,
            "recipe": "Measured",
        }

    def _parse_video_edit_request(self, payload: dict, provider: Provider) -> dict:
        prompt = _required(payload, "prompt", str).strip()
        if not prompt or len(prompt) > 4_000:
            raise GenerationError("prompt must contain 1 to 4000 characters")
        source_output_id = _required(payload, "source_output_id", str)
        if not _OUTPUT_ID.fullmatch(source_output_id):
            raise GenerationError("source output is invalid")
        source_dir = self.paths.outputs / source_output_id
        source_video = source_dir / "video.mp4"
        metadata_path = source_dir / "metadata.json"
        if not source_video.is_file() or not metadata_path.is_file():
            raise GenerationError("source video is unavailable")
        try:
            source_metadata = json.loads(metadata_path.read_text())
            source_request = source_metadata["request"]
            if source_request.get("capability") not in {Capability.VIDEO_GENERATION.value, Capability.VIDEO_EDITING.value}:
                raise ValueError()
            width, height, frames, fps = (source_request[key] for key in ("width", "height", "frames", "fps"))
            if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (width, height, frames, fps)):
                raise ValueError()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise GenerationError("source video metadata is invalid") from error
        change_amount = payload.get("change_amount")
        if isinstance(change_amount, bool) or not isinstance(change_amount, (int, float)) or not 0.05 <= float(change_amount) <= 0.95:
            raise GenerationError("change amount must be between 0.05 and 0.95")
        recipe = payload.get("recipe", "Balanced")
        if recipe not in (provider.facts.calibration_recipes or _LEGACY_RECIPE_NAMES):
            raise GenerationError("recipe is not available")
        try:
            profile = provider.measured_recipes().recipes[recipe]
        except (AttributeError, KeyError, ValueError, OSError, RuntimeError) as error:
            raise GenerationError("requested recipe is not measured for video editing") from error
        # Editing preserves the source's measured media facts; a profile with
        # different dimensions or cadence would silently crop or retime it.
        if (profile.width, profile.height, profile.frames, profile.fps) != (width, height, frames, fps):
            raise GenerationError("source video does not match the selected measured edit recipe")
        return {
            "capability": Capability.VIDEO_EDITING,
            "prompt": prompt,
            "seed": _required(payload, "seed", int),
            "width": width,
            "height": height,
            "frames": frames,
            "fps": fps,
            "steps": profile.steps,
            "guidance_scale": profile.guidance_scale,
            "recipe": recipe,
            "source_video": source_video,
            "source_output_id": source_output_id,
            "change_amount": float(change_amount),
        }

    def _parse_image_edit_request(self, payload: dict, provider: Provider) -> dict:
        prompt = _required(payload, "prompt", str).strip()
        if not prompt or len(prompt) > 4_000:
            raise GenerationError("prompt must contain 1 to 4000 characters")
        source_output_id = _required(payload, "source_output_id", str)
        if not _OUTPUT_ID.fullmatch(source_output_id):
            raise GenerationError("source output is invalid")
        source_dir = self.paths.outputs / source_output_id
        source_image = source_dir / "image.png"
        metadata_path = source_dir / "metadata.json"
        if not source_image.is_file() or not metadata_path.is_file():
            raise GenerationError("source image is unavailable")
        try:
            metadata = json.loads(metadata_path.read_text())
            if metadata["request"].get("capability") not in {Capability.IMAGE_GENERATION.value, Capability.IMAGE_EDITING.value}:
                raise ValueError()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise GenerationError("source image metadata is invalid") from error
        try:
            profile = provider.measured_profile()
        except (AttributeError, ValueError, OSError, RuntimeError) as error:
            raise GenerationError("selected image editing settings are not measured") from error
        return {
            "capability": Capability.IMAGE_EDITING,
            "prompt": prompt,
            "seed": _required(payload, "seed", int),
            "width": profile.width,
            "height": profile.height,
            "frames": 1,
            "fps": 1,
            "steps": profile.steps,
            "guidance_scale": profile.guidance_scale,
            "recipe": "Measured",
            "source_image": source_image,
            "source_output_id": source_output_id,
        }

    def _on_progress(self, job: Job, fraction: float, text: str, callback: Callable[[Job], None]) -> None:
        self.jobs._progress(job, fraction, text)
        callback(self.jobs.status(job.job_id))

    def _watch_terminal(self, job: Job, callback: Callable[[Job, dict | None], None]) -> None:
        import threading
        import time

        def watch() -> None:
            while True:
                current = self.jobs.status(job.job_id)
                if current.state in TERMINAL_STATES:
                    output_id = self._job_outputs.pop(job.job_id, None)
                    output = self._job_results.pop(job.job_id, None)
                    if current.state == JobState.SUCCEEDED and output_id:
                        output = {"output_id": output_id}
                    callback(current, output)
                    return
                time.sleep(0.01)
        threading.Thread(target=watch, daemon=True).start()

    def _write_metadata(self, paths: OutputPaths, request: dict, result: dict[str, object], provider: Provider | None) -> None:
        metadata_request = {
            key: (value.value if isinstance(value, Capability) else value.name if isinstance(value, Path) else value)
            for key, value in request.items()
        }
        metadata = {
            "schema_version": 1,
            "output_id": paths.output_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider.facts.provider_id if provider else "kokoro-onnx",
            "provider_revision": provider.facts.revision if provider else "0.5.0",
            "request": metadata_request,
            "result": result,
            "lineage": ([{"output_id": request["source_output_id"], "relation": "narrated_from" if request.get("capability") == Capability.NARRATION else "edited_from"}]
                        if request.get("source_output_id") else []),
        }
        (paths.partial_dir / "metadata.json").write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")))

    def _index_output(self, metadata_path: Path) -> None:
        connection = self.store.open()
        try:
            raw = metadata_path.read_text()
            output_id = json.loads(raw)["output_id"]
            with connection:
                connection.execute("INSERT INTO outputs(output_id, metadata_json) VALUES(?, ?)", (output_id, raw))
        finally:
            connection.close()

    def status_payload(self) -> dict:
        current = self.jobs.current()
        profiles, image_profile = self._measured_profiles(self.provider)
        available_models = {}
        model_ids = list(REGISTRY)
        model_ids.extend(model_id for model_id in self._providers if model_id not in REGISTRY)
        for model_id in model_ids:
            spec = REGISTRY.get(model_id)
            provider = self._providers.get(model_id)
            recipes, image = self._measured_profiles(provider) if provider else (None, None)
            available_models[model_id] = {
                "capabilities": [capability.value for capability in (spec.capabilities if spec else provider.facts.capabilities)],
                "modes": sorted(spec.supported_modes) if spec else ["text", "image"],
                "installed": self._model_status(model_id)["installed"] if spec else True,
                "measured_recipes": recipes,
                "measured_image_profile": image,
                "reason": spec.reason if spec else "Runtime test provider.",
                "calibration": self._calibration_info(provider, recipes, image),
            }
        return {
            "active_job": self._job_payload(current) if current else None,
            "measured_recipes": profiles,
            "measured_image_profile": image_profile,
            "available_models": available_models,
        }

    def model_catalog(self) -> dict:
        """Expose reviewed model facts and local install state, never a download URL."""
        models = [self._model_status(model_id) for model_id in REGISTRY] + [self._kokoro_status()]
        for model_id in sorted(RETIRED_MODEL_IDS):
            status = self._retired_model_status(model_id)
            if status["installed"]:
                models.append(status)
        return {"models": models}

    def submit_model_download(self, model_id: str, on_progress: Callable[[Job], None], on_terminal: Callable[[Job, dict | None], None]) -> Job:
        if model_id not in REGISTRY:
            raise GenerationError("selected model is not managed by SynVid")
        spec = REGISTRY[model_id]
        job_ready = __import__("threading").Event(); job: Job | None = None
        if model_id == "wan2.2-ti2v-5b-mlx":
            # This model's app-owned snapshot is produced by a local MLX
            # conversion, not a direct copy of the upstream repository's own
            # files (see model_install_wan_mlx.py) — the generic download_model
            # path cannot fetch files that only exist after conversion.
            from .model_install_wan_mlx import PEAK_INSTALL_BYTES, install_wan_mlx_snapshot
            def runner(_progress, cancelled) -> None:
                job_ready.wait(); assert job is not None
                with self.reservations.hold(Estimate(PEAK_INSTALL_BYTES, True)):
                    result = install_wan_mlx_snapshot(self.paths, spec, lambda fraction, text: self._on_progress(job, fraction, text, on_progress), cancelled)
                    self._job_results[job.job_id] = {"model_install": result}
        else:
            def runner(_progress, cancelled) -> None:
                job_ready.wait(); assert job is not None
                with self.reservations.hold(Estimate(int(spec.expected_size_gib * 1024**3), True)):
                    result = download_model(self.paths, spec, lambda fraction, text: self._on_progress(job, fraction, text, on_progress), cancelled)
                    self._job_results[job.job_id] = {"model_install": result}
        job = self.jobs.submit(runner, operation="model_download"); job_ready.set(); self._watch_terminal(job, on_terminal)
        return job

    def submit_calibration(self, model_id: str, recipe_name: str, on_progress: Callable[[Job], None], on_terminal: Callable[[Job, dict | None], None]) -> Job:
        """Measure a quality-approved recipe on this Mac and write a real measured-profile.json.

        Never writes anything on failure or cancellation — an interrupted
        calibration must not leave a half-written profile that could let a
        job through with unverified settings.
        """
        provider = self._providers.get(model_id)
        if provider is None:
            raise GenerationError("selected model is not available")
        if recipe_name not in provider.facts.calibration_recipes:
            raise GenerationError("selected recipe has no quality-approved calibration shape")
        profile_path = self.paths.models / model_id / "measured-profile.json"
        job_ready = __import__("threading").Event(); job: Job | None = None

        def runner(_progress, cancelled) -> None:
            job_ready.wait(); assert job is not None
            # Same measured-memory-ceiling reasoning as generation: only one
            # diffusion pipeline may be resident while calibration measures
            # this one's real footprint.
            for other_id, other_provider in self._providers.items():
                if other_id != model_id:
                    other_provider.unload()
            if self.narrator is not None:
                self.narrator.unload()
            existing_raw = None
            if profile_path.exists():
                try:
                    existing_raw = json.loads(profile_path.read_text())
                except (OSError, ValueError, json.JSONDecodeError):
                    existing_raw = None
            new_content = provider.calibrate(
                recipe_name, existing_raw,
                lambda fraction, text: self._on_progress(job, fraction, text, on_progress), cancelled,
            )
            self._write_json_atomically(profile_path, new_content)
            measured = new_content.get("recipes", {}).get(recipe_name) if isinstance(new_content, dict) else None
            disk_bytes = measured.get("estimated_disk_bytes") if isinstance(measured, dict) else None
            if isinstance(disk_bytes, int) and disk_bytes > 0:
                # Calibration can be the first measured profile in a fresh
                # worker. Refresh admission immediately instead of requiring
                # an app restart before the newly measured model can run.
                self._estimates[model_id] = Estimate(disk_bytes, True)
            self._job_results[job.job_id] = {"calibration": {"model_id": model_id, "recipe": recipe_name}}

        job = self.jobs.submit(runner, operation="calibrate"); job_ready.set(); self._watch_terminal(job, on_terminal)
        return job

    def remove_model(self, model_id: str) -> dict:
        if self.jobs.current() is not None:
            raise GenerationError("wait for the active job before removing a model")
        if model_id not in REGISTRY and model_id not in RETIRED_MODEL_IDS and model_id != "kokoro-onnx":
            raise GenerationError("selected model is not managed by SynVid")
        if model_id in self._providers:
            self._providers[model_id].unload()
        elif model_id == "kokoro-onnx" and self.narrator is not None:
            self.narrator.unload()
        root = self.paths.models / model_id
        if root.is_symlink() or not root.exists():
            return {"removed": False, "model_id": model_id, "freed_bytes": 0}
        if not root.is_dir():
            raise GenerationError("model storage is invalid; removal was refused")
        freed_bytes = self._tree_size(root)
        shutil.rmtree(root)
        return {"removed": True, "model_id": model_id, "freed_bytes": freed_bytes}

    def clean_temporary(self) -> dict:
        if self.jobs.current() is not None:
            raise GenerationError("wait for the active job before cleaning temporary files")
        root = self.paths.temporary
        freed_bytes = self._tree_size(root)
        for child in root.iterdir() if root.is_dir() else ():
            if child.is_symlink() or child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                shutil.rmtree(child)
        root.mkdir(parents=True, exist_ok=True)
        return {"freed_bytes": freed_bytes}

    def delete_output(self, output_id: str, *, cascade: bool = False) -> dict:
        """Explicitly remove one unreferenced, completed owned output.

        Outputs are immutable while retained, but retention is not permanent.
        Refuse a default deletion if another output refers to it. An explicit
        cascade removes the complete generated-descendant tree, but never a
        Story-referenced artifact.
        """
        if self.jobs.current() is not None:
            raise GenerationError("wait for the active job before deleting a library item")
        if not _OUTPUT_ID.fullmatch(output_id):
            raise GenerationError("output ID is invalid")
        output_ids = self._output_descendant_ids(output_id)
        if len(output_ids) > 1 and not cascade:
            raise GenerationError("this item has generated descendants and cannot be deleted here")
        for item_id in output_ids:
            output_dir = self.paths.outputs / item_id
            if output_dir.is_symlink() or not output_dir.is_dir():
                raise GenerationError("library item is unavailable")
            if self._story_references_output(item_id):
                raise GenerationError("this item or one of its descendants is used by a Story and cannot be deleted here")
        freed_bytes = sum(self._tree_size(self.paths.outputs / item_id) for item_id in output_ids)
        for item_id in output_ids:
            shutil.rmtree(self.paths.outputs / item_id)
            self.store.remove_output(item_id)
        return {"deleted": True, "output_id": output_id, "deleted_output_ids": output_ids, "freed_bytes": freed_bytes}

    def _output_has_descendants(self, output_id: str) -> bool:
        return len(self._output_descendant_ids(output_id)) > 1

    def _output_descendant_ids(self, output_id: str) -> list[str]:
        """Return an output and every transitive generated descendant."""
        connection = self.store.open()
        try:
            rows = connection.execute("SELECT metadata_json FROM outputs WHERE output_id != ?", (output_id,)).fetchall()
        finally:
            connection.close()
        lineage_by_output: dict[str, list[dict]] = {}
        for (raw,) in rows:
            try:
                metadata = json.loads(raw)
                item_id = metadata.get("output_id")
                lineage = metadata.get("lineage", [])
                if isinstance(item_id, str) and isinstance(lineage, list):
                    lineage_by_output[item_id] = [item for item in lineage if isinstance(item, dict)]
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        selected = [output_id]
        known = {output_id}
        while True:
            additions = [item_id for item_id, lineage in lineage_by_output.items()
                         if item_id not in known and any(item.get("output_id") in known for item in lineage)]
            if not additions:
                return selected
            selected.extend(additions)
            known.update(additions)

    def _story_references_output(self, output_id: str) -> bool:
        for summary in self.stories.list():
            try:
                story = self.stories.get(summary["story_id"])
            except (KeyError, StoryError):
                continue
            if output_id in self.stories._artifact_ids(story):
                return True
        return False

    def _model_status(self, model_id: str) -> dict:
        spec = REGISTRY[model_id]
        root = self.paths.models / model_id
        installed = root.is_dir() and not root.is_symlink() and (root / "snapshot").is_dir()
        provider = self._providers.get(model_id)
        recipes, image = self._measured_profiles(provider) if provider else (None, None)
        return {
            "model_id": model_id,
            "display_name": spec.display_name,
            "reason": spec.reason,
            "repository": spec.repository,
            "revision": spec.revision,
            "license": spec.license_name,
            "profile": spec.profile,
            "expected_size_gib": spec.expected_size_gib,
            "requires_access_confirmation": spec.requires_access_confirmation,
            "modes": sorted(spec.supported_modes),
            "installed": installed,
            "installed_bytes": self._tree_size(root) if installed else 0,
            "calibration": self._calibration_info(provider, recipes, image),
        }

    def _retired_model_status(self, model_id: str) -> dict:
        display_name, reason = RETIRED_MODELS[model_id]
        root = self.paths.models / model_id
        installed = root.is_dir() and not root.is_symlink() and (root / "snapshot").is_dir()
        return {
            "model_id": model_id,
            "display_name": display_name,
            "reason": reason,
            "repository": "retired",
            "revision": "retired",
            "license": "not available for new downloads",
            "profile": "retired",
            "expected_size_gib": 0,
            "requires_access_confirmation": False,
            "modes": [],
            "installed": installed,
            "installed_bytes": self._tree_size(root) if installed else 0,
            "calibration": None,
            "retired": True,
        }

    def _kokoro_status(self) -> dict:
        root = self.paths.models / "kokoro-onnx"
        installed = root.is_dir() and not root.is_symlink() and (root / "snapshot").is_dir()
        return {
            "model_id": "kokoro-onnx",
            "display_name": "Kokoro Voice",
            "reason": "Synthesizes local narration for completed videos and Story Mode scenes.",
            "repository": "hexgrad/Kokoro-82M",
            "revision": "SynVid verified voice snapshot",
            "license": "Apache-2.0 model; MIT runtime",
            "profile": "shareable",
            "expected_size_gib": 0.4,
            "requires_access_confirmation": False,
            "installed": installed,
            "installed_bytes": self._tree_size(root) if installed else 0,
        }

    @staticmethod
    def _tree_size(root: Path) -> int:
        if root.is_symlink() or not root.is_dir():
            return 0
        return sum(path.stat().st_size for path in root.rglob("*") if path.is_file() and not path.is_symlink())

    @staticmethod
    def _measured_profiles(provider: Provider) -> tuple[dict | None, dict | None]:
        profiles = None
        try:
            measured = provider.measured_recipes().recipes
            profiles = {
                name: {
                    "width": profile.width, "height": profile.height,
                    "frames": profile.frames, "fps": profile.fps,
                    "steps": profile.steps, "guidance_scale": profile.guidance_scale,
                    "mode": profile.mode,
                }
                for name, profile in measured.items()
            }
        except (AttributeError, ValueError, OSError, RuntimeError):
            # A missing profile is intentionally surfaced as unavailable rather
            # than guessed by the UI.
            pass
        image_profile = None
        if Capability.VIDEO_GENERATION not in provider.facts.capabilities:
            try:
                profile = provider.measured_profile()
                image_profile = {
                    "width": profile.width,
                    "height": profile.height,
                    "steps": profile.steps,
                    "guidance_scale": profile.guidance_scale,
                }
            except (AttributeError, ValueError, OSError, RuntimeError):
                pass
        return profiles, image_profile

    @staticmethod
    def _calibration_info(provider: Provider | None, measured_recipes: dict | None, measured_image_profile: dict | None) -> dict[str, dict] | None:
        """Per-recipe calibration state for the UI: already measured vs. a static reference shape."""
        if provider is None or not provider.facts.calibration_recipes:
            return None
        measured_names = frozenset(measured_recipes) if measured_recipes else (frozenset({"Balanced"}) if measured_image_profile is not None else frozenset())
        return {
            recipe_name: {
                "measured": recipe_name in measured_names,
                "reference": provider.calibration_reference(recipe_name),
            }
            for recipe_name in sorted(provider.facts.calibration_recipes)
        }

    @staticmethod
    def _write_json_atomically(destination: Path, payload: dict) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".partial")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True))
        temporary.replace(destination)

    def export(self, output_id: str, profile: str) -> dict:
        if profile not in {"high", "balanced", "small"}:
            raise GenerationError("export profile is not available")
        if not output_id or len(output_id) > 128 or "/" in output_id or "\\" in output_id:
            raise GenerationError("invalid output ID")
        source = self.paths.outputs / output_id / "video.mp4"
        if not source.is_file():
            raise GenerationError("canonical output is unavailable")
        destination_dir = self.paths.outputs / output_id / "exports"
        destination_dir.mkdir(exist_ok=True)
        destination = destination_dir / f"{profile}.mp4"
        temporary = destination.with_name(f"{profile}.partial.mp4")
        crf = {"high": "18", "balanced": "23", "small": "30"}[profile]
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            subprocess.run(
                [ffmpeg, "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-c:a", "aac", "-preset", "medium", "-crf", crf, "-pix_fmt", "yuv420p", str(temporary)],
                check=True, capture_output=True, text=True,
            )
            os.replace(temporary, destination)
        except (OSError, subprocess.SubprocessError) as error:
            temporary.unlink(missing_ok=True)
            raise GenerationError("could not create export") from error
        return {"output_id": output_id, "profile": profile, "export_file": f"exports/{profile}.mp4"}

    def create_story(self, payload: dict) -> dict:
        return self.stories.create(payload)

    def list_stories(self) -> list[dict]:
        return self.stories.list()

    def get_story(self, story_id: str) -> dict:
        return self.stories.get(story_id)

    def delete_story(self, payload: dict) -> dict:
        """Remove a story project. Generated artifacts are retained by default.

        Mirrors delete_output's cascade contract: the story document itself
        is always removed (subject to the optimistic revision check), but its
        scene/composition artifacts are only deleted when cascade=True, and
        only when nothing else (another story, another output's lineage)
        still needs them.
        """
        if self.jobs.current() is not None:
            raise GenerationError("wait for the active job before deleting a story")
        story_id = _required(payload, "story_id", str)
        cascade = payload.get("cascade", False)
        if not isinstance(cascade, bool):
            raise GenerationError("delete_story cascade must be a boolean")
        story = self.stories.get(story_id)
        artifact_ids = self.stories._artifact_ids(story)
        self.stories.delete(payload)
        deleted_output_ids: list[str] = []
        retained_output_ids: list[str] = []
        freed_bytes = 0
        for output_id in artifact_ids:
            if cascade:
                try:
                    result = self.delete_output(output_id, cascade=True)
                except GenerationError:
                    if (self.paths.outputs / output_id).is_dir():
                        retained_output_ids.append(output_id)
                    continue
                deleted_output_ids.extend(result["deleted_output_ids"])
                freed_bytes += result["freed_bytes"]
            else:
                retained_output_ids.append(output_id)
        return {
            "deleted": True,
            "story_id": story_id,
            "cascade": cascade,
            "deleted_output_ids": sorted(set(deleted_output_ids)),
            "retained_output_ids": sorted(set(retained_output_ids) - set(deleted_output_ids)),
            "freed_bytes": freed_bytes,
        }

    def update_story(self, payload: dict) -> dict:
        return self.stories.update(payload)

    def add_story_scene(self, payload: dict) -> dict:
        return self.stories.add_scene(payload)

    def update_story_scene(self, payload: dict) -> dict:
        return self.stories.update_scene(payload)

    def reorder_story_scenes(self, payload: dict) -> dict:
        return self.stories.reorder(payload)

    def submit_story_draft(self, payload: dict, on_progress: Callable[[Job], None], on_terminal: Callable[[Job, dict | None], None]) -> Job:
        if not self.story_planner_enabled:
            raise GenerationError("local story drafting is unavailable because its structured-output gate has not passed")
        story_id, expected = payload.get("story_id"), payload.get("expected_revision")
        story = self.stories.get(story_id)
        if story["revision"] != expected: raise GenerationError("story changed in another window; reload before drafting")
        count = payload.get("count", 3)
        if isinstance(count, bool) or not isinstance(count, int): raise GenerationError("requested scene count is invalid")
        ready = __import__("threading").Event(); job: Job | None = None
        def runner(_progress, cancelled) -> None:
            ready.wait(); assert job is not None
            self._on_progress(job, 0.05, "Loading local story planner", on_progress)
            try:
                scenes = self.story_planner.draft(story["premise"], story["style_bible"], count, cancelled)
                if cancelled(): raise InterruptedError("story drafting cancelled")
                self._job_results[job.job_id] = {"story_draft": {"story_id": story_id, "revision": expected, "scenes": scenes}}
                self._on_progress(job, 1.0, "Story draft ready for review", on_progress)
            finally:
                # Never retain the compact instruction model beside render/TTS.
                self.story_planner.unload()
        job = self.jobs.submit(runner); ready.set(); self._watch_terminal(job, on_terminal)
        return job

    def record_story_artifact(self, payload: dict) -> dict:
        """Promote an existing validated immutable output as a scene artifact.

        This is the common endpoint for generated variants and future native
        imports.  An opaque ID alone is never enough: its contained sidecar and
        declared media type must match the requested scene step.
        """
        output_id = _required(payload, "output_id", str); step = _required(payload, "step", str)
        if not _OUTPUT_ID.fullmatch(output_id): raise GenerationError("story artifact output is invalid")
        expected = {"still": "image.png", "clip": "video.mp4", "narration": "video.mp4", "segment": "video.mp4", "subtitles": "subtitles.srt"}
        if step not in expected: raise GenerationError("story artifact step is invalid")
        try:
            metadata = json.loads((self.paths.outputs / output_id / "metadata.json").read_text())
            media_file = metadata["result"]["media_file"]
            media = resolve_owned_file(self.paths.outputs, output_id, media_file)
            if media_file != expected[step] or not media.is_file() or media.is_symlink(): raise ValueError()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise GenerationError("replacement media does not match the selected story step") from error
        return self.stories.record_artifact(payload)

    def import_story_still(self, payload: dict) -> dict:
        """Normalize a Rust-picked image into an immutable Story still output."""
        source_id = _required(payload, "source_image_id", str)
        if not _SOURCE_IMAGE_ID.fullmatch(source_id): raise GenerationError("selected source image is invalid")
        source = self.paths.temporary / "imports" / source_id
        paths: OutputPaths | None = None
        try:
            if not source.is_file() or source.is_symlink() or source.stat().st_size > 64 * 1024 * 1024: raise ValueError()
            from PIL import Image
            with Image.open(source) as image:
                image.verify()
            with Image.open(source) as image:
                if image.width < 1 or image.height < 1 or image.width > 8192 or image.height > 8192: raise ValueError()
                normalized = image.convert("RGB")
                paths = allocate(self.paths.outputs); normalized.save(paths.partial_dir / "image.png", format="PNG", optimize=True)
        except (OSError, ValueError, ImportError) as error:
            if paths and paths.partial_dir.exists(): shutil.rmtree(paths.partial_dir)
            raise GenerationError("selected story image is invalid") from error
        assert paths is not None
        request = {"capability": "story_import", "source_import_id": source_id, "width": normalized.width, "height": normalized.height, "frames": 1, "fps": 1, "seed": 0, "steps": 0, "guidance_scale": 0.0, "recipe": "PNG normalization"}
        self._write_metadata(paths, request, {"media_file": "image.png", "media_type": "image/png"}, None)
        final = promote(paths); self._index_output(final / "metadata.json")
        replacement = dict(payload); replacement["output_id"] = paths.output_id; replacement["step"] = "still"
        return self.record_story_artifact(replacement)

    def import_story_subtitles(self, payload: dict) -> dict:
        source_id = _required(payload, "source_subtitle_id", str)
        if not _SOURCE_IMAGE_ID.fullmatch(source_id): raise GenerationError("selected subtitle file is invalid")
        source = self.paths.temporary / "imports" / source_id
        try:
            if not source.is_file() or source.is_symlink() or source.stat().st_size > 4 * 1024 * 1024: raise ValueError()
            text = source.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
            blocks = [item for item in text.split("\n\n") if item.strip()]
            if not blocks or len(blocks) > 10_000 or any(" --> " not in item for item in blocks): raise ValueError()
        except (OSError, UnicodeDecodeError, ValueError) as error: raise GenerationError("selected subtitle file is not valid SRT") from error
        paths = allocate(self.paths.outputs); (paths.partial_dir / "subtitles.srt").write_text(text + "\n", encoding="utf-8")
        request = {"capability": "story_import", "source_import_id": source_id, "width": 0, "height": 0, "frames": 0, "fps": 0, "seed": 0, "steps": 0, "guidance_scale": 0.0, "recipe": "Validated SRT"}
        self._write_metadata(paths, request, {"media_file": "subtitles.srt", "media_type": "application/x-subrip"}, None)
        final = promote(paths); self._index_output(final / "metadata.json")
        replacement = dict(payload); replacement["output_id"] = paths.output_id; replacement["step"] = "subtitles"
        return self.record_story_artifact(replacement)

    def import_story_narration(self, payload: dict) -> dict:
        source_id = _required(payload, "source_audio_id", str); story_id = _required(payload, "story_id", str); scene_id = _required(payload, "scene_id", str)
        if not _SOURCE_IMAGE_ID.fullmatch(source_id): raise GenerationError("selected narration file is invalid")
        story = self.stories.get(story_id); expected = _required(payload, "expected_revision", int)
        if story["revision"] != expected: raise GenerationError("story changed in another window; reload before replacing narration")
        scene = next((item for item in story["scenes"] if item["scene_id"] == scene_id), None)
        if not scene or not isinstance(scene["artifacts"].get("clip"), dict): raise GenerationError("a current scene clip is required before replacing narration")
        clip_id = scene["artifacts"]["clip"]["output_id"]; source = self.paths.temporary / "imports" / source_id
        try:
            clip_metadata = json.loads((self.paths.outputs / clip_id / "metadata.json").read_text()); request = clip_metadata["request"]; duration = request["frames"] / request["fps"]
            if not source.is_file() or source.is_symlink() or source.stat().st_size > 64 * 1024 * 1024: raise ValueError()
            paths = allocate(self.paths.outputs); wav = paths.partial_dir / "narration.wav"; shutil.copyfile(source, wav); facts = pad_or_reject_wav(wav, duration)
            replace_audio(self.paths.outputs / clip_id / "video.mp4", wav, paths.partial_dir / "video.mp4", duration); wav.unlink()
        except (OSError, KeyError, TypeError, ValueError, NarrationError, json.JSONDecodeError) as error:
            if 'paths' in locals() and paths.partial_dir.exists(): shutil.rmtree(paths.partial_dir)
            raise GenerationError("selected narration WAV is invalid or does not fit the scene") from error
        request = {"capability": Capability.NARRATION, "source_output_id": clip_id, "source_import_id": source_id, "width": request["width"], "height": request["height"], "frames": request["frames"], "fps": request["fps"], "seed": 0, "steps": 0, "guidance_scale": 0.0, "recipe": "Imported WAV"}
        self._write_metadata(paths, request, {"media_file": "video.mp4", "media_type": "video/mp4", **facts}, None)
        final = promote(paths); self._index_output(final / "metadata.json")
        replacement = dict(payload); replacement["output_id"] = paths.output_id; replacement["step"] = "narration"
        return self.record_story_artifact(replacement)

    def import_story_clip(self, payload: dict) -> dict:
        source_id = _required(payload, "source_clip_id", str); story_id = _required(payload, "story_id", str); scene_id = _required(payload, "scene_id", str); expected = _required(payload, "expected_revision", int)
        if not _SOURCE_IMAGE_ID.fullmatch(source_id): raise GenerationError("selected clip is invalid")
        story = self.stories.get(story_id)
        if story["revision"] != expected: raise GenerationError("story changed in another window; reload before replacing clip")
        scene = next((item for item in story["scenes"] if item["scene_id"] == scene_id), None)
        if not scene or not isinstance(scene["artifacts"].get("clip"), dict): raise GenerationError("a current scene clip is required before replacing it")
        baseline = scene["artifacts"]["clip"]["output_id"]; source = self.paths.temporary / "imports" / source_id
        try:
            metadata = json.loads((self.paths.outputs / baseline / "metadata.json").read_text()); request = metadata["request"]; width, height, frames, fps = (request[key] for key in ("width", "height", "frames", "fps")); duration = frames / fps
            if not source.is_file() or source.is_symlink() or source.stat().st_size > 512 * 1024 * 1024: raise ValueError()
            import imageio_ffmpeg
            paths = allocate(self.paths.outputs); ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            subprocess.run([ffmpeg, "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a?", "-t", f"{duration:.6f}", "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2", "-r", str(fps), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(paths.partial_dir / "video.mp4")], check=True, capture_output=True, text=True)
            if not (paths.partial_dir / "video.mp4").is_file(): raise ValueError()
        except (ImportError, OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            if 'paths' in locals() and paths.partial_dir.exists(): shutil.rmtree(paths.partial_dir)
            raise GenerationError("selected video clip could not be normalized") from error
        request = {"capability": Capability.VIDEO_GENERATION, "source_output_id": baseline, "source_import_id": source_id, "width": width, "height": height, "frames": frames, "fps": fps, "seed": 0, "steps": 0, "guidance_scale": 0.0, "recipe": "Imported clip normalization"}
        self._write_metadata(paths, request, {"media_file": "video.mp4", "media_type": "video/mp4"}, None); final = promote(paths); self._index_output(final / "metadata.json")
        replacement = dict(payload); replacement["output_id"] = paths.output_id; replacement["step"] = "clip"
        return self.record_story_artifact(replacement)

    def export_story_project(self, payload: dict) -> dict:
        story_id = _required(payload, "story_id", str)
        self_contained = payload.get("self_contained", False)
        if not isinstance(self_contained, bool): raise GenerationError("project export option is invalid")
        archive = self.stories.export_project(story_id, self_contained=self_contained)
        return {"archive_name": archive.name, "self_contained": self_contained}

    def import_story_project(self, payload: dict) -> dict:
        source_id = _required(payload, "source_project_id", str)
        if not re.fullmatch(r"storyproj-[a-z0-9-]{8,128}", source_id):
            raise GenerationError("selected story project is invalid")
        source = self.paths.temporary / "imports" / source_id
        if not source.is_file() or source.is_symlink() or source.stat().st_size > 2 * 1024 * 1024 * 1024:
            raise GenerationError("selected story project is unavailable")
        try:
            story = self.stories.import_project(source)
            # Self-contained archives adopt immutable output directories too.
            # Index their existing sidecars so Library and lineage recovery see
            # the same selections after restart, without rewriting media.
            for output_id in self.stories._artifact_ids(story):
                metadata = self.paths.outputs / output_id / "metadata.json"
                if metadata.is_file() and not metadata.is_symlink():
                    self._index_output(metadata)
            return story
        except Exception as error:
            if isinstance(error, GenerationError): raise
            raise GenerationError("selected story project could not be imported") from error

    def submit_story_render(self, payload: dict, on_progress: Callable[[Job], None], on_terminal: Callable[[Job, dict | None], None]) -> Job:
        """Run Story Mode as one cancellable job, checkpointing each scene step.

        The renderer intentionally calls providers directly inside this single
        controller job: submitting child jobs would violate the product's
        single-active-job contract and make a crash indistinguishable from a
        completed scene checkpoint.
        """
        story_id = _required(payload, "story_id", str)
        expected = _required(payload, "expected_revision", int)
        through = payload.get("through", "clip")
        if through not in {"still", "clip", "narration", "subtitles"}:
            raise GenerationError("story render phase is unavailable")
        scene_ids_raw = payload.get("scene_ids")
        if scene_ids_raw is not None and (not isinstance(scene_ids_raw, list) or len(scene_ids_raw) > 64 or not all(isinstance(item, str) for item in scene_ids_raw)):
            raise GenerationError("selected story scenes are invalid")
        scene_ids = set(scene_ids_raw) if scene_ids_raw is not None else None
        image_provider = next((item for item in self._providers.values() if Capability.IMAGE_GENERATION in item.facts.capabilities), None)
        video_provider = next((item for item in self._providers.values() if Capability.VIDEO_GENERATION in item.facts.capabilities), None)
        if image_provider is None or video_provider is None:
            raise GenerationError("Story Mode requires one validated image and video model")
        try:
            image_profile = image_provider.measured_profile()
            video_profile = video_provider.measured_recipes().recipes["Balanced"]
        except (AttributeError, KeyError, OSError, RuntimeError, ValueError) as error:
            raise GenerationError("Story Mode requires measured image and video recipes") from error
        job_ready = __import__("threading").Event(); job: Job | None = None

        def save(provider: Provider, request_values: dict, result: dict[str, object]) -> str:
            paths = allocate(self.paths.outputs)
            try:
                request = OperationRequest(operation_id=paths.output_id, output_dir=paths.partial_dir, **request_values)
                actual = provider.run(request, lambda _fraction, _text: None, lambda: job.cancel_requested if job else True)
                media_file = actual.get("media_file")
                if not isinstance(media_file, str) or not resolve_owned_file(self.paths.outputs / ".partial", paths.output_id, media_file).is_file():
                    raise GenerationError("story provider did not produce valid media")
                self._write_metadata(paths, request_values, actual, provider)
                final = promote(paths); self._index_output(final / "metadata.json")
                return paths.output_id
            except BaseException:
                if paths.partial_dir.exists(): shutil.rmtree(paths.partial_dir)
                raise

        def runner(_progress, cancelled) -> None:
            job_ready.wait(); assert job is not None
            def make_still(scene: dict) -> str:
                # Measured: flux-schnell's ~34 GiB peak MPS allocation plus
                # LTX's ~30 GiB peak RSS sums past this Mac's 48 GiB unified
                # memory. Holding both resident (as the one-shot generate/edit
                # path already avoids via its own "unload other providers"
                # step) pushed real physical footprint to 51+ GiB during a
                # real render, causing severe system-wide memory pressure
                # that stalled unrelated IPC requests for minutes.
                if video_provider.facts.provider_id != image_provider.facts.provider_id:
                    video_provider.unload()
                return save(image_provider, {"capability": Capability.IMAGE_GENERATION, "prompt": scene["prompt"], "seed": 0, "width": image_profile.width, "height": image_profile.height, "frames": 1, "fps": 1, "steps": image_profile.steps, "guidance_scale": image_profile.guidance_scale, "recipe": "Measured"}, {})
            def make_clip(scene: dict, still_id: str) -> str:
                source = self.paths.outputs / still_id / "image.png"
                if not source.is_file():
                    raise GenerationError("current story still is unavailable")
                if image_provider.facts.provider_id != video_provider.facts.provider_id:
                    image_provider.unload()
                return save(video_provider, {"capability": Capability.VIDEO_GENERATION, "prompt": scene["prompt"], "seed": 0, "width": video_profile.width, "height": video_profile.height, "frames": video_profile.frames, "fps": video_profile.fps, "steps": video_profile.steps, "guidance_scale": video_profile.guidance_scale, "recipe": "Balanced", "source_image": source, "source_output_id": still_id}, {})
            def make_narration(scene: dict, clip_id: str) -> str:
                if self.narrator is None:
                    raise GenerationError("story narration is not available")
                try:
                    metadata = json.loads((self.paths.outputs / clip_id / "metadata.json").read_text())
                    media_file = metadata["result"]["media_file"]
                    source = resolve_owned_file(self.paths.outputs, clip_id, media_file)
                    if not source.is_file():
                        raise ValueError()
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    raise GenerationError("current story clip is unavailable") from error
                paths = allocate(self.paths.outputs)
                try:
                    wav = paths.partial_dir / "narration.wav"
                    cues = synthesize_segmented(self.narrator, scene["narration"], wav, cancelled)
                    facts = pad_or_reject_wav(wav, video_profile.frames / video_profile.fps)
                    replace_audio(source, wav, paths.partial_dir / "video.mp4", video_profile.frames / video_profile.fps)
                    wav.unlink(missing_ok=True)
                    request = {"capability": Capability.NARRATION, "text": scene["narration"], "source_output_id": clip_id, "width": video_profile.width, "height": video_profile.height, "frames": video_profile.frames, "fps": video_profile.fps, "seed": 0, "steps": 0, "guidance_scale": 0.0, "recipe": "Story narration"}
                    self._write_metadata(paths, request, {"media_file": "video.mp4", "media_type": "video/mp4", "subtitle_cues": cues, **facts}, None)
                    final = promote(paths); self._index_output(final / "metadata.json")
                    return paths.output_id
                except BaseException:
                    if paths.partial_dir.exists(): shutil.rmtree(paths.partial_dir)
                    raise
                finally:
                    self.narrator.unload()
            def make_subtitles(scene: dict, narration_id: str) -> str:
                try:
                    metadata = json.loads((self.paths.outputs / narration_id / "metadata.json").read_text()); cues = metadata["result"]["subtitle_cues"]
                    if not isinstance(cues, list): raise ValueError()
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error: raise GenerationError("story narration timing is unavailable") from error
                paths = allocate(self.paths.outputs)
                write_srt(paths.partial_dir / "subtitles.srt", cues)
                request = {"capability": "story_subtitles", "source_output_id": narration_id, "width": 0, "height": 0, "frames": 0, "fps": 0, "seed": 0, "steps": 0, "guidance_scale": 0.0, "recipe": "Sentence timing"}
                self._write_metadata(paths, request, {"media_file": "subtitles.srt", "media_type": "application/x-subrip"}, None)
                final = promote(paths); self._index_output(final / "metadata.json"); return paths.output_id
            renderer = StoryRenderer(self.stories, make_still, make_clip, make_narration, make_subtitles)
            renderer.render(story_id, expected, scene_ids=scene_ids, through=through, cancelled=cancelled, progress=lambda fraction, text: self._on_progress(job, fraction, text, on_progress))
        job = self.jobs.submit(runner); job_ready.set(); self._watch_terminal(job, on_terminal)
        return job

    def submit_story_compose(self, payload: dict, on_progress: Callable[[Job], None], on_terminal: Callable[[Job, dict | None], None]) -> Job:
        story_id = _required(payload, "story_id", str); expected = _required(payload, "expected_revision", int)
        story = self.stories.get(story_id)
        if story["revision"] != expected: raise GenerationError("story changed in another window; reload before composing")
        facts = self._story_composition_facts(story)
        ready = __import__("threading").Event(); job: Job | None = None; output_paths: OutputPaths | None = None
        def runner(_progress, cancelled) -> None:
            nonlocal output_paths
            ready.wait(); assert job is not None
            try:
                self._on_progress(job, 0.05, "Validating story scenes", on_progress)
                output_paths = allocate(self.paths.outputs)
                contributors = compose_hard_cuts(story, self.paths.outputs, output_paths.partial_dir / "video.mp4", target_facts=facts, cancelled=cancelled)
                self._on_progress(job, 0.9, "Saving immutable story movie", on_progress)
                request = {"capability": "story_composition", "story_id": story_id, "story_revision": expected, "width": facts["width"], "height": facts["height"], "frames": facts["frames"], "fps": facts["fps"], "seed": 0, "steps": 0, "guidance_scale": 0.0, "recipe": "Hard cuts"}
                self._write_metadata(output_paths, request, {"media_file": "video.mp4", "media_type": "video/mp4", "expected_duration_seconds": facts["frames"] / facts["fps"]}, None)
                metadata_path = output_paths.partial_dir / "metadata.json"; metadata = json.loads(metadata_path.read_text()); metadata["lineage"] = [{"output_id": item, "relation": "story_scene"} for item in contributors]; metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")))
                final = promote(output_paths); self._index_output(final / "metadata.json")
                saved = self.stories.record_composition({"story_id": story_id, "expected_revision": expected, "output_id": output_paths.output_id})
                self._job_outputs[job.job_id] = output_paths.output_id
                self._on_progress(job, 1.0, f"Story revision {saved['revision']} composed", on_progress)
            except BaseException:
                if output_paths and output_paths.partial_dir.exists(): shutil.rmtree(output_paths.partial_dir)
                raise
        job = self.jobs.submit(runner); ready.set(); self._watch_terminal(job, on_terminal); return job

    def _story_composition_facts(self, story: dict) -> dict[str, int]:
        """Require the current approved selections to share measured media facts."""
        expected: tuple[int, int, int] | None = None
        total_frames = 0
        for scene in story.get("scenes", []):
            if not scene.get("approved"):
                raise GenerationError("all story scenes must be approved before composition")
            artifact = scene.get("artifacts", {}).get("narration") or scene.get("artifacts", {}).get("clip")
            if not isinstance(artifact, dict) or not isinstance(artifact.get("output_id"), str):
                raise GenerationError("every approved scene needs a current clip or narration")
            try:
                metadata = json.loads((self.paths.outputs / artifact["output_id"] / "metadata.json").read_text())
                request = metadata["request"]; values = tuple(request[key] for key in ("width", "height", "fps"))
                if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
                    raise ValueError()
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise GenerationError("current story scene media facts are unavailable") from error
            if expected is None: expected = values
            elif values != expected: raise GenerationError("approved story scenes have mismatched media facts")
            start = float(scene.get("shot", {}).get("trim_start_seconds", 0.0)); end = float(scene.get("shot", {}).get("trim_end_seconds", 0.0))
            frames = request.get("frames")
            if isinstance(frames, bool) or not isinstance(frames, int) or frames <= 0: raise GenerationError("current story scene duration is unavailable")
            clip_frames = frames - round(start * values[2]) if not end else round((end - start) * values[2])
            if clip_frames <= 0: raise GenerationError("story trim exceeds the current scene duration")
            total_frames += clip_frames
        if expected is None: raise GenerationError("a story needs at least one approved scene")
        return {"width": expected[0], "height": expected[1], "fps": expected[2], "frames": total_frames}

    def library_payload(self) -> list[dict]:
        """Return bounded metadata only; media paths never cross IPC."""
        connection = self.store.open()
        try:
            rows = connection.execute("SELECT output_id, metadata_json FROM outputs ORDER BY rowid DESC LIMIT 100").fetchall()
        finally:
            connection.close()
        library = []
        for output_id, raw in rows:
            try:
                metadata = json.loads(raw)
                request = metadata.get("request", {})
                result = metadata.get("result", {})
                if not isinstance(request, dict) or not isinstance(result, dict):
                    continue
                library.append({
                    "output_id": output_id,
                    "created_at": metadata.get("created_at"),
                    "prompt": request.get("prompt", ""),
                    "seed": request.get("seed"),
                    "media_file": result.get("media_file"),
                })
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return library

    def recovery_preview(self) -> dict:
        partial_root = self.paths.outputs / ".partial"
        partials = [path for path in partial_root.iterdir() if path.is_dir()] if partial_root.is_dir() else []
        return {"partial_output_count": len(partials), "reserved_bytes": self.reservations.reserved_bytes}

    def recover(self) -> dict:
        preview = self.recovery_preview()
        partial_root = self.paths.outputs / ".partial"
        for path in partial_root.iterdir() if partial_root.is_dir() else []:
            if path.is_dir():
                shutil.rmtree(path)
        self.reservations.recover_after_interruption()
        return {**preview, "recovered": True}

    def cancel(self, job_id: str) -> Job:
        return self.jobs.cancel(job_id)

    @staticmethod
    def _job_payload(job: Job) -> dict:
        return {
            "job_id": job.job_id, "operation": job.operation, "state": job.state.value, "progress": job.progress,
            "status_text": job.status_text, "error": job.error, "created_at": job.created_at,
            "finished_at": job.finished_at,
        }
