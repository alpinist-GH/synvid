"""Verified model installation into an app-owned, immutable snapshot root.

Network clients remain in the small CLI boundary.  This module accepts a
reviewed registry entry plus injected metadata/downloader functions so its
security and atomic-promotion behaviour is testable without network access.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
from typing import Callable, Iterable

from .model_security import ModelSecurityError, validate_download_request, verify_tree
from .models import ModelSpec
from .paths import AppPaths


@dataclass(frozen=True)
class RemoteFile:
    """The minimal reviewed subset of a provider's file-metadata response."""

    name: str
    sha256: str | None


class ModelInstallError(RuntimeError):
    pass


def install_snapshot(
    paths: AppPaths,
    spec: ModelSpec,
    remote_files: Iterable[RemoteFile],
    download: Callable[[Path], None],
) -> dict[str, object]:
    """Download one pinned snapshot, verify it, and atomically make it usable.

    ``download`` receives only the installer-owned staging directory. It must
    not be able to override repository, revision, file patterns, or destination.
    """
    validate_download_request(spec, spec.repository, spec.revision, False)
    paths.create()
    install_root = paths.models / spec.model_id
    snapshot = install_root / "snapshot"
    staging = install_root / ".staging"
    if snapshot.exists():
        raise ModelInstallError("a verified model snapshot is already installed; delete it explicitly before replacing it")
    install_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        remote_checksums = {
            item.name: item.sha256.lower()
            for item in remote_files
            if item.sha256 is not None
        }
        download(staging)
        # huggingface_hub local_dir bookkeeping is transport state, never model
        # data. Remove it before the strict reviewed-data verification.
        shutil.rmtree(staging / ".cache", ignore_errors=True)
        _verify_upstream_lfs_checksums(staging, remote_checksums)
        checksums = _local_checksums(staging)
        verify_tree(staging, spec, checksums)
        installed_bytes = sum(path.stat().st_size for path in staging.rglob("*") if path.is_file())
        staging.rename(snapshot)
        manifest = install_root / f"{spec.model_id}.sha256.json"
        _write_json_atomically(manifest, checksums)
        return {
            "model_id": spec.model_id,
            "revision": spec.revision,
            "files": len(checksums),
            "installed_bytes": installed_bytes,
        }
    except BaseException:
        # Retain a hidden staging directory for explicit resume; it cannot be
        # loaded because only snapshot/ plus its manifest is trusted.
        raise


def _verify_upstream_lfs_checksums(root: Path, remote_checksums: dict[str, str]) -> None:
    for relative, expected in remote_checksums.items():
        path = root / relative
        if path.exists() and path.is_file():
            actual = _sha256(path)
            if actual != expected:
                raise ModelSecurityError(f"checksum mismatch: {relative}")


def _local_checksums(root: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ModelSecurityError("model tree may contain only regular files")
        if path.is_file():
            checksums[path.relative_to(root).as_posix()] = _sha256(path)
    return checksums


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomically(destination: Path, payload: dict[str, str]) -> None:
    temporary = destination.with_suffix(destination.suffix + ".partial")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2))
    temporary.replace(destination)
