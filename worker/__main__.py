"""Long-lived SynVid worker entry point; stdout is protocol messages only."""

from __future__ import annotations

import fcntl
import os
import sys

# Torch/multiprocessing can spawn a `resource_tracker` helper process the
# first time a semaphore-backed primitive is touched (e.g. during model
# loading). On POSIX that helper inherits the parent's file descriptors,
# including fd 1 - the same pipe Rust reads worker protocol replies from - and
# fd 2, which Rust drains for diagnostics and to detect process exit. If those
# fds still point at the real pipes when the helper forks, Rust (or any
# harness) never sees EOF after this process exits: the helper keeps the
# write ends open as an orphan. Move both to private fds and repoint fd 1/2 at
# /dev/null before any provider/torch import can trigger that fork, so
# children only ever inherit the harmless devnull descriptors.
_protocol_stdout = os.fdopen(os.dup(1), "w", buffering=1)
_protocol_stderr = os.fdopen(os.dup(2), "w", buffering=1)
# Belt-and-suspenders: even a fork()-based multiprocessing child (which
# copies the whole fd table, unlike the posix_spawn-based resource_tracker
# launch) cannot carry a close-on-exec descriptor past its own exec().
for _fd in (_protocol_stdout.fileno(), _protocol_stderr.fileno()):
    _flags = fcntl.fcntl(_fd, fcntl.F_GETFD)
    fcntl.fcntl(_fd, fcntl.F_SETFD, _flags | fcntl.FD_CLOEXEC)
with open(os.devnull, "wb") as _devnull:
    os.dup2(_devnull.fileno(), 1)
    os.dup2(_devnull.fileno(), 2)
sys.stdout = _protocol_stdout
sys.stderr = _protocol_stderr
# fd 0 (the request pipe Rust writes into) needs the same protection: measured
# with `lsof`, the resource_tracker helper inherits fd 0 across its
# posix_spawn just like it used to inherit fd 1/2. With two processes holding
# the pipe's read end open, the kernel can deliver a request line to whichever
# one happens to call read() first; if it lands in the helper (which never
# consumes it - its own event loop only reads the tracker fd it was launched
# with) the real request vanishes and this process's next stdin read blocks
# forever. fd 0 does not need dup2'ing to a private number first (nothing
# else writes directly to the raw descriptor the way stdout/stderr do);
# marking it close-on-exec in place is enough since CLOEXEC only affects
# inheritance across exec, not this process's own use of it.
_stdin_flags = fcntl.fcntl(0, fcntl.F_GETFD)
fcntl.fcntl(0, fcntl.F_SETFD, _stdin_flags | fcntl.FD_CLOEXEC)

from pathlib import Path
import threading

from .protocol import Envelope, ProtocolError, negotiate_version, parse_envelope, validate_request
from .paths import AppPaths
from .providers.ltx import LtxProvider
from .providers.flux import FluxSchnellProvider
from .providers.qwen_image_edit import QwenImageEditProvider
from .providers.hunyuan import HunyuanVideo15Provider
from .providers.wan_mlx import WanMlxProvider
from .models import REGISTRY
from .resources import Estimate
from .service import GenerationError, GenerationService
from .jobs import BusyError
from .narration import KokoroNarrator, NarrationError
from .stories import StoryError
from .story_planner import StoryPlannerError
from .story_compose import StoryComposeError
from .store import StoreError


WORKER_VERSION = "0.1.0"


_stdout_lock = threading.Lock()


def _reply(request: Envelope, kind: str, payload: dict) -> None:
    with _stdout_lock:
        print(Envelope(request.version, request.request_id, kind, payload).to_json_line(), flush=True)


def _app_paths() -> AppPaths:
    # Rust supplies this root in production.  The fallback is the documented
    # macOS location, never the current working directory.
    application_support = Path(os.environ.get("SYNVID_APP_SUPPORT", Path.home() / "Library" / "Application Support"))
    return AppPaths.under(application_support)


def _service() -> GenerationService:
    paths = _app_paths()
    ltx = LtxProvider(
        paths.models / "ltx-video" / "snapshot",
        paths.models / "ltx-video" / "measured-profile.json",
    )
    flux = FluxSchnellProvider(
        paths.models / "flux-schnell" / "snapshot",
        paths.models / "flux-schnell" / "measured-profile.json",
    )
    qwen_image_edit = QwenImageEditProvider(
        paths.models / "qwen-image-edit" / "snapshot",
        paths.models / "qwen-image-edit" / "measured-profile.json",
    )
    hunyuan_t2v = HunyuanVideo15Provider(
        paths.models / "hunyuan15-480p-t2v" / "snapshot",
        paths.models / "hunyuan15-480p-t2v" / "measured-profile.json",
        model_id="hunyuan15-480p-t2v",
    )
    hunyuan_i2v = HunyuanVideo15Provider(
        paths.models / "hunyuan15-480p-i2v" / "snapshot",
        paths.models / "hunyuan15-480p-i2v" / "measured-profile.json",
        model_id="hunyuan15-480p-i2v",
    )
    wan_mlx = WanMlxProvider(
        paths.models / "wan2.2-ti2v-5b-mlx" / "snapshot",
        paths.models / "wan2.2-ti2v-5b-mlx" / "measured-profile.json",
    )
    estimate = _measured_estimate(paths.models / "ltx-video" / "measured-profile.json")
    flux_estimate = _measured_estimate(paths.models / "flux-schnell" / "measured-profile.json")
    qwen_image_edit_estimate = _measured_estimate(paths.models / "qwen-image-edit" / "measured-profile.json")
    hunyuan_t2v_estimate = _hunyuan_estimate(paths.models / "hunyuan15-480p-t2v" / "measured-profile.json", "hunyuan15-480p-t2v")
    hunyuan_i2v_estimate = _hunyuan_estimate(paths.models / "hunyuan15-480p-i2v" / "measured-profile.json", "hunyuan15-480p-i2v")
    wan_mlx_estimate = _measured_estimate(paths.models / "wan2.2-ti2v-5b-mlx" / "measured-profile.json")
    return GenerationService(
        paths,
        ltx,
        estimate,
        additional_providers=(flux, qwen_image_edit, hunyuan_t2v, hunyuan_i2v, wan_mlx),
        estimates={
            "flux-schnell": flux_estimate,
            "qwen-image-edit": qwen_image_edit_estimate,
            "hunyuan15-480p-t2v": hunyuan_t2v_estimate,
            "hunyuan15-480p-i2v": hunyuan_i2v_estimate,
            "wan2.2-ti2v-5b-mlx": wan_mlx_estimate,
        },
        narrator=KokoroNarrator(paths.models / "kokoro-onnx" / "snapshot"),
    )


def _measured_estimate(profile: Path) -> Estimate:
    try:
        import json
        measured = json.loads(profile.read_text())
        recipes = measured.get("recipes", {"Balanced": measured})
        selected = recipes.get("Balanced") if isinstance(recipes, dict) else None
        if not isinstance(selected, dict) and isinstance(recipes, dict):
            selected = next((value for value in recipes.values() if isinstance(value, dict)), None)
        disk_bytes = selected.get("estimated_disk_bytes") if isinstance(selected, dict) else None
        estimate = Estimate(disk_bytes, isinstance(disk_bytes, int) and disk_bytes > 0)
    except (OSError, ValueError, json.JSONDecodeError):
        estimate = Estimate(None, False)
    # This estimate remains deliberately unavailable until smoke_test writes a
    # real measurement.  Generation is rejected rather than guessing disk use.
    return estimate


def _hunyuan_estimate(profile: Path, model_id: str) -> Estimate:
    estimate = _measured_estimate(profile)
    if estimate.is_measured:
        return estimate
    # The checkpoint size is known from the pinned Hugging Face snapshot. The
    # generation memory estimate remains intentionally unmeasured.
    return Estimate(int(REGISTRY[model_id].expected_size_gib * 1024**3), True)


def serve() -> int:
    service = _service()
    for line in sys.stdin:
        request: Envelope | None = None
        try:
            request = parse_envelope(line)
            validate_request(request)
            if request.kind == "hello":
                version = negotiate_version(
                    request.payload.get("protocol_min", 0), request.payload.get("protocol_max", 0)
                )
                _reply(request, "hello_ack", {"protocol_version": version, "worker_version": WORKER_VERSION, "features": []})
            elif request.kind == "get_status":
                _reply(request, "status", service.status_payload())
            elif request.kind == "model_catalog":
                _reply(request, "status", service.model_catalog())
            elif request.kind == "download_model":
                model_id = request.payload.get("model_id")
                if not isinstance(model_id, str): raise ProtocolError("download_model requires a model ID")
                def progress(job): _reply(request, "progress", service._job_payload(job))
                def terminal(job, output):
                    payload = service._job_payload(job)
                    if output: payload.update(output)
                    _reply(request, "terminal", payload)
                job = service.submit_model_download(model_id, progress, terminal)
                _reply(request, "accepted", {"job_id": job.job_id})
            elif request.kind == "calibrate_model":
                model_id = request.payload.get("model_id")
                recipe = request.payload.get("recipe")
                if not isinstance(model_id, str) or not isinstance(recipe, str):
                    raise ProtocolError("calibrate_model requires a model ID and recipe name")
                def progress(job): _reply(request, "progress", service._job_payload(job))
                def terminal(job, output):
                    payload = service._job_payload(job)
                    if output: payload.update(output)
                    _reply(request, "terminal", payload)
                job = service.submit_calibration(model_id, recipe, progress, terminal)
                _reply(request, "accepted", {"job_id": job.job_id})
            elif request.kind == "remove_model":
                _reply(request, "status", service.remove_model(request.payload.get("model_id")))
            elif request.kind == "clean_temporary":
                _reply(request, "status", service.clean_temporary())
            elif request.kind == "list_outputs":
                _reply(request, "status", {"outputs": service.library_payload()})
            elif request.kind == "delete_output":
                output_id = request.payload.get("output_id")
                if not isinstance(output_id, str): raise ProtocolError("delete_output requires an output ID")
                cascade = request.payload.get("cascade", False)
                if not isinstance(cascade, bool): raise ProtocolError("delete_output cascade must be a boolean")
                _reply(request, "status", service.delete_output(output_id, cascade=cascade))
            elif request.kind == "recovery_preview":
                _reply(request, "status", service.recovery_preview())
            elif request.kind == "recover":
                _reply(request, "status", service.recover())
            elif request.kind == "generate":
                def progress(job):
                    _reply(request, "progress", service._job_payload(job))

                def terminal(job, output):
                    payload = service._job_payload(job)
                    if output:
                        payload.update(output)
                    _reply(request, "terminal", payload)

                job = service.submit(request.payload, progress, terminal)
                _reply(request, "accepted", {"job_id": job.job_id})
            elif request.kind == "edit_video":
                def progress(job):
                    _reply(request, "progress", service._job_payload(job))

                def terminal(job, output):
                    payload = service._job_payload(job)
                    if output:
                        payload.update(output)
                    _reply(request, "terminal", payload)

                job = service.submit_video_edit(request.payload, progress, terminal)
                _reply(request, "accepted", {"job_id": job.job_id})
            elif request.kind == "edit_image":
                def progress(job):
                    _reply(request, "progress", service._job_payload(job))

                def terminal(job, output):
                    payload = service._job_payload(job)
                    if output:
                        payload.update(output)
                    _reply(request, "terminal", payload)

                job = service.submit_image_edit(request.payload, progress, terminal)
                _reply(request, "accepted", {"job_id": job.job_id})
            elif request.kind == "narrate":
                def progress(job):
                    _reply(request, "progress", service._job_payload(job))

                def terminal(job, output):
                    payload = service._job_payload(job)
                    if output:
                        payload.update(output)
                    _reply(request, "terminal", payload)

                job = service.submit_narration(request.payload, progress, terminal)
                _reply(request, "accepted", {"job_id": job.job_id})
            elif request.kind == "export_video":
                output_id = request.payload.get("output_id")
                profile = request.payload.get("profile")
                if not isinstance(output_id, str) or not isinstance(profile, str):
                    raise ProtocolError("export_video requires an output ID and profile")
                _reply(request, "status", service.export(output_id, profile))
            elif request.kind == "story_create":
                _reply(request, "status", service.create_story(request.payload))
            elif request.kind == "story_list":
                _reply(request, "status", {"stories": service.list_stories()})
            elif request.kind == "story_get":
                story_id = request.payload.get("story_id")
                if not isinstance(story_id, str): raise ProtocolError("story_get requires a story ID")
                _reply(request, "status", service.get_story(story_id))
            elif request.kind == "story_delete":
                _reply(request, "status", service.delete_story(request.payload))
            elif request.kind == "story_update":
                _reply(request, "status", service.update_story(request.payload))
            elif request.kind == "story_add_scene":
                _reply(request, "status", service.add_story_scene(request.payload))
            elif request.kind == "story_update_scene":
                _reply(request, "status", service.update_story_scene(request.payload))
            elif request.kind == "story_reorder_scenes":
                _reply(request, "status", service.reorder_story_scenes(request.payload))
            elif request.kind == "story_draft_scenes":
                def progress(job): _reply(request, "progress", service._job_payload(job))
                def terminal(job, output):
                    payload = service._job_payload(job)
                    if output: payload.update(output)
                    _reply(request, "terminal", payload)
                job = service.submit_story_draft(request.payload, progress, terminal)
                _reply(request, "accepted", {"job_id": job.job_id})
            elif request.kind == "story_record_artifact":
                _reply(request, "status", service.record_story_artifact(request.payload))
            elif request.kind == "story_import_still":
                _reply(request, "status", service.import_story_still(request.payload))
            elif request.kind == "story_import_subtitles":
                _reply(request, "status", service.import_story_subtitles(request.payload))
            elif request.kind == "story_import_narration":
                _reply(request, "status", service.import_story_narration(request.payload))
            elif request.kind == "story_import_clip":
                _reply(request, "status", service.import_story_clip(request.payload))
            elif request.kind == "story_export_project":
                _reply(request, "status", service.export_story_project(request.payload))
            elif request.kind == "story_import_project":
                _reply(request, "status", service.import_story_project(request.payload))
            elif request.kind == "render_story":
                def progress(job):
                    _reply(request, "progress", service._job_payload(job))

                def terminal(job, output):
                    payload = service._job_payload(job)
                    if output:
                        payload.update(output)
                    _reply(request, "terminal", payload)

                job = service.submit_story_render(request.payload, progress, terminal)
                _reply(request, "accepted", {"job_id": job.job_id})
            elif request.kind == "compose_story":
                def progress(job): _reply(request, "progress", service._job_payload(job))
                def terminal(job, output):
                    payload = service._job_payload(job)
                    if output: payload.update(output)
                    _reply(request, "terminal", payload)
                job = service.submit_story_compose(request.payload, progress, terminal)
                _reply(request, "accepted", {"job_id": job.job_id})
            elif request.kind == "cancel":
                job_id = request.payload.get("job_id")
                if not isinstance(job_id, str):
                    raise ProtocolError("cancel requires job_id")
                _reply(request, "status", service._job_payload(service.cancel(job_id)))
            elif request.kind == "unload_model":
                if service.jobs.current() is not None:
                    raise BusyError(service.jobs.current().job_id)
                service.provider.unload()
                _reply(request, "status", {"active_job": None, "model_loaded": False})
            else:
                _reply(request, "error", {"code": "unsupported_request", "message": "request is not available in Stage 0"})
        except BusyError as error:
            _reply(request, "error", {"code": "busy", "message": str(error), "current_job_id": error.current_job_id})
        except (ProtocolError, GenerationError, NarrationError, StoryError, StoryPlannerError, StoryComposeError, KeyError, TypeError) as error:
            # A malformed request cannot be trusted to contain a valid ID.
            _reply(request or Envelope(1, "protocol-error", "error", {}), "error", {"code": "invalid_request", "message": str(error)})
        except StoreError as error:
            # Corrupt or incompatible-newer-schema metadata must be reported
            # back over the protocol, not left to crash the whole worker
            # process out from under Rust mid-session.
            _reply(request or Envelope(1, "protocol-error", "error", {}), "error", {"code": "store_unavailable", "message": str(error)})
        except Exception as error:
            # Last-resort safety net for the synchronous (non-job) request
            # kinds above: a denied-file-access OSError, a permission error
            # walking a directory, or any other unanticipated failure must
            # fail that one request, not take down a worker that may have
            # other in-flight state. Job-based requests already get this
            # guarantee from JobManager._run's own broad catch; this mirrors
            # it for everything dispatched inline in this loop.
            print(f"unexpected error handling {request.kind if request else 'unknown'!r} request: {error!r}", file=sys.stderr)
            _reply(request or Envelope(1, "protocol-error", "error", {}), "error", {"code": "internal_error", "message": "SynVid hit an unexpected local error handling that request."})
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
