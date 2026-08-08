"""Versioned, atomic Story Mode project storage with optimistic revisions."""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any
import uuid
import hashlib
import zipfile
import re


STORY_SCHEMA_VERSION = 1
_MAX_TEXT = 4_000
_MAX_SCENES = 64
_ASPECT_RATIOS = frozenset({"16:9", "9:16", "1:1"})
_ARCHIVE_MAX_BYTES = 2 * 1024 * 1024 * 1024
_ARCHIVE_MAX_ENTRIES = 512
_OUTPUT_ID = re.compile(r"^[0-9a-f-]{36}$")
_STEPS = frozenset({"still", "clip", "narration", "subtitles", "segment"})
_DOWNSTREAM = {
    "still": frozenset({"clip", "segment"}),
    "clip": frozenset({"segment"}),
    "narration": frozenset({"subtitles", "segment"}),
    "subtitles": frozenset(),
    "segment": frozenset(),
}


class StoryError(ValueError):
    pass


class StoryConflict(StoryError):
    pass


def _text(value: object, name: str, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise StoryError(f"{name} must be text")
    value = value.strip()
    if (required and not value) or len(value) > _MAX_TEXT:
        raise StoryError(f"{name} must contain {'1 to ' if required else 'at most '}{_MAX_TEXT} characters")
    return value


class StoryStore:
    def __init__(self, root: Path, outputs_root: Path | None = None):
        self.root = root
        self.outputs_root = outputs_root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        story = {
            "schema_version": STORY_SCHEMA_VERSION,
            "story_id": str(uuid.uuid4()),
            "revision": 1,
            "title": _text(payload.get("title"), "title", required=True),
            "premise": _text(payload.get("premise"), "premise"),
            "style_bible": _text(payload.get("style_bible"), "style bible"),
            "aspect_ratio": self._aspect_ratio(payload.get("aspect_ratio", "16:9")),
            # Composition is a story-level immutable output selection.  It is
            # deliberately separate from a scene's artifacts, so a scene edit
            # can make only the final movie stale without discarding history.
            "artifacts": {},
            "scenes": [],
        }
        with self._lock:
            self._write(story)
        return story

    def list(self) -> list[dict[str, object]]:
        stories = []
        with self._lock:
            for path in sorted(self.root.glob("*.json")):
                try:
                    story = self._read_path(path)
                    stories.append({key: story[key] for key in ("story_id", "revision", "title", "aspect_ratio")})
                except StoryError:
                    continue
        return stories

    def get(self, story_id: str) -> dict[str, object]:
        with self._lock:
            return self._read(story_id)

    def update(self, payload: dict[str, object]) -> dict[str, object]:
        story_id, expected = self._identity(payload)
        with self._lock:
            story = self._read(story_id); self._check_revision(story, expected)
            changed = False
            for key, label in (("title", "title"), ("premise", "premise"), ("style_bible", "style bible")):
                if key in payload:
                    value = _text(payload[key], label, required=key == "title")
                    changed = changed or value != story[key]
                    story[key] = value
            if "aspect_ratio" in payload:
                value = self._aspect_ratio(payload["aspect_ratio"])
                changed = changed or value != story["aspect_ratio"]
                story["aspect_ratio"] = value
            if changed:
                self._invalidate_composition(story)
            return self._advance(story)

    def add_scene(self, payload: dict[str, object]) -> dict[str, object]:
        story_id, expected = self._identity(payload)
        with self._lock:
            story = self._read(story_id); self._check_revision(story, expected)
            scenes = story["scenes"]
            if len(scenes) >= _MAX_SCENES:
                raise StoryError(f"a story may contain at most {_MAX_SCENES} scenes")
            scenes.append(self._new_scene(payload))
            self._invalidate_composition(story)
            return self._advance(story)

    def duplicate_scene(self, payload: dict[str, object]) -> dict[str, object]:
        """Duplicate editorial text, never an artifact selection or media."""
        story_id, expected = self._identity(payload)
        scene_id = payload.get("scene_id")
        if not isinstance(scene_id, str):
            raise StoryError("scene ID is invalid")
        with self._lock:
            story = self._read(story_id); self._check_revision(story, expected)
            scenes = story["scenes"]
            if len(scenes) >= _MAX_SCENES:
                raise StoryError(f"a story may contain at most {_MAX_SCENES} scenes")
            source = self._scene(story, scene_id)
            clone = self._new_scene({"prompt": source["prompt"], "narration": source["narration"]})
            scenes.insert(scenes.index(source) + 1, clone)
            self._invalidate_composition(story)
            return self._advance(story)

    def delete_scene(self, payload: dict[str, object]) -> dict[str, object]:
        story_id, expected = self._identity(payload)
        scene_id = payload.get("scene_id")
        if not isinstance(scene_id, str):
            raise StoryError("scene ID is invalid")
        with self._lock:
            story = self._read(story_id); self._check_revision(story, expected)
            scene = self._scene(story, scene_id)
            # Artifacts are immutable outputs and deliberately outlive a story edit.
            story["scenes"].remove(scene)
            self._invalidate_composition(story)
            return self._advance(story)

    def update_scene(self, payload: dict[str, object]) -> dict[str, object]:
        story_id, expected = self._identity(payload)
        scene_id = payload.get("scene_id")
        if not isinstance(scene_id, str):
            raise StoryError("scene ID is invalid")
        with self._lock:
            story = self._read(story_id); self._check_revision(story, expected)
            scene = self._scene(story, scene_id)
            changed = False
            invalidated: set[str] = set()
            for key, label in (("prompt", "scene prompt"), ("narration", "scene narration")):
                if key in payload:
                    value = _text(payload[key], label)
                    if value != scene[key]:
                        scene[key] = value; changed = True
                        # A visual change invalidates its visual descendants;
                        # a narration change cannot throw away a reviewed still/clip.
                        invalidated.update({"still", "clip", "segment"} if key == "prompt" else {"narration", "subtitles", "segment"})
            if "approved" in payload:
                if not isinstance(payload["approved"], bool): raise StoryError("scene approval is invalid")
                scene["approved"] = payload["approved"]; changed = True
            shot = scene.setdefault("shot", {"trim_start_seconds": 0.0, "trim_end_seconds": 0.0, "narration_muted": False, "transition": "hard_cut"})
            for key in ("trim_start_seconds", "trim_end_seconds"):
                if key in payload:
                    value = payload[key]
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0: raise StoryError(f"{key} is invalid")
                    if float(value) != shot[key]: shot[key] = float(value); changed = True; invalidated.add("segment")
            if shot["trim_end_seconds"] and shot["trim_end_seconds"] <= shot["trim_start_seconds"]: raise StoryError("trim end must follow trim start")
            if "narration_muted" in payload:
                if not isinstance(payload["narration_muted"], bool): raise StoryError("narration mute is invalid")
                if payload["narration_muted"] != shot["narration_muted"]: shot["narration_muted"] = payload["narration_muted"]; changed = True; invalidated.add("segment")
            if not changed: raise StoryError("scene update contains no changes")
            scene["revision"] += 1
            for step in invalidated:
                scene["artifacts"].pop(step, None)
            if changed:
                self._invalidate_composition(story)
            return self._advance(story)

    def reorder(self, payload: dict[str, object]) -> dict[str, object]:
        story_id, expected = self._identity(payload)
        order = payload.get("scene_ids")
        if not isinstance(order, list) or not all(isinstance(item, str) for item in order):
            raise StoryError("scene order is invalid")
        with self._lock:
            story = self._read(story_id); self._check_revision(story, expected)
            scenes = story["scenes"]
            if len(order) != len(scenes) or len(set(order)) != len(order) or set(order) != {item["scene_id"] for item in scenes}:
                raise StoryError("scene order must contain each existing scene exactly once")
            indexed = {scene["scene_id"]: scene for scene in scenes}
            story["scenes"] = [indexed[scene_id] for scene_id in order]
            self._invalidate_composition(story)
            return self._advance(story)

    def record_artifact(self, payload: dict[str, object]) -> dict[str, object]:
        """Attach one immutable output to one scene step without changing siblings."""
        story_id, expected = self._identity(payload); scene_id, step, output_id = payload.get("scene_id"), payload.get("step"), payload.get("output_id")
        if not isinstance(scene_id, str) or step not in _STEPS or not isinstance(output_id, str) or not _OUTPUT_ID.fullmatch(output_id): raise StoryError("story artifact is invalid")
        with self._lock:
            story = self._read(story_id); self._check_revision(story, expected)
            scene = self._scene(story, scene_id)
            previous = scene["artifacts"].get(step)
            variants = list(previous.get("variants", [])) if isinstance(previous, dict) else []
            if not any(item.get("output_id") == output_id for item in variants if isinstance(item, dict)):
                variants.append({"output_id": output_id})
            scene["artifacts"][step] = {"output_id": output_id, "variants": variants}
            # Replacing a selected artifact invalidates only its downstream work.
            for dependent in _DOWNSTREAM[step]:
                scene["artifacts"].pop(dependent, None)
            self._invalidate_composition(story)
            scene["revision"] += 1
            return self._advance(story)

    def promote_artifact(self, payload: dict[str, object]) -> dict[str, object]:
        """Select a retained variant without regenerating its immutable output."""
        story_id, expected = self._identity(payload)
        scene_id, step, output_id = payload.get("scene_id"), payload.get("step"), payload.get("output_id")
        if not isinstance(scene_id, str) or step not in _STEPS or not isinstance(output_id, str) or not _OUTPUT_ID.fullmatch(output_id):
            raise StoryError("story artifact is invalid")
        with self._lock:
            story = self._read(story_id); self._check_revision(story, expected)
            scene = self._scene(story, scene_id); artifact = scene["artifacts"].get(step)
            if not isinstance(artifact, dict) or not any(item.get("output_id") == output_id for item in artifact.get("variants", []) if isinstance(item, dict)):
                raise StoryError("story artifact variant is unavailable")
            if artifact["output_id"] == output_id:
                return story
            artifact["output_id"] = output_id
            for dependent in _DOWNSTREAM[step]: scene["artifacts"].pop(dependent, None)
            scene["revision"] += 1
            self._invalidate_composition(story)
            return self._advance(story)

    def record_composition(self, payload: dict[str, object]) -> dict[str, object]:
        """Attach a newly composed immutable movie to this exact revision.

        A composition never replaces the prior movie on disk.  Updating a
        story removes only this *current selection*, leaving its output ID in
        history/lineage for an explicit retention decision.
        """
        story_id, expected = self._identity(payload)
        output_id = payload.get("output_id")
        if not isinstance(output_id, str) or not _OUTPUT_ID.fullmatch(output_id):
            raise StoryError("story composition is invalid")
        with self._lock:
            story = self._read(story_id); self._check_revision(story, expected)
            story["artifacts"]["composition"] = {"output_id": output_id, "story_revision": story["revision"]}
            return self._advance(story)

    def export_project(self, story_id: str, *, self_contained: bool = False) -> Path:
        """Create a checksummed project-only or self-contained archive."""
        with self._lock:
            story = self._read(story_id)
            exports = self.root / "exports"; exports.mkdir(exist_ok=True)
            destination = exports / f"{story_id}.synvidstory"
            temporary = destination.with_suffix(".synvidstory.partial")
            encoded = json.dumps(story, sort_keys=True, separators=(",", ":")).encode()
            media_ids = self._artifact_ids(story) if self_contained else []
            if self_contained and self.outputs_root is None: raise StoryError("self-contained export is unavailable")
            files = {"project.json": hashlib.sha256(encoded).hexdigest()}
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("project.json", encoded)
                for output_id in media_ids:
                    source = self.outputs_root / output_id
                    if not source.is_dir() or source.is_symlink(): raise StoryError("current story media is unavailable")
                    for path in source.rglob("*"):
                        if not path.is_file() or path.is_symlink(): continue
                        name = f"media/{output_id}/{path.relative_to(source).as_posix()}"
                        data = path.read_bytes(); files[name] = hashlib.sha256(data).hexdigest(); archive.writestr(name, data)
                manifest = json.dumps({"schema_version": 1, "project_sha256": files["project.json"], "self_contained": self_contained, "files": files}, sort_keys=True).encode()
                archive.writestr("manifest.json", manifest)
            temporary.replace(destination)
            return destination

    def import_project(self, source: Path) -> dict[str, object]:
        """Safely adopt one project-only archive as a new local project ID."""
        if not source.is_file() or source.is_symlink() or source.stat().st_size > _ARCHIVE_MAX_BYTES:
            raise StoryError("project archive is unavailable or too large")
        try:
            with zipfile.ZipFile(source) as archive:
                entries = archive.infolist()
                if len(entries) > _ARCHIVE_MAX_ENTRIES or {item.filename for item in entries} < {"project.json", "manifest.json"}:
                    raise StoryError("project archive contains unsupported entries")
                if any(item.is_dir() or item.filename.startswith("/") or ".." in Path(item.filename).parts or (item.external_attr >> 16) & 0o170000 == 0o120000 for item in entries):
                    raise StoryError("project archive contains an unsafe entry")
                if sum(item.file_size for item in entries) > _ARCHIVE_MAX_BYTES:
                    raise StoryError("project archive expands beyond the allowed size")
                encoded, manifest_raw = archive.read("project.json"), archive.read("manifest.json")
                manifest = json.loads(manifest_raw)
                files = manifest.get("files") if isinstance(manifest, dict) else None
                if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("project_sha256") != hashlib.sha256(encoded).hexdigest() or not isinstance(files, dict) or files.get("project.json") != hashlib.sha256(encoded).hexdigest() or set(files) != {item.filename for item in entries if item.filename != "manifest.json"}:
                    raise StoryError("project archive checksum is invalid")
                for name, digest in files.items():
                    if not isinstance(digest, str) or hashlib.sha256(archive.read(name)).hexdigest() != digest: raise StoryError("project archive checksum is invalid")
                story = json.loads(encoded)
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as error:
            raise StoryError("project archive is corrupt") from error
        if not isinstance(story, dict): raise StoryError("project archive is invalid")
        self._validate_story(story)
        if manifest.get("self_contained"):
            if self.outputs_root is None: raise StoryError("self-contained import is unavailable")
            expected_ids = set(self._artifact_ids(story))
            imported_ids = {Path(name).parts[1] for name in files if name.startswith("media/") and len(Path(name).parts) >= 3}
            if imported_ids != expected_ids: raise StoryError("self-contained archive media is incomplete")
            with zipfile.ZipFile(source) as archive:
                for output_id in imported_ids:
                    destination = self.outputs_root / output_id
                    if destination.exists(): raise StoryError("self-contained archive conflicts with local media")
                    staging = self.outputs_root / ".partial" / f"import-{uuid.uuid4()}"; staging.mkdir(parents=True)
                    for name in files:
                        if name.startswith(f"media/{output_id}/"):
                            relative = Path(name).relative_to(f"media/{output_id}"); target = staging / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(archive.read(name))
                    staging.replace(destination)
        story["story_id"] = str(uuid.uuid4()); story["revision"] = 1
        with self._lock: self._write(story)
        return story

    @staticmethod
    def _aspect_ratio(value: object) -> str:
        if not isinstance(value, str) or value not in _ASPECT_RATIOS:
            raise StoryError("aspect ratio is unavailable")
        return value

    @staticmethod
    def _identity(payload: dict[str, object]) -> tuple[str, int]:
        story_id, expected = payload.get("story_id"), payload.get("expected_revision")
        try: uuid.UUID(str(story_id))
        except (TypeError, ValueError, AttributeError) as error: raise StoryError("story ID is invalid") from error
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
            raise StoryError("expected revision is invalid")
        return str(story_id), expected

    @staticmethod
    def _check_revision(story: dict[str, object], expected: int) -> None:
        if story["revision"] != expected:
            raise StoryConflict("story changed in another window; reload before saving")

    @staticmethod
    def _scene(story: dict[str, object], scene_id: str) -> dict[str, object]:
        for scene in story["scenes"]:
            if scene["scene_id"] == scene_id: return scene
        raise StoryError("scene is unavailable")

    def _advance(self, story: dict[str, object]) -> dict[str, object]:
        story["revision"] += 1; self._write(story); return story

    def _read(self, story_id: str) -> dict[str, object]:
        return self._read_path(self.root / f"{story_id}.json")

    def _read_path(self, path: Path) -> dict[str, object]:
        try: story = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error: raise StoryError("story is unavailable or corrupt") from error
        self._validate_story(story)
        return story

    def _validate_story(self, story: object) -> None:
        if not isinstance(story, dict) or story.get("schema_version") != STORY_SCHEMA_VERSION or not isinstance(story.get("scenes"), list) or len(story["scenes"]) > _MAX_SCENES or not isinstance(story.get("artifacts", {}), dict):
            raise StoryError("story schema is unsupported")
        self._identity({"story_id": story.get("story_id"), "expected_revision": story.get("revision")})
        _text(story.get("title"), "title", required=True); _text(story.get("premise"), "premise"); _text(story.get("style_bible"), "style bible"); self._aspect_ratio(story.get("aspect_ratio"))
        ids = set()
        for scene in story["scenes"]:
            if not isinstance(scene, dict): raise StoryError("story scene is invalid")
            scene_id = scene.get("scene_id")
            try: uuid.UUID(str(scene_id))
            except (ValueError, TypeError, AttributeError) as error: raise StoryError("story scene ID is invalid") from error
            if scene_id in ids or not isinstance(scene.get("approved"), bool) or not isinstance(scene.get("revision"), int) or scene["revision"] < 1 or not isinstance(scene.get("artifacts"), dict): raise StoryError("story scene is invalid")
            ids.add(scene_id); _text(scene.get("prompt"), "scene prompt"); _text(scene.get("narration"), "scene narration")
            shot = scene.get("shot", {"trim_start_seconds": 0.0, "trim_end_seconds": 0.0, "narration_muted": False, "transition": "hard_cut"})
            if not isinstance(shot, dict) or shot.get("transition") != "hard_cut" or not isinstance(shot.get("narration_muted"), bool) or any(isinstance(shot.get(key), bool) or not isinstance(shot.get(key), (int, float)) or shot[key] < 0 for key in ("trim_start_seconds", "trim_end_seconds")) or (shot["trim_end_seconds"] and shot["trim_end_seconds"] <= shot["trim_start_seconds"]): raise StoryError("story shot is invalid")
            for step, artifact in scene["artifacts"].items():
                if step not in _STEPS or not isinstance(artifact, dict) or not isinstance(artifact.get("output_id"), str) or not _OUTPUT_ID.fullmatch(artifact["output_id"]):
                    raise StoryError("story artifact is invalid")
                # Early v1 documents stored only the selected output.  Treat
                # that as its sole retained variant when reading it; writes
                # made by this version always persist the explicit list.
                variants = artifact.get("variants", [{"output_id": artifact["output_id"]}])
                if not isinstance(variants, list) or not variants or any(not isinstance(item, dict) or not isinstance(item.get("output_id"), str) or not _OUTPUT_ID.fullmatch(item["output_id"]) for item in variants) or artifact["output_id"] not in {item["output_id"] for item in variants}:
                    raise StoryError("story artifact variants are invalid")

    @staticmethod
    def _new_scene(payload: dict[str, object]) -> dict[str, object]:
        return {"scene_id": str(uuid.uuid4()), "prompt": _text(payload.get("prompt"), "scene prompt"),
                "narration": _text(payload.get("narration"), "scene narration"), "approved": False,
                "revision": 1, "artifacts": {}, "shot": {"trim_start_seconds": 0.0, "trim_end_seconds": 0.0, "narration_muted": False, "transition": "hard_cut"}}

    @staticmethod
    def _invalidate_composition(story: dict[str, object]) -> None:
        story["artifacts"].pop("composition", None)

    @staticmethod
    def _artifact_ids(story: dict[str, object]) -> list[str]:
        ids = []
        for scene in story["scenes"]:
            ids.extend(item["output_id"] for item in scene["artifacts"].values())
        ids.extend(item["output_id"] for item in story.get("artifacts", {}).values() if isinstance(item, dict) and isinstance(item.get("output_id"), str))
        return sorted(set(ids))

    def _write(self, story: dict[str, object]) -> None:
        destination = self.root / f"{story['story_id']}.json"
        temporary = destination.with_suffix(".json.partial")
        temporary.write_text(json.dumps(story, sort_keys=True, separators=(",", ":")))
        temporary.replace(destination)
