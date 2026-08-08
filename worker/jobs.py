"""Single-active-job lifecycle with one terminal state per job."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import threading
import uuid
from typing import Callable


class JobState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_STATES = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.INTERRUPTED})


class BusyError(RuntimeError):
    def __init__(self, current_job_id: str):
        super().__init__("a job is already active")
        self.current_job_id = current_job_id


@dataclass
class Job:
    job_id: str
    state: JobState = JobState.RUNNING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    progress: float = 0.0
    status_text: str = "queued"
    error: str | None = None
    cancel_requested: bool = False


class JobController:
    def __init__(self):
        self._lock = threading.Lock()
        self._current: Job | None = None
        self._jobs: dict[str, Job] = {}

    def submit(self, runner: Callable[[Callable[[float, str], None], Callable[[], bool]], None]) -> Job:
        with self._lock:
            if self._current is not None:
                raise BusyError(self._current.job_id)
            job = Job(job_id=str(uuid.uuid4()))
            self._current = job
            self._jobs[job.job_id] = job
        threading.Thread(target=self._run, args=(job, runner), daemon=True).start()
        return job

    def _run(self, job: Job, runner: Callable[[Callable[[float, str], None], Callable[[], bool]], None]) -> None:
        try:
            runner(lambda progress, text: self._progress(job, progress, text), lambda: job.cancel_requested)
            terminal = JobState.CANCELLED if job.cancel_requested else JobState.SUCCEEDED
            self._finish(job, terminal)
        except InterruptedError:
            self._finish(job, JobState.CANCELLED)
        except BaseException as error:
            self._finish(job, JobState.FAILED, str(error))

    def _progress(self, job: Job, progress: float, text: str) -> None:
        with self._lock:
            if job.state == JobState.RUNNING:
                job.progress = min(1.0, max(0.0, progress))
                job.status_text = text

    def _finish(self, job: Job, state: JobState, error: str | None = None) -> None:
        with self._lock:
            if job.state in TERMINAL_STATES:
                return
            job.state, job.error = state, error
            job.finished_at = datetime.now(timezone.utc).isoformat()
            if self._current is job:
                self._current = None

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            if job.state == JobState.RUNNING:
                job.cancel_requested = True
                job.status_text = "cancelling"
            return job

    def status(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id]

    def current(self) -> Job | None:
        with self._lock:
            return self._current
