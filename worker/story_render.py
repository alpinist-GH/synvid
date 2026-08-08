"""Resumable approved-scene render orchestration.

Concrete media work is injected so the state machine can be tested without
loading models; each step is recorded before the next begins.
"""
from __future__ import annotations
from typing import Callable

from .stories import StoryError, StoryStore


class StoryRenderError(RuntimeError): pass


class StoryRenderer:
    def __init__(self, stories: StoryStore, make_still: Callable[[dict], str], make_clip: Callable[[dict, str], str], make_narration: Callable[[dict, str], str], make_subtitles: Callable[[dict, str], str] | None = None):
        self.stories, self.make_still, self.make_clip, self.make_narration, self.make_subtitles = stories, make_still, make_clip, make_narration, make_subtitles

    def render(self, story_id: str, expected_revision: int, *, scene_ids: set[str] | None = None,
               through: str = "narration", cancelled: Callable[[], bool] = lambda: False,
               progress: Callable[[float, str], None] = lambda _fraction, _text: None) -> dict:
        """Render approved stale work only, stopping at a reviewable phase.

        The callbacks create immutable output IDs; this coordinator never
        touches media paths and therefore cannot overwrite an existing output.
        """
        if through not in {"still", "clip", "narration", "subtitles"}:
            raise StoryRenderError("story render phase is unavailable")
        story = self.stories.get(story_id)
        if story["revision"] != expected_revision: raise StoryRenderError("story changed in another window; reload before rendering")
        selected = [scene for scene in story["scenes"] if scene_ids is None or scene["scene_id"] in scene_ids]
        if scene_ids is not None and len(selected) != len(scene_ids):
            raise StoryRenderError("selected story scene is unavailable")
        approved = [scene for scene in selected if scene["approved"]]
        if len(approved) != len(selected):
            raise StoryRenderError("selected story scenes must be approved before rendering")
        for number, scene in enumerate(approved, start=1):
            if not scene["approved"]: continue
            if cancelled(): raise InterruptedError("story render cancelled")
            if "still" not in scene["artifacts"]:
                progress((number - 1) / max(1, len(approved)), f"Scene {number}: generating key image")
                story = self._record(story, scene["scene_id"], "still", self.make_still(scene)); scene = self._scene(story, scene["scene_id"])
            if through == "still": continue
            if "clip" not in scene["artifacts"]:
                if cancelled(): raise InterruptedError("story render cancelled")
                progress((number - 0.66) / max(1, len(approved)), f"Scene {number}: generating motion clip")
                story = self._record(story, scene["scene_id"], "clip", self.make_clip(scene, scene["artifacts"]["still"]["output_id"])); scene = self._scene(story, scene["scene_id"])
            if through == "clip": continue
            if scene.get("narration") and "narration" not in scene["artifacts"]:
                if cancelled(): raise InterruptedError("story render cancelled")
                progress((number - 0.33) / max(1, len(approved)), f"Scene {number}: generating narration")
                story = self._record(story, scene["scene_id"], "narration", self.make_narration(scene, scene["artifacts"]["clip"]["output_id"]))
                scene = self._scene(story, scene["scene_id"])
            if through == "narration" or not scene.get("narration"): continue
            if "subtitles" not in scene["artifacts"]:
                if self.make_subtitles is None: raise StoryRenderError("story subtitles are unavailable")
                story = self._record(story, scene["scene_id"], "subtitles", self.make_subtitles(scene, scene["artifacts"]["narration"]["output_id"]))
        progress(1.0, "Story render checkpoint saved")
        return story

    def _record(self, story: dict, scene_id: str, step: str, output_id: str) -> dict:
        return self.stories.record_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "step": step, "output_id": output_id})

    @staticmethod
    def _scene(story: dict, scene_id: str) -> dict:
        return next(scene for scene in story["scenes"] if scene["scene_id"] == scene_id)
