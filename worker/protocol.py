"""Versioned, bounded JSON-lines protocol shared by Rust and Python."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Mapping

PROTOCOL_MIN_VERSION = 1
PROTOCOL_MAX_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024
MAX_REQUEST_ID_LENGTH = 128
MAX_KIND_LENGTH = 64
MAX_PAYLOAD_DEPTH = 8
MAX_PAYLOAD_ITEMS = 128

# This is deliberately a protocol allow-list, not a command dispatcher.  It
# keeps a malformed or newer webview from smuggling a generic operation into
# the worker before Stage 1 wires the real operations.
REQUEST_KINDS = frozenset({"hello", "get_status", "model_catalog", "download_model", "remove_model", "clean_temporary", "list_outputs", "delete_output", "recovery_preview", "recover", "generate", "edit_video", "edit_image", "narrate", "export_video", "story_create", "story_list", "story_get", "story_update", "story_add_scene", "story_update_scene", "story_reorder_scenes", "story_draft_scenes", "story_record_artifact", "story_import_still", "story_import_subtitles", "story_import_narration", "story_import_clip", "story_export_project", "story_import_project", "render_story", "compose_story", "cancel", "unload_model"})
# A generate request is acknowledged immediately.  Its progress and exactly one
# terminal event use the same request ID, so the Rust supervisor can route a
# complete job stream without inventing another unbounded channel.
EVENT_KINDS = frozenset({"hello_ack", "status", "accepted", "progress", "terminal", "error"})


class ProtocolError(ValueError):
    """A malformed or incompatible worker message."""


@dataclass(frozen=True)
class Envelope:
    version: int
    request_id: str
    kind: str
    payload: Mapping[str, Any]

    def to_json_line(self) -> str:
        encoded = json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ProtocolError("message exceeds protocol size limit")
        return encoded


def negotiate_version(peer_min: int, peer_max: int) -> int:
    lower = max(PROTOCOL_MIN_VERSION, peer_min)
    upper = min(PROTOCOL_MAX_VERSION, peer_max)
    if lower > upper:
        raise ProtocolError("no compatible protocol version")
    return upper


def parse_envelope(line: str) -> Envelope:
    if len(line.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message exceeds protocol size limit")
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as error:
        raise ProtocolError("invalid JSON") from error
    if not isinstance(raw, dict):
        raise ProtocolError("message must be an object")
    version = raw.get("version")
    request_id = raw.get("request_id")
    kind = raw.get("kind")
    payload = raw.get("payload")
    if not isinstance(version, int) or not PROTOCOL_MIN_VERSION <= version <= PROTOCOL_MAX_VERSION:
        raise ProtocolError("unsupported protocol version")
    if not isinstance(request_id, str) or not request_id or len(request_id) > MAX_REQUEST_ID_LENGTH:
        raise ProtocolError("invalid request_id")
    if not isinstance(kind, str) or not kind or len(kind) > MAX_KIND_LENGTH:
        raise ProtocolError("invalid kind")
    if not isinstance(payload, dict) or not _is_bounded_json(payload):
        raise ProtocolError("payload must be an object")
    return Envelope(version=version, request_id=request_id, kind=kind, payload=payload)


def validate_request(envelope: Envelope) -> None:
    if envelope.kind not in REQUEST_KINDS:
        raise ProtocolError("unsupported request kind")


def _is_bounded_json(value: Any, depth: int = 0) -> bool:
    if depth > MAX_PAYLOAD_DEPTH:
        return False
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, list):
        return len(value) <= MAX_PAYLOAD_ITEMS and all(_is_bounded_json(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return (
            len(value) <= MAX_PAYLOAD_ITEMS
            and all(isinstance(key, str) and _is_bounded_json(item, depth + 1) for key, item in value.items())
        )
    return False
