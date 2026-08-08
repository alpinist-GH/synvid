"""Long-lived SynVid worker entry point; stdout is protocol messages only."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import threading

from .protocol import Envelope, ProtocolError, negotiate_version, parse_envelope, validate_request
from .paths import AppPaths
from .providers.ltx import LtxProvider
from .providers.flux import FluxSchnellProvider
from .resources import Estimate
from .service import GenerationError, GenerationService
from .jobs import BusyError
from .narration import KokoroNarrator, NarrationError


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
    estimate = _measured_estimate(paths.models / "ltx-video" / "measured-profile.json")
    flux_estimate = _measured_estimate(paths.models / "flux-schnell" / "measured-profile.json")
    return GenerationService(
        paths,
        ltx,
        estimate,
        additional_providers=(flux,),
        estimates={"flux-schnell": flux_estimate},
        narrator=KokoroNarrator(paths.models / "kokoro-onnx" / "snapshot"),
    )


def _measured_estimate(profile: Path) -> Estimate:
    try:
        import json
        measured = json.loads(profile.read_text())
        recipes = measured.get("recipes", {"Balanced": measured})
        balanced = recipes.get("Balanced", {}) if isinstance(recipes, dict) else {}
        disk_bytes = balanced.get("estimated_disk_bytes") if isinstance(balanced, dict) else None
        estimate = Estimate(disk_bytes, isinstance(disk_bytes, int) and disk_bytes > 0)
    except (OSError, ValueError, json.JSONDecodeError):
        estimate = Estimate(None, False)
    # This estimate remains deliberately unavailable until smoke_test writes a
    # real measurement.  Generation is rejected rather than guessing disk use.
    return estimate


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
            elif request.kind == "list_outputs":
                _reply(request, "status", {"outputs": service.library_payload()})
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
        except (ProtocolError, GenerationError, NarrationError, KeyError, TypeError) as error:
            # A malformed request cannot be trusted to contain a valid ID.
            _reply(request or Envelope(1, "protocol-error", "error", {}), "error", {"code": "invalid_request", "message": str(error)})
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
