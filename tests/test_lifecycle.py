import tempfile
import threading
import time
import unittest
from pathlib import Path

from worker.jobs import BusyError, JobController, JobState
from worker.outputs import OutputError, allocate, promote, resolve_owned_file
from worker.resources import Estimate, ReservationBook


class LifecycleTests(unittest.TestCase):
    def test_atomic_promotion_and_containment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = allocate(root)
            (paths.partial_dir / "result.json").write_text("{}")
            self.assertTrue(promote(paths).is_dir())
            self.assertEqual(resolve_owned_file(root, paths.output_id, "result.json").name, "result.json")
            with self.assertRaises(OutputError):
                resolve_owned_file(root, paths.output_id, "../secret")

    def test_reservations_release(self):
        with tempfile.TemporaryDirectory() as temp:
            book = ReservationBook(Path(temp), safety_margin_bytes=0)
            token = book.reserve(Estimate(bytes_required=1, is_measured=True))
            book.release(token)
            self.assertEqual(book.reserved_bytes, 0)

    def test_reservations_release_on_failure_and_crash_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            book = ReservationBook(Path(temp), safety_margin_bytes=0)
            with self.assertRaises(RuntimeError):
                with book.hold(Estimate(bytes_required=1, is_measured=True)):
                    raise RuntimeError("cancelled")
            self.assertEqual(book.reserved_bytes, 0)
            book.reserve(Estimate(bytes_required=1, is_measured=True))
            book.recover_after_interruption()
            self.assertEqual(book.reserved_bytes, 0)

    def test_busy_cancel_and_single_terminal_state(self):
        controller = JobController()
        entered = threading.Event()
        release = threading.Event()

        def runner(progress, cancelled):
            entered.set()
            while not release.wait(0.01):
                if cancelled():
                    raise InterruptedError()

        job = controller.submit(runner)
        self.assertTrue(entered.wait(1))
        with self.assertRaises(BusyError):
            controller.submit(runner)
        controller.cancel(job.job_id)
        release.set()
        for _ in range(100):
            if controller.status(job.job_id).state != JobState.RUNNING:
                break
            time.sleep(0.01)
        self.assertEqual(controller.status(job.job_id).state, JobState.CANCELLED)
