from dataclasses import dataclass, field
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from worker.jobs import BusyError, JobState
from worker.paths import AppPaths
from worker.providers.fake import FakeProvider
from worker.providers.base import Capability, ProgressCallback, ProviderFacts
from worker.resources import Estimate
from worker.service import GenerationError, GenerationService
from worker.stories import StoryConflict, StoryError
from typing import Callable


@dataclass
class CalibratableFakeProvider:
    """A fixture calibratable provider; mirrors real providers' recipes-wrapper merge."""

    mode: str = "success"
    facts: ProviderFacts = ProviderFacts(
        provider_id="fake-calibratable",
        capabilities=frozenset({Capability.VIDEO_GENERATION}),
        profile="shareable",
        revision="fixture-v1",
        license_name="test-only",
        requires_access_confirmation=False,
        calibration_recipes=frozenset({"Balanced", "Draft"}),
    )
    unloaded: bool = False
    calibrate_started: threading.Event = field(default_factory=threading.Event)

    def run(self, request, progress, cancelled) -> dict[str, str]:
        raise NotImplementedError("fixture provider only calibrates")

    def calibrate(self, recipe_name: str, existing_raw: dict | None, progress: ProgressCallback, cancelled: Callable[[], bool]) -> dict[str, object]:
        self.calibrate_started.set()
        if self.mode == "failure":
            raise RuntimeError("fixture calibration failed")
        if self.mode == "hang":
            while not cancelled():
                time.sleep(0.01)
            raise InterruptedError("fixture calibration cancelled")
        recipes = dict(existing_raw.get("recipes", {})) if isinstance(existing_raw, dict) else {}
        recipes[recipe_name] = {
            "width": 64, "height": 64, "steps": 4, "guidance_scale": 1.0,
            "dtype": "bfloat16", "estimated_disk_bytes": 1, "peak_rss_bytes": 1,
        }
        return {"recipes": recipes}

    def unload(self) -> None:
        self.unloaded = True


class GenerationServiceTests(unittest.TestCase):
    PAYLOAD = {
        "prompt": "fixture prompt", "seed": 1, "width": 64, "height": 64,
        "frames": 9, "fps": 8, "steps": 3, "guidance_scale": 1.0,
    }

    def _service(self, root, provider=None):
        return GenerationService(AppPaths.under(Path(root)), provider or FakeProvider(), Estimate(1, True))

    def test_fake_and_real_provider_contract_persists_immutable_output(self):
        with tempfile.TemporaryDirectory() as temp:
            service = self._service(temp)
            terminal = threading.Event()
            received = []
            job = service.submit(self.PAYLOAD, lambda _job: None, lambda job, output: (received.append((job, output)), terminal.set()))
            self.assertTrue(terminal.wait(2))
            self.assertEqual(received[0][0].state, JobState.SUCCEEDED)
            output_id = received[0][1]["output_id"]
            output_dir = service.paths.outputs / output_id
            metadata = json.loads((output_dir / "metadata.json").read_text())
            self.assertEqual(metadata["output_id"], output_id)
            self.assertTrue((output_dir / "tiny.mp4").is_file())
            self.assertEqual(service.reservations.reserved_bytes, 0)

    def test_failure_cleans_partial_and_emits_one_terminal_state(self):
        with tempfile.TemporaryDirectory() as temp:
            service = self._service(temp, FakeProvider(mode="failure"))
            terminal = threading.Event()
            calls = []
            job = service.submit(self.PAYLOAD, lambda _job: None, lambda job, output: (calls.append((job, output)), terminal.set()))
            self.assertTrue(terminal.wait(2))
            self.assertEqual(calls[0][0].state, JobState.FAILED)
            self.assertIsNone(calls[0][1])
            self.assertFalse((service.paths.outputs / ".partial").exists() and any((service.paths.outputs / ".partial").iterdir()))
            self.assertEqual(service.reservations.reserved_bytes, 0)

    def test_busy_response_while_active_job(self):
        class HangingProvider(FakeProvider):
            def run(self, request, progress, cancelled):
                while not cancelled():
                    time.sleep(0.01)
                raise InterruptedError()
        with tempfile.TemporaryDirectory() as temp:
            service = self._service(temp, HangingProvider())
            first = service.submit(self.PAYLOAD, lambda _job: None, lambda _job, _output: None)
            with self.assertRaises(BusyError):
                service.submit(self.PAYLOAD, lambda _job: None, lambda _job, _output: None)
            service.cancel(first.job_id)

    def test_model_catalog_removal_and_temporary_cleanup_stay_in_owned_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            service = self._service(temp)
            snapshot = service.paths.models / "qwen-image-edit" / "snapshot"
            snapshot.mkdir(parents=True); (snapshot / "model.safetensors").write_bytes(b"model")
            catalog = {item["model_id"]: item for item in service.model_catalog()["models"]}
            self.assertTrue(catalog["qwen-image-edit"]["installed"])
            self.assertIn("Edits a completed image", catalog["qwen-image-edit"]["reason"])
            removed = service.remove_model("qwen-image-edit")
            self.assertTrue(removed["removed"]); self.assertFalse(snapshot.parent.exists())
            temporary = service.paths.temporary / "imports" / "source.png"
            temporary.parent.mkdir(parents=True); temporary.write_bytes(b"temporary")
            cleaned = service.clean_temporary()
            self.assertEqual(cleaned["freed_bytes"], len(b"temporary")); self.assertFalse(temporary.exists())
            self.assertTrue(service.paths.outputs.is_dir())

    def test_installed_retired_wan_model_is_removal_only(self):
        with tempfile.TemporaryDirectory() as temp:
            service = self._service(temp)
            root = service.paths.models / "wan2.2-ti2v-5b"
            (root / "snapshot").mkdir(parents=True)
            (root / "snapshot" / "model.safetensors").write_bytes(b"retired")
            catalog = {item["model_id"]: item for item in service.model_catalog()["models"]}
            self.assertTrue(catalog["wan2.2-ti2v-5b"]["installed"])
            self.assertTrue(catalog["wan2.2-ti2v-5b"]["retired"])
            removed = service.remove_model("wan2.2-ti2v-5b")
            self.assertTrue(removed["removed"])
            self.assertFalse(root.exists())

    def test_library_deletion_removes_unreferenced_output_and_refuses_descendants(self):
        with tempfile.TemporaryDirectory() as temp:
            service = self._service(temp)
            terminal = threading.Event(); received = []
            service.submit(self.PAYLOAD, lambda _job: None, lambda job, output: (received.append((job, output)), terminal.set()))
            self.assertTrue(terminal.wait(2))
            output_id = received[0][1]["output_id"]
            self.assertEqual([item["output_id"] for item in service.library_payload()], [output_id])
            result = service.delete_output(output_id)
            self.assertTrue(result["deleted"])
            self.assertGreater(result["freed_bytes"], 0)
            self.assertFalse((service.paths.outputs / output_id).exists())
            self.assertEqual(service.library_payload(), [])

            source_id = "00000000-0000-0000-0000-000000000001"
            descendant_id = "00000000-0000-0000-0000-000000000002"
            for item_id, lineage in ((source_id, []), (descendant_id, [{"output_id": source_id, "relation": "edited_from"}])):
                directory = service.paths.outputs / item_id; directory.mkdir()
                (directory / "video.mp4").write_bytes(b"video")
                metadata = {"output_id": item_id, "request": {}, "result": {"media_file": "video.mp4"}, "lineage": lineage}
                path = directory / "metadata.json"; path.write_text(json.dumps(metadata)); service._index_output(path)
            with self.assertRaisesRegex(GenerationError, "descendants"):
                service.delete_output(source_id)
            grandchild_id = "00000000-0000-0000-0000-000000000003"
            directory = service.paths.outputs / grandchild_id; directory.mkdir()
            (directory / "video.mp4").write_bytes(b"video")
            metadata = {"output_id": grandchild_id, "request": {}, "result": {"media_file": "video.mp4"}, "lineage": [{"output_id": descendant_id, "relation": "narrated_from"}]}
            path = directory / "metadata.json"; path.write_text(json.dumps(metadata)); service._index_output(path)
            result = service.delete_output(source_id, cascade=True)
            self.assertEqual(result["deleted_output_ids"], [source_id, descendant_id, grandchild_id])
            self.assertEqual(service.library_payload(), [])

    def test_story_planner_draft_is_cancellable_and_unloaded(self):
        class BlockingPlanner:
            def __init__(self): self.unloaded = False
            def draft(self, _premise, _style, _count, cancelled):
                while not cancelled(): time.sleep(0.01)
                raise InterruptedError("cancelled")
            def unload(self): self.unloaded = True
        with tempfile.TemporaryDirectory() as temp:
            service = self._service(temp); planner = BlockingPlanner(); service.story_planner = planner; service.story_planner_enabled = True
            story = service.create_story({"title": "Draft"}); done = threading.Event(); received = []
            job = service.submit_story_draft({"story_id": story["story_id"], "expected_revision": story["revision"], "count": 3}, lambda _job: None, lambda completed, output: (received.append((completed, output)), done.set()))
            service.cancel(job.job_id)
            self.assertTrue(done.wait(2)); self.assertEqual(received[0][0].state, JobState.CANCELLED)
            self.assertIsNone(received[0][1]); self.assertTrue(planner.unloaded)

    def test_story_planner_is_unavailable_until_its_quality_gate_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            service = self._service(temp)
            story = service.create_story({"title": "Manual story", "premise": "Write scenes by hand"})
            with self.assertRaises(GenerationError):
                service.submit_story_draft(
                    {"story_id": story["story_id"], "expected_revision": story["revision"], "count": 3},
                    lambda _job: None,
                    lambda _job, _output: None,
                )

    def test_storyboard_render_is_one_job_and_checkpoints_each_scene(self):
        class ImageProfile: width = height = 64; steps = 2; guidance_scale = 0.0
        class VideoProfile: width = height = 64; frames = 9; fps = 8; steps = 2; guidance_scale = 1.0
        class Recipes: recipes = {"Balanced": VideoProfile()}
        class Image(FakeProvider):
            facts = ProviderFacts("story-image", frozenset({Capability.IMAGE_GENERATION}), "shareable", "fixture", "test", False)
            def measured_profile(self): return ImageProfile()
            def run(self, request, progress, cancelled):
                (request.output_dir / "image.png").write_bytes(b"image"); return {"media_file": "image.png"}
        class Video(FakeProvider):
            facts = ProviderFacts("story-video", frozenset({Capability.VIDEO_GENERATION}), "shareable", "fixture", "test", False)
            def measured_recipes(self): return Recipes()
            def run(self, request, progress, cancelled):
                self.sources = getattr(self, "sources", []) + [request.source_image]
                (request.output_dir / "video.mp4").write_bytes(b"video"); return {"media_file": "video.mp4"}
        with tempfile.TemporaryDirectory() as temp:
            image, video = Image(), Video()
            image.facts = Image.facts; video.facts = Video.facts
            service = GenerationService(AppPaths.under(Path(temp)), video, Estimate(1, True), additional_providers=(image,), estimates={"story-image": Estimate(1, True)})
            story = service.create_story({"title": "Storyboard"})
            story = service.add_story_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"})
            story = service.update_story_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": story["scenes"][0]["scene_id"], "approved": True})
            done = threading.Event(); received = []
            job = service.submit_story_render({"story_id": story["story_id"], "expected_revision": story["revision"], "through": "clip"}, lambda _job: None, lambda completed, output: (received.append((completed, output)), done.set()))
            self.assertTrue(done.wait(2)); self.assertEqual(received[0][0].state, JobState.SUCCEEDED)
            rendered = service.get_story(story["story_id"])
            self.assertIn("still", rendered["scenes"][0]["artifacts"]); self.assertIn("clip", rendered["scenes"][0]["artifacts"])
            self.assertEqual(len(video.sources), 1); self.assertTrue(video.sources[0].is_file())

    def _rendered_one_scene_story(self, temp, title):
        class ImageProfile: width = height = 64; steps = 2; guidance_scale = 0.0
        class VideoProfile: width = height = 64; frames = 9; fps = 8; steps = 2; guidance_scale = 1.0
        class Recipes: recipes = {"Balanced": VideoProfile()}
        class Image(FakeProvider):
            facts = ProviderFacts("story-image", frozenset({Capability.IMAGE_GENERATION}), "shareable", "fixture", "test", False)
            def measured_profile(self): return ImageProfile()
            def run(self, request, progress, cancelled):
                (request.output_dir / "image.png").write_bytes(b"image"); return {"media_file": "image.png"}
        class Video(FakeProvider):
            facts = ProviderFacts("story-video", frozenset({Capability.VIDEO_GENERATION}), "shareable", "fixture", "test", False)
            def measured_recipes(self): return Recipes()
            def run(self, request, progress, cancelled):
                (request.output_dir / "video.mp4").write_bytes(b"video"); return {"media_file": "video.mp4"}
        image, video = Image(), Video()
        image.facts = Image.facts; video.facts = Video.facts
        service = GenerationService(AppPaths.under(Path(temp)), video, Estimate(1, True), additional_providers=(image,), estimates={"story-image": Estimate(1, True)})
        story = service.create_story({"title": title})
        story = service.add_story_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"})
        story = service.update_story_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": story["scenes"][0]["scene_id"], "approved": True})
        done = threading.Event(); received = []
        service.submit_story_render({"story_id": story["story_id"], "expected_revision": story["revision"], "through": "clip"}, lambda _job: None, lambda completed, output: (received.append((completed, output)), done.set()))
        self.assertTrue(done.wait(2)); self.assertEqual(received[0][0].state, JobState.SUCCEEDED)
        story = service.get_story(story["story_id"])
        artifacts = story["scenes"][0]["artifacts"]
        return service, story, artifacts["still"]["output_id"], artifacts["clip"]["output_id"]

    def test_delete_story_by_default_retains_generated_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            service, story, still_id, clip_id = self._rendered_one_scene_story(temp, "Kept media")
            result = service.delete_story({"story_id": story["story_id"], "expected_revision": story["revision"]})
            self.assertTrue(result["deleted"]); self.assertFalse(result["cascade"])
            self.assertEqual(result["deleted_output_ids"], [])
            self.assertEqual(sorted(result["retained_output_ids"]), sorted([still_id, clip_id]))
            with self.assertRaises(StoryError):
                service.get_story(story["story_id"])
            self.assertTrue((service.paths.outputs / still_id).is_dir())
            self.assertTrue((service.paths.outputs / clip_id).is_dir())

    def test_delete_story_cascade_removes_unshared_artifacts_but_keeps_shared_ones(self):
        with tempfile.TemporaryDirectory() as temp:
            service, story, still_id, clip_id = self._rendered_one_scene_story(temp, "Cascade me")
            other = service.create_story({"title": "Shares the still"})
            other = service.add_story_scene({"story_id": other["story_id"], "expected_revision": other["revision"], "prompt": "borrowed"})
            other = service.record_story_artifact({"story_id": other["story_id"], "expected_revision": other["revision"], "scene_id": other["scenes"][0]["scene_id"], "step": "still", "output_id": still_id})
            result = service.delete_story({"story_id": story["story_id"], "expected_revision": story["revision"], "cascade": True})
            self.assertTrue(result["cascade"])
            self.assertEqual(result["deleted_output_ids"], [clip_id])
            self.assertEqual(result["retained_output_ids"], [still_id])
            self.assertFalse((service.paths.outputs / clip_id).exists())
            self.assertTrue((service.paths.outputs / still_id).is_dir())

    def test_delete_story_rejects_stale_revision_and_busy_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            service = self._service(temp)
            story = service.create_story({"title": "Conflict"})
            with self.assertRaises(StoryConflict):
                service.delete_story({"story_id": story["story_id"], "expected_revision": story["revision"] + 1})
            class HangingProvider(FakeProvider):
                def run(self, request, progress, cancelled):
                    while not cancelled(): time.sleep(0.01)
                    raise InterruptedError()
            service = GenerationService(AppPaths.under(Path(temp) / "busy"), HangingProvider(), Estimate(1, True))
            story = service.create_story({"title": "Busy"})
            service.submit(self.PAYLOAD, lambda _job: None, lambda _job, _output: None)
            with self.assertRaises(GenerationError):
                service.delete_story({"story_id": story["story_id"], "expected_revision": story["revision"]})

    def test_story_render_unloads_the_sibling_provider_between_still_and_clip(self):
        # Measured: flux-schnell (~34 GiB peak MPS) plus LTX (~30 GiB peak
        # RSS) sum past this Mac's 48 GiB unified memory. Holding both
        # resident for the whole story render pushed real physical footprint
        # to 51+ GiB and stalled unrelated IPC requests for minutes
        # afterward. The render must alternate residency instead.
        class ImageProfile: width = height = 64; steps = 2; guidance_scale = 0.0
        class VideoProfile: width = height = 64; frames = 9; fps = 8; steps = 2; guidance_scale = 1.0
        class Recipes: recipes = {"Balanced": VideoProfile()}
        calls = []
        class Image(FakeProvider):
            facts = ProviderFacts("story-image", frozenset({Capability.IMAGE_GENERATION}), "shareable", "fixture", "test", False)
            def measured_profile(self): return ImageProfile()
            def run(self, request, progress, cancelled):
                calls.append("image-run")
                (request.output_dir / "image.png").write_bytes(b"image"); return {"media_file": "image.png"}
            def unload(self): calls.append("image-unload")
        class Video(FakeProvider):
            facts = ProviderFacts("story-video", frozenset({Capability.VIDEO_GENERATION}), "shareable", "fixture", "test", False)
            def measured_recipes(self): return Recipes()
            def run(self, request, progress, cancelled):
                calls.append("video-run")
                (request.output_dir / "video.mp4").write_bytes(b"video"); return {"media_file": "video.mp4"}
            def unload(self): calls.append("video-unload")
        with tempfile.TemporaryDirectory() as temp:
            image, video = Image(), Video()
            image.facts = Image.facts; video.facts = Video.facts
            service = GenerationService(AppPaths.under(Path(temp)), video, Estimate(1, True), additional_providers=(image,), estimates={"story-image": Estimate(1, True)})
            story = service.create_story({"title": "Alternating residency"})
            story = service.add_story_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "one"})
            story = service.add_story_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "prompt": "two"})
            for scene in story["scenes"]:
                story = service.update_story_scene({"story_id": story["story_id"], "expected_revision": story["revision"], "scene_id": scene["scene_id"], "approved": True})
            done = threading.Event(); received = []
            service.submit_story_render({"story_id": story["story_id"], "expected_revision": story["revision"], "through": "clip"}, lambda _job: None, lambda completed, output: (received.append((completed, output)), done.set()))
            self.assertTrue(done.wait(2)); self.assertEqual(received[0][0].state, JobState.SUCCEEDED)
            # Two scenes, each still followed by its clip: the sibling
            # provider is unloaded before every step so the two never hold
            # residency at the same time, even though the first unload calls
            # are no-ops (nothing loaded yet).
            self.assertEqual(calls, [
                "video-unload", "image-run",
                "image-unload", "video-run",
                "video-unload", "image-run",
                "image-unload", "video-run",
            ])

    def test_image_provider_uses_its_measured_profile_and_persists_png(self):
        class Profile:
            width = 64
            height = 64
            steps = 4
            guidance_scale = 0.0

        class FakeImageProvider(FakeProvider):
            def measured_profile(self):
                return Profile()

            def run(self, request, progress, cancelled):
                self.assert_request = request
                (request.output_dir / "image.png").write_bytes(b"fixture image")
                return {"media_file": "image.png", "media_type": "image/png"}

        with tempfile.TemporaryDirectory() as temp:
            provider = FakeImageProvider()
            provider.facts = ProviderFacts(
                provider_id="fake-image",
                capabilities=frozenset({Capability.IMAGE_GENERATION}),
                profile="shareable",
                revision="fixture-image-v1",
                license_name="test-only",
                requires_access_confirmation=False,
            )
            service = self._service(temp, provider)
            terminal = threading.Event()
            received = []
            job = service.submit(
                {"prompt": "fixture image", "seed": 7},
                lambda _job: None,
                lambda completed, output: (received.append((completed, output)), terminal.set()),
            )
            self.assertTrue(terminal.wait(2))
            self.assertEqual(received[0][0].state, JobState.SUCCEEDED)
            self.assertEqual(provider.assert_request.capability, Capability.IMAGE_GENERATION)
            self.assertEqual(provider.assert_request.width, 64)
            output_id = received[0][1]["output_id"]
            self.assertTrue((service.paths.outputs / output_id / "image.png").is_file())
            self.assertEqual(service.status_payload()["measured_image_profile"]["steps"], 4)

    def test_routes_to_explicitly_selected_measured_provider(self):
        class Profile:
            width = 64
            height = 64
            steps = 4
            guidance_scale = 0.0

        class SelectedImageProvider(FakeProvider):
            def measured_profile(self):
                return Profile()

            def run(self, request, progress, cancelled):
                self.request = request
                (request.output_dir / "image.png").write_bytes(b"fixture image")
                return {"media_file": "image.png", "media_type": "image/png"}

        with tempfile.TemporaryDirectory() as temp:
            image = SelectedImageProvider()
            image.facts = ProviderFacts(
                provider_id="selected-image", capabilities=frozenset({Capability.IMAGE_GENERATION}),
                profile="shareable", revision="fixture-image-v1", license_name="test-only",
                requires_access_confirmation=False,
            )
            service = GenerationService(
                AppPaths.under(Path(temp)), FakeProvider(), Estimate(1, True),
                additional_providers=(image,), estimates={"selected-image": Estimate(1, True)},
            )
            terminal = threading.Event()
            service.submit(
                {"model_id": "selected-image", "prompt": "fixture image", "seed": 7},
                lambda _job: None, lambda _job, _output: terminal.set(),
            )
            self.assertTrue(terminal.wait(2))
            self.assertEqual(image.request.capability, Capability.IMAGE_GENERATION)
            self.assertEqual(service.status_payload()["available_models"]["selected-image"]["measured_image_profile"]["steps"], 4)
            with self.assertRaisesRegex(Exception, "selected model is not available"):
                service.submit({"model_id": "wan2.1-14b", "prompt": "no", "seed": 1}, lambda _job: None, lambda _job, _output: None)

    def test_video_edit_creates_immutable_descendant_with_lineage(self):
        class Profile:
            width = height = 64
            frames = 9
            fps = 8
            steps = 3
            guidance_scale = 1.0

        class Recipes:
            recipes = {"Balanced": Profile()}

        class EditingProvider(FakeProvider):
            facts = ProviderFacts("fake-edit", frozenset({Capability.VIDEO_GENERATION, Capability.VIDEO_EDITING}), "shareable", "fixture-edit-v1", "test-only", False)
            def measured_recipes(self): return Recipes()
            def run(self, request, progress, cancelled):
                self.request = request
                (request.output_dir / "video.mp4").write_bytes(b"fixture video")
                return {"media_file": "video.mp4"}

        with tempfile.TemporaryDirectory() as temp:
            provider = EditingProvider()
            provider.facts = EditingProvider.facts
            service = self._service(temp, provider)
            source_dir = service.paths.outputs / "11111111-1111-1111-1111-111111111111"
            source_dir.mkdir(parents=True)
            (source_dir / "video.mp4").write_bytes(b"source")
            (source_dir / "metadata.json").write_text(json.dumps({"request": {"capability": "video_generation", "width": 64, "height": 64, "frames": 9, "fps": 8}}))
            terminal = threading.Event(); received = []
            service.submit_video_edit({"prompt": "change it", "seed": 2, "recipe": "Balanced", "source_output_id": source_dir.name, "change_amount": 0.35}, lambda _job: None, lambda job, output: (received.append((job, output)), terminal.set()))
            self.assertTrue(terminal.wait(2))
            output_id = received[0][1]["output_id"]
            metadata = json.loads((service.paths.outputs / output_id / "metadata.json").read_text())
            self.assertEqual(metadata["lineage"], [{"output_id": source_dir.name, "relation": "edited_from"}])
            self.assertEqual(provider.request.source_video, source_dir / "video.mp4")
            self.assertTrue((source_dir / "video.mp4").is_file())

    def test_image_edit_creates_immutable_descendant_with_lineage(self):
        class Profile:
            width = height = 64
            steps = 4
            guidance_scale = 1.0

        class EditingProvider(FakeProvider):
            facts = ProviderFacts("fake-image-edit", frozenset({Capability.IMAGE_EDITING}), "shareable", "fixture-image-edit-v1", "test-only", False)
            def measured_profile(self): return Profile()
            def run(self, request, progress, cancelled):
                self.request = request
                (request.output_dir / "image.png").write_bytes(b"edited fixture image")
                return {"media_file": "image.png", "media_type": "image/png"}

        with tempfile.TemporaryDirectory() as temp:
            provider = EditingProvider()
            provider.facts = EditingProvider.facts
            service = self._service(temp, provider)
            source_dir = service.paths.outputs / "11111111-1111-1111-1111-111111111111"
            source_dir.mkdir(parents=True)
            (source_dir / "image.png").write_bytes(b"source image")
            (source_dir / "metadata.json").write_text(json.dumps({"request": {"capability": "image_generation"}}))
            terminal = threading.Event(); received = []
            service.submit_image_edit({"model_id": "fake-image-edit", "prompt": "change it", "seed": 2, "source_output_id": source_dir.name}, lambda _job: None, lambda job, output: (received.append((job, output)), terminal.set()))
            self.assertTrue(terminal.wait(2))
            output_id = received[0][1]["output_id"]
            metadata = json.loads((service.paths.outputs / output_id / "metadata.json").read_text())
            self.assertEqual(metadata["request"]["capability"], "image_editing")
            self.assertEqual(metadata["lineage"], [{"output_id": source_dir.name, "relation": "edited_from"}])
            self.assertEqual(provider.request.source_image, source_dir / "image.png")
            self.assertTrue((source_dir / "image.png").is_file())

    def test_recipe_validation_is_driven_by_the_providers_own_catalog(self):
        class Profile:
            width = 512
            height = 288
            frames = 9
            fps = 8
            steps = 3
            guidance_scale = 1.0

        class Recipes:
            recipes = {"BalancedLandscape": Profile()}

        class AspectProvider(FakeProvider):
            facts = ProviderFacts(
                "fake-aspect", frozenset({Capability.VIDEO_GENERATION}), "shareable", "fixture-aspect-v1", "test-only", False,
                calibration_recipes=frozenset({"BalancedLandscape"}),
            )
            def measured_recipes(self): return Recipes()

        with tempfile.TemporaryDirectory() as temp:
            provider = AspectProvider()
            provider.facts = AspectProvider.facts
            service = self._service(temp, provider)
            terminal = threading.Event(); received = []
            service.submit(
                {"prompt": "wide shot", "seed": 1, "recipe": "BalancedLandscape"},
                lambda _job: None, lambda job, output: (received.append((job, output)), terminal.set()),
            )
            self.assertTrue(terminal.wait(2))
            self.assertEqual(received[0][0].state, JobState.SUCCEEDED)
            with self.assertRaisesRegex(GenerationError, "recipe is not available"):
                service.submit({"prompt": "wide shot", "seed": 1, "recipe": "Balanced"}, lambda _job: None, lambda _job, _output: None)


class CalibrationServiceTests(unittest.TestCase):
    def _service(self, root, calibratable):
        return GenerationService(AppPaths.under(Path(root)), FakeProvider(), Estimate(1, True), additional_providers=(calibratable,))

    def test_writes_measured_profile_atomically_on_success_and_unloads_other_providers(self):
        with tempfile.TemporaryDirectory() as temp:
            calibratable = CalibratableFakeProvider()
            service = self._service(temp, calibratable)
            terminal = threading.Event(); received = []
            service.submit_calibration("fake-calibratable", "Balanced", lambda _job: None, lambda job, output: (received.append((job, output)), terminal.set()))
            self.assertTrue(terminal.wait(2))
            self.assertEqual(received[0][0].state, JobState.SUCCEEDED)
            self.assertTrue(service.provider.unloaded)  # the OTHER resident provider, not the one calibrating
            profile_path = service.paths.models / "fake-calibratable" / "measured-profile.json"
            content = json.loads(profile_path.read_text())
            self.assertEqual(content["recipes"]["Balanced"]["width"], 64)
            self.assertEqual(service._estimates["fake-calibratable"], Estimate(1, True))

    def test_merges_a_second_recipe_without_clobbering_the_first(self):
        with tempfile.TemporaryDirectory() as temp:
            calibratable = CalibratableFakeProvider()
            service = self._service(temp, calibratable)
            for recipe in ("Balanced", "Draft"):
                terminal = threading.Event()
                service.submit_calibration("fake-calibratable", recipe, lambda _job: None, lambda _job, _output: terminal.set())
                self.assertTrue(terminal.wait(2))
            profile_path = service.paths.models / "fake-calibratable" / "measured-profile.json"
            content = json.loads(profile_path.read_text())
            self.assertEqual(set(content["recipes"]), {"Balanced", "Draft"})

    def test_rejects_a_recipe_the_provider_does_not_calibrate(self):
        with tempfile.TemporaryDirectory() as temp:
            service = self._service(temp, CalibratableFakeProvider())
            with self.assertRaises(GenerationError):
                service.submit_calibration("fake-calibratable", "High", lambda _job: None, lambda _job, _output: None)

    def test_rejects_unknown_model(self):
        with tempfile.TemporaryDirectory() as temp:
            service = self._service(temp, CalibratableFakeProvider())
            with self.assertRaises(GenerationError):
                service.submit_calibration("not-a-real-model", "Balanced", lambda _job: None, lambda _job, _output: None)

    def test_failure_leaves_any_existing_profile_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            calibratable = CalibratableFakeProvider(mode="failure")
            service = self._service(temp, calibratable)
            profile_path = service.paths.models / "fake-calibratable" / "measured-profile.json"
            terminal = threading.Event(); received = []
            service.submit_calibration("fake-calibratable", "Balanced", lambda _job: None, lambda job, output: (received.append((job, output)), terminal.set()))
            self.assertTrue(terminal.wait(2))
            self.assertEqual(received[0][0].state, JobState.FAILED)
            self.assertFalse(profile_path.exists())

    def test_respects_single_active_job_admission(self):
        with tempfile.TemporaryDirectory() as temp:
            calibratable = CalibratableFakeProvider(mode="hang")
            service = self._service(temp, calibratable)
            first = service.submit_calibration("fake-calibratable", "Balanced", lambda _job: None, lambda _job, _output: None)
            self.assertTrue(calibratable.calibrate_started.wait(2))
            with self.assertRaises(BusyError):
                service.submit_calibration("fake-calibratable", "Balanced", lambda _job: None, lambda _job, _output: None)
            service.cancel(first.job_id)
