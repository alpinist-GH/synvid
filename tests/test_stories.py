import tempfile
import unittest
from pathlib import Path
import zipfile
from unittest.mock import patch

from worker.stories import StoryConflict, StoryError, StoryStore
from worker.paths import AppPaths
from worker.providers.fake import FakeProvider
from worker.resources import Estimate
from worker.service import GenerationError, GenerationService


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

    def test_self_contained_package_validates_and_adopts_media_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); outputs = root / "outputs"; outputs.mkdir()
            store = StoryStore(root / "stories", outputs); story = store.create({"title": "Portable"})
            story = store.add_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"})
            output_id = "11111111-1111-1111-1111-111111111111"; (outputs / output_id).mkdir(); (outputs / output_id / "metadata.json").write_text("{}")
            story = store.record_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": story["scenes"][0]["scene_id"], "step": "still", "output_id": output_id})
            archive = store.export_project(story["story_id"], self_contained=True)
            destination = root / "new-outputs"; destination.mkdir(); imported = StoryStore(root / "new-stories", destination).import_project(archive)
            self.assertEqual(imported["title"], "Portable")
            self.assertEqual((destination / output_id / "metadata.json").read_text(), "{}")

    def test_project_import_rejects_missing_self_contained_media_before_adoption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); outputs = root / "outputs"; outputs.mkdir(); store = StoryStore(root / "stories", outputs)
            story = store.create({"title": "Portable"}); story = store.add_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"})
            output_id = "11111111-1111-1111-1111-111111111111"; (outputs / output_id).mkdir(); (outputs / output_id / "metadata.json").write_text("{}")
            story = store.record_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": story["scenes"][0]["scene_id"], "step": "still", "output_id": output_id})
            archive = store.export_project(story["story_id"], self_contained=True)
            broken = root / "missing-media.synvidstory"
            with zipfile.ZipFile(archive) as original, zipfile.ZipFile(broken, "w") as replacement:
                project = original.read("project.json"); digest = __import__("hashlib").sha256(project).hexdigest()
                replacement.writestr("project.json", project)
                replacement.writestr("manifest.json", __import__("json").dumps({"schema_version": 1, "project_sha256": digest, "self_contained": True, "files": {"project.json": digest}}))
            other_stories = root / "other-stories"; other_outputs = root / "other-outputs"
            with self.assertRaises(StoryError): StoryStore(other_stories, other_outputs).import_project(broken)
            self.assertFalse(list(other_stories.glob("*.json")))

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

    def test_shot_edits_are_non_destructive_and_invalidate_only_segment(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StoryStore(Path(temp)); story = store.create({"title": "Shot"})
            story = store.add_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"})
            scene_id = story["scenes"][0]["scene_id"]
            story = store.record_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "step": "clip", "output_id": "11111111-1111-1111-1111-111111111111"})
            story = store.record_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "step": "segment", "output_id": "22222222-2222-2222-2222-222222222222"})
            edited = store.update_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "trim_start_seconds": 0.5, "trim_end_seconds": 2.0, "narration_muted": True})
            self.assertEqual(edited["scenes"][0]["shot"]["trim_start_seconds"], 0.5)
            self.assertTrue(edited["scenes"][0]["shot"]["narration_muted"])
            self.assertIn("clip", edited["scenes"][0]["artifacts"])
            self.assertNotIn("segment", edited["scenes"][0]["artifacts"])

    def test_story_artifact_promotion_requires_matching_owned_media(self):
        with tempfile.TemporaryDirectory() as temp:
            service = GenerationService(AppPaths.under(Path(temp)), FakeProvider(), Estimate(1, True)); story = service.create_story({"title": "Media"})
            story = service.add_story_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"}); scene_id = story["scenes"][0]["scene_id"]
            output_id = "11111111-1111-1111-1111-111111111111"; directory = service.paths.outputs / output_id; directory.mkdir(); (directory / "image.png").write_bytes(b"image"); (directory / "metadata.json").write_text('{"result":{"media_file":"image.png"}}')
            saved = service.record_story_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "step": "still", "output_id": output_id})
            self.assertEqual(saved["scenes"][0]["artifacts"]["still"]["output_id"], output_id)
            with self.assertRaises(GenerationError): service.record_story_artifact({"story_id": saved["story_id"], "expected_revision": saved["revision"], "scene_id": scene_id, "step": "clip", "output_id": output_id})

    def test_imported_story_still_is_normalized_and_keeps_import_provenance(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            service = GenerationService(AppPaths.under(Path(temp)), FakeProvider(), Estimate(1, True)); story = service.create_story({"title": "Import"})
            story = service.add_story_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"}); scene_id = story["scenes"][0]["scene_id"]
            imports = service.paths.temporary / "imports"; imports.mkdir(parents=True); source_id = "image-0123456789abcdef"; Image.new("RGB", (4, 3)).save(imports / source_id, format="PNG")
            saved = service.import_story_still({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "source_image_id": source_id})
            output_id = saved["scenes"][0]["artifacts"]["still"]["output_id"]
            metadata = __import__("json").loads((service.paths.outputs / output_id / "metadata.json").read_text())
            self.assertEqual(metadata["request"]["source_import_id"], source_id)
            self.assertTrue((service.paths.outputs / output_id / "image.png").is_file())

    def test_imported_subtitles_are_validated_and_replace_only_subtitle_step(self):
        with tempfile.TemporaryDirectory() as temp:
            service = GenerationService(AppPaths.under(Path(temp)), FakeProvider(), Estimate(1, True)); story = service.create_story({"title": "Captions"})
            story = service.add_story_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"}); scene_id = story["scenes"][0]["scene_id"]
            imports = service.paths.temporary / "imports"; imports.mkdir(parents=True); source_id = "subtitle-0123456789abcdef"; (imports / source_id).write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
            saved = service.import_story_subtitles({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "source_subtitle_id": source_id})
            output_id = saved["scenes"][0]["artifacts"]["subtitles"]["output_id"]
            self.assertTrue((service.paths.outputs / output_id / "subtitles.srt").is_file())

    def test_imported_clip_is_normalized_to_current_scene_facts(self):
        with tempfile.TemporaryDirectory() as temp:
            service = GenerationService(AppPaths.under(Path(temp)), FakeProvider(), Estimate(1, True)); story = service.create_story({"title": "Clip"})
            story = service.add_story_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"}); scene_id = story["scenes"][0]["scene_id"]
            baseline = "11111111-1111-1111-1111-111111111111"; directory = service.paths.outputs / baseline; directory.mkdir(); (directory / "video.mp4").write_bytes(b"baseline")
            (directory / "metadata.json").write_text('{"request":{"width":64,"height":48,"frames":8,"fps":8},"result":{"media_file":"video.mp4"}}')
            story = service.record_story_artifact({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "step": "clip", "output_id": baseline})
            imports = service.paths.temporary / "imports"; imports.mkdir(parents=True); source_id = "clip-0123456789abcdef"; (imports / source_id).write_bytes(b"source")
            commands = []
            def run(command, **_kwargs):
                commands.append(command); Path(command[-1]).write_bytes(b"normalized")
            with patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="/fixed/ffmpeg"), patch("worker.service.subprocess.run", side_effect=run):
                saved = service.import_story_clip({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene_id, "source_clip_id": source_id})
            output_id = saved["scenes"][0]["artifacts"]["clip"]["output_id"]
            self.assertNotEqual(output_id, baseline); self.assertTrue(any("scale=64:48" in item for item in commands[0]))
            metadata = __import__("json").loads((service.paths.outputs / output_id / "metadata.json").read_text())
            self.assertEqual(metadata["request"]["source_import_id"], source_id)
