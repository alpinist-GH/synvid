"""Offline validation for reviewed, immutable model snapshots."""

from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path
from typing import Mapping

from .models import ModelSpec


class ModelSecurityError(RuntimeError):
    pass


_FORBIDDEN_SUFFIXES = frozenset({".bin", ".ckpt", ".pkl", ".pickle", ".pt", ".pth", ".py", ".pyc"})


def validate_download_request(spec: ModelSpec, repository: str, revision: str, trust_remote_code: bool) -> None:
    """Reject all request-controlled locations and executable loading options."""
    if repository != spec.repository or revision != spec.revision:
        raise ModelSecurityError("model repository and revision must match the reviewed registry")
    if trust_remote_code:
        raise ModelSecurityError("remote model code is not permitted")


def verify_tree(root: Path, spec: ModelSpec, expected_sha256: Mapping[str, str]) -> None:
    """Verify an already-downloaded, reviewed file set before it can be loaded."""
    if not expected_sha256:
        raise ModelSecurityError("reviewed checksum manifest is required")
    seen: set[str] = set()
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if path.is_symlink() or not path.is_file():
            raise ModelSecurityError("model tree may contain only regular files")
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() in _FORBIDDEN_SUFFIXES:
            raise ModelSecurityError(f"unsafe model serialization: {relative}")
        if not any(fnmatch.fnmatchcase(relative, pattern) for pattern in spec.allowed_files):
            raise ModelSecurityError(f"unexpected model file: {relative}")
        expected = expected_sha256.get(relative)
        if expected is None:
            raise ModelSecurityError(f"unreviewed model file: {relative}")
        actual = _sha256(path)
        if actual != expected.lower():
            raise ModelSecurityError(f"checksum mismatch: {relative}")
        seen.add(relative)
    if seen != set(expected_sha256):
        raise ModelSecurityError("model tree is incomplete")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
