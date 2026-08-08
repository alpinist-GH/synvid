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
from worker.service import GenerationService


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
