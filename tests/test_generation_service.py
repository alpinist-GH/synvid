import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from worker.jobs import BusyError, JobState
from worker.paths import AppPaths
from worker.providers.fake import FakeProvider
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
