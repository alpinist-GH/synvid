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
from .narration import NarrationError, Narrator, pad_or_reject_wav, replace_audio
from .outputs import OutputPaths, allocate, promote, resolve_owned_file
from .paths import AppPaths
from .providers.base import Capability, OperationRequest, Provider
from .resources import Estimate, ReservationBook
from .store import Store


class GenerationError(ValueError):
    pass


_SOURCE_IMAGE_ID = re.compile(r"^[a-z0-9-]{16,128}$")
_OUTPUT_ID = re.compile(r"^[0-9a-f-]{36}$")


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
        self._job_outputs: dict[str, str] = {}
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
        if recipe not in {"Draft", "Balanced", "High"}:
            raise GenerationError("recipe is not available")
        try:
            profile = provider.measured_recipes().recipes[recipe]
        except AttributeError:
            profile = None
        except (KeyError, ValueError, OSError, RuntimeError) as error:
            raise GenerationError("requested recipe is not measured for LTX") from error
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
        if recipe not in {"Draft", "Balanced", "High"}:
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
                    output_id = self._job_outputs.get(job.job_id)
                    output = {"output_id": output_id} if current.state == JobState.SUCCEEDED and output_id else None
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
        for model_id, provider in self._providers.items():
            recipes, image = self._measured_profiles(provider)
            available_models[model_id] = {
                "capabilities": [capability.value for capability in provider.facts.capabilities],
                "measured_recipes": recipes,
                "measured_image_profile": image,
            }
        return {
            "active_job": self._job_payload(current) if current else None,
            "measured_recipes": profiles,
            "measured_image_profile": image_profile,
            "available_models": available_models,
        }

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
                }
                for name, profile in measured.items()
            }
        except (AttributeError, ValueError, OSError, RuntimeError):
            # A missing profile is intentionally surfaced as unavailable rather
            # than guessed by the UI.
            pass
        image_profile = None
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
            "job_id": job.job_id, "state": job.state.value, "progress": job.progress,
            "status_text": job.status_text, "error": job.error, "created_at": job.created_at,
            "finished_at": job.finished_at,
        }
