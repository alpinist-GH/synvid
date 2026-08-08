import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from worker.jobs import BusyError, JobState
from worker.paths import AppPaths
from worker.providers.fake import FakeProvider
from worker.providers.base import Capability, ProviderFacts
from worker.resources import Estimate
from worker.service import GenerationError, GenerationService


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
