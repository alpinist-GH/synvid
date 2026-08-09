"""Pinned Hugging Face transport for the verified model installer."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Callable

from huggingface_hub import HfApi, snapshot_download

from .model_install import ModelInstallError, RemoteFile, install_snapshot
from .models import ModelSpec
from .paths import AppPaths


def download_model(paths: AppPaths, spec: ModelSpec, progress: Callable[[float, str], None], cancelled: Callable[[], bool]) -> dict[str, object]:
    """Fetch only reviewed files at a pinned revision, then atomically verify/promote."""
    # Downloads begin anonymously. SynVid does not require, request, or read
    # a Hugging Face API token merely to open or use the application.
    token = None
    progress(0.02, "Checking pinned model manifest")
    api = HfApi(token=token)
    remote = []
    for item in api.list_repo_tree(spec.repository, revision=spec.revision, recursive=True, expand=True):
        if cancelled(): raise InterruptedError("model download cancelled")
        name = getattr(item, "path", None)
        if not isinstance(name, str) or not any(fnmatch.fnmatchcase(name, pattern) for pattern in spec.allowed_files):
            continue
        lfs = getattr(item, "lfs", None) or {}
        # huggingface_hub's BlobLfsInfo exposes the content hash as `sha256`
        # (it is a dict subclass, so `.get` succeeds but silently returns
        # None for the wrong key rather than raising).
        digest = lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)
        remote.append(RemoteFile(name, digest if isinstance(digest, str) and len(digest) == 64 else None))
    if not remote or not any(item.sha256 is not None for item in remote):
        raise ModelInstallError("Pinned model manifest has no LFS checksum metadata.")

    def fetch(staging: Path) -> None:
        progress(0.05, "Downloading verified model files")
        snapshot_download(spec.repository, revision=spec.revision, token=token, local_dir=staging, allow_patterns=list(spec.allowed_files), max_workers=4)
        if cancelled(): raise InterruptedError("model download cancelled")

    result = install_snapshot(paths, spec, remote, fetch)
    progress(1.0, "Verified model installed")
    return result
