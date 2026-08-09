"""Pinned Hugging Face transport for the verified model installer."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Callable

from huggingface_hub import HfApi, snapshot_download

from .model_install import ModelInstallError, RemoteFile, install_snapshot
from .models import ModelSpec
from .paths import AppPaths


def download_model(paths: AppPaths, spec: ModelSpec, progress: Callable[[float, str], None], cancelled: Callable[[], bool]) -> dict[str, object]:
    """Fetch only reviewed files at a pinned revision, then atomically verify/promote."""
    token = os.environ.get("SYNVID_HF_TOKEN")
    if spec.requires_access_confirmation and not token:
        raise ModelInstallError("This model requires an approved Hugging Face credential in Keychain.")
    progress(0.02, "Checking pinned model manifest")
    api = HfApi(token=token)
    remote = []
    for item in api.list_repo_tree(spec.repository, revision=spec.revision, recursive=True, expand=True):
        if cancelled(): raise InterruptedError("model download cancelled")
        name = getattr(item, "path", None)
        if not isinstance(name, str) or not any(fnmatch.fnmatchcase(name, pattern) for pattern in spec.allowed_files):
            continue
        lfs = getattr(item, "lfs", None) or {}
        digest = lfs.get("oid") if isinstance(lfs, dict) else getattr(lfs, "oid", None)
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
