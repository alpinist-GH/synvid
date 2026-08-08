"""App-owned paths.  No worker request is allowed to supply these roots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    root: Path
    database: Path
    models: Path
    outputs: Path
    temporary: Path
    logs: Path

    @classmethod
    def under(cls, application_support: Path) -> "AppPaths":
        root = application_support / "SynVid"
        return cls(
            root=root,
            database=root / "index.sqlite3",
            models=root / "models",
            outputs=root / "outputs",
            temporary=root / "temporary",
            logs=root / "logs",
        )

    def create(self) -> None:
        for path in (self.root, self.models, self.outputs, self.temporary, self.logs):
            path.mkdir(parents=True, exist_ok=True)
