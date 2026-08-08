import tempfile
import unittest
from pathlib import Path
from worker.stories import StoryStore
from worker.story_render import StoryRenderer
from worker.story_compose import compose_hard_cuts
from unittest.mock import patch

IDS = iter(["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", "33333333-3333-3333-3333-333333333333"])

class StoryRenderTests(unittest.TestCase):
    def test_approved_scenes_resume_without_repeating_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StoryStore(Path(temp)); story = store.create({"title":"Render"})
            story = store.add_scene({"story_id":story["story_id"],"expected_revision":story["revision"],"prompt":"one"})
            story = store.update_scene({"story_id":story["story_id"],"expected_revision":story["revision"],"scene_id":story["scenes"][0]["scene_id"],"approved":True})
            calls=[]
            def step(name):
                return lambda *_: (calls.append(name), next(IDS))[1]
            renderer=StoryRenderer(store, step("still"), step("clip"), step("narration"))
            saved=renderer.render(story["story_id"],story["revision"])
            self.assertEqual(calls,["still","clip"])
            renderer.render(saved["story_id"],saved["revision"])
            self.assertEqual(calls,["still","clip"])

    def test_composition_applies_non_destructive_trim_and_mute(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); output_id = "11111111-1111-1111-1111-111111111111"; (root / output_id).mkdir(); (root / output_id / "video.mp4").write_bytes(b"clip")
            story = {"scenes": [{"approved": True, "artifacts": {"clip": {"output_id": output_id}}, "shot": {"trim_start_seconds": 1.0, "trim_end_seconds": 2.5, "narration_muted": True, "transition": "hard_cut"}}]}
            destination = root / "composed.mp4"; commands = []
            def run(command, **_kwargs): commands.append(command); Path(command[-1]).write_bytes(b"media")
            with patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="/fixed/ffmpeg"), patch("worker.story_compose.subprocess.run", side_effect=run):
                compose_hard_cuts(story, root, destination)
            self.assertIn("-ss", commands[0]); self.assertIn("-t", commands[0]); self.assertIn("-an", commands[0])
