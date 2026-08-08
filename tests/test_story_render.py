import tempfile
import unittest
from pathlib import Path
from worker.stories import StoryStore
from worker.story_render import StoryRenderer

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
