import tempfile
import unittest
from pathlib import Path
import zipfile

from worker.stories import StoryConflict, StoryError, StoryStore


class StoryStoreTests(unittest.TestCase):
    def test_manual_story_survives_reopen_and_scene_changes_are_narrow(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StoryStore(Path(temp))
            story = store.create({"title": "Storm over the lake", "premise": "A calm journey", "style_bible": "watercolor", "aspect_ratio": "16:9"})
            first = store.add_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "A boat on a still lake"})
            second = store.add_scene({"story_id": first["story_id"], "expected_revision": first["revision"], "prompt": "Clouds gather"})
            second["scenes"][1]["artifacts"] = {"still": {"output_id": "11111111-1111-1111-1111-111111111111"}}
            store._write(second)
            edited = store.update_scene({"story_id": second["story_id"], "expected_revision": second["revision"], "scene_id": second["scenes"][0]["scene_id"], "narration": "The sky darkens.", "approved": True})
            reopened = StoryStore(Path(temp)).get(edited["story_id"])
            self.assertEqual(reopened, edited)
            self.assertTrue(reopened["scenes"][0]["approved"])
            self.assertEqual(reopened["scenes"][1]["prompt"], "Clouds gather")
            self.assertEqual(reopened["scenes"][0]["artifacts"], {})
            self.assertEqual(reopened["scenes"][1]["artifacts"], {"still": {"output_id": "11111111-1111-1111-1111-111111111111"}})
            self.assertEqual(StoryStore(Path(temp)).list()[0]["title"], "Storm over the lake")

    def test_revision_conflicts_and_invalid_reorder_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StoryStore(Path(temp)); story = store.create({"title": "A story"})
            changed = store.update({"story_id": story["story_id"], "expected_revision": 1, "premise": "new"})
            with self.assertRaises(StoryConflict):
                store.update({"story_id": story["story_id"], "expected_revision": 1, "premise": "stale"})
            with self.assertRaises(StoryError):
                store.reorder({"story_id": story["story_id"], "expected_revision": changed["revision"], "scene_ids": ["missing"]})

    def test_project_archive_is_atomic_portable_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StoryStore(Path(temp)); story = store.create({"title": "Portable"})
            archive = store.export_project(story["story_id"])
            imported = store.import_project(archive)
            self.assertNotEqual(imported["story_id"], story["story_id"])
            self.assertEqual(imported["title"], "Portable")
            bad = Path(temp) / "unsafe.synvidstory"
            with zipfile.ZipFile(bad, "w") as contents:
                contents.writestr("../outside", "no")
                contents.writestr("project.json", "{}")
            with self.assertRaises(StoryError): store.import_project(bad)

    def test_artifacts_are_scene_scoped_and_revision_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StoryStore(Path(temp)); story = store.create({"title": "Artifacts"})
            story = store.add_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"})
            story = store.add_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "two"})
            saved = store.record_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": story["scenes"][0]["scene_id"], "step": "still", "output_id": "11111111-1111-1111-1111-111111111111"})
            self.assertIn("still", saved["scenes"][0]["artifacts"]); self.assertEqual(saved["scenes"][1]["artifacts"], {})
            with self.assertRaises(StoryConflict): store.record_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": story["scenes"][0]["scene_id"], "step": "clip", "output_id": "11111111-1111-1111-1111-111111111111"})

    def test_invalidation_is_narrow_and_variant_promotion_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StoryStore(Path(temp)); story = store.create({"title": "Narrow"})
            story = store.add_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "first", "narration": "hello"})
            scene_id = story["scenes"][0]["scene_id"]
            ids = iter(["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", "33333333-3333-3333-3333-333333333333"])
            for step in ("still", "clip", "narration"):
                story = store.record_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "step": step, "output_id": next(ids)})
            changed = store.update_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "narration": "changed"})
            self.assertIn("still", changed["scenes"][0]["artifacts"])
            self.assertIn("clip", changed["scenes"][0]["artifacts"])
            self.assertNotIn("narration", changed["scenes"][0]["artifacts"])
            story = store.record_artifact({"story_id": changed["story_id"], "expected_revision": changed["revision"], "scene_id": scene_id, "step": "still", "output_id": "44444444-4444-4444-4444-444444444444"})
            promoted = store.promote_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "step": "still", "output_id": "11111111-1111-1111-1111-111111111111"})
            self.assertEqual(promoted["scenes"][0]["artifacts"]["still"]["output_id"], "11111111-1111-1111-1111-111111111111")
            self.assertEqual(len(promoted["scenes"][0]["artifacts"]["still"]["variants"]), 2)

    def test_duplicate_is_draft_without_artifacts_and_delete_retains_media(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StoryStore(Path(temp)); story = store.create({"title": "Duplicate"})
            story = store.add_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"})
            scene_id = story["scenes"][0]["scene_id"]
            copied = store.duplicate_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id})
            self.assertEqual(len(copied["scenes"]), 2); self.assertFalse(copied["scenes"][1]["approved"]); self.assertEqual(copied["scenes"][1]["artifacts"], {})
            deleted = store.delete_scene({"story_id": copied["story_id"], "expected_revision": copied["revision"], "scene_id": scene_id})
            self.assertEqual(len(deleted["scenes"]), 1)

    def test_scene_or_story_change_makes_only_current_composition_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StoryStore(Path(temp)); story = store.create({"title": "Composition"})
            story = store.add_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "first"})
            scene_id = story["scenes"][0]["scene_id"]
            story = store.record_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "step": "still", "output_id": "11111111-1111-1111-1111-111111111111"})
            composed = store.record_composition({"story_id": story["story_id"], "expected_revision": story["revision"], "output_id": "22222222-2222-2222-2222-222222222222"})
            self.assertIn("composition", composed["artifacts"])
            edited = store.update_scene({"story_id": composed["story_id"], "expected_revision": composed["revision"], "scene_id": scene_id, "narration": "new"})
            self.assertNotIn("composition", edited["artifacts"])
            self.assertIn("still", edited["scenes"][0]["artifacts"])

    def test_corrupt_variant_list_is_rejected_on_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StoryStore(Path(temp)); story = store.create({"title": "Variants"})
            story = store.add_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"})
            story = store.record_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": story["scenes"][0]["scene_id"], "step": "still", "output_id": "11111111-1111-1111-1111-111111111111"})
            story["scenes"][0]["artifacts"]["still"]["variants"] = [{"output_id": "22222222-2222-2222-2222-222222222222"}]
            store._write(story)
            with self.assertRaises(StoryError):
                store.get(story["story_id"])
