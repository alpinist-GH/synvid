"""Central resource admission with reservation cleanup guarantees."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import uuid
from contextlib import contextmanager
from typing import Iterator


class AdmissionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Estimate:
    bytes_required: int | None
    is_measured: bool

    def require_measured(self) -> int:
        if self.bytes_required is None or not self.is_measured:
            raise AdmissionError("operation has no disk estimate")
        return self.bytes_required


class ReservationBook:
    def __init__(self, root: Path, safety_margin_bytes: int = 2 * 1024**3):
        self._root = root
        self._safety_margin_bytes = safety_margin_bytes
        self._reservations: dict[str, int] = {}

    def reserve(self, estimate: Estimate) -> str:
        required = estimate.require_measured()
        available = shutil.disk_usage(self._root).free - sum(self._reservations.values())
        if required + self._safety_margin_bytes > available:
            raise AdmissionError("insufficient disk space after safety margin")
        token = str(uuid.uuid4())
        self._reservations[token] = required
        return token

    def release(self, token: str) -> None:
        self._reservations.pop(token, None)

    @contextmanager
    def hold(self, estimate: Estimate) -> Iterator[str]:
        token = self.reserve(estimate)
        try:
            yield token
        finally:
            self.release(token)

    def recover_after_interruption(self) -> None:
        """Reservations represent process-local work and cannot survive a crash."""
        self._reservations.clear()

    @property
    def reserved_bytes(self) -> int:
        return sum(self._reservations.values())
