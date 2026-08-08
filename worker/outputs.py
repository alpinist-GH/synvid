"""Immutable output metadata and contained, atomic finalization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import uuid


class OutputError(ValueError):
    pass


@dataclass(frozen=True)
class OutputPaths:
    output_id: str
    partial_dir: Path
    final_dir: Path


def allocate(output_root: Path) -> OutputPaths:
    output_id = str(uuid.uuid4())
    partial_dir = output_root / ".partial" / output_id
    final_dir = output_root / output_id
    partial_dir.mkdir(parents=True, exist_ok=False)
    return OutputPaths(output_id=output_id, partial_dir=partial_dir, final_dir=final_dir)


def resolve_owned_file(output_root: Path, output_id: str, relative_path: str) -> Path:
    if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
        raise OutputError("output path must be relative and contained")
    candidate = (output_root / output_id / relative_path).resolve()
    expected_root = (output_root / output_id).resolve()
    if expected_root not in candidate.parents:
        raise OutputError("output path escapes output root")
    return candidate


def promote(paths: OutputPaths) -> Path:
    if paths.final_dir.exists():
        raise OutputError("output ID already exists")
    os.replace(paths.partial_dir, paths.final_dir)
    return paths.final_dir
