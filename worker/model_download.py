"""Pinned Hugging Face transport for the verified model installer."""

from __future__ import annotations

import fnmatch
from pathlib import Path
import threading
from typing import Callable

from huggingface_hub import HfApi, snapshot_download
from tqdm.auto import tqdm

from .model_install import ModelInstallError, RemoteFile, install_snapshot
from .models import ModelSpec
from .paths import AppPaths


def download_model(paths: AppPaths, spec: ModelSpec, progress: Callable[[float, str], None], cancelled: Callable[[], bool]) -> dict[str, object]:
    """Fetch only reviewed files at a pinned revision, then atomically verify/promote."""
    # Downloads begin anonymously. SynVid does not require, request, or read
    # a Hugging Face API token merely to open or use the application.
    token = None
    progress(0.0, "Checking pinned model manifest")
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
        size = getattr(item, "size", None)
        if not isinstance(size, int) or size < 0:
            size = lfs.get("size") if isinstance(lfs, dict) else getattr(lfs, "size", None)
        remote.append(RemoteFile(
            name,
            digest if isinstance(digest, str) and len(digest) == 64 else None,
            size if isinstance(size, int) and size >= 0 else None,
        ))
    if not remote or not any(item.sha256 is not None for item in remote):
        raise ModelInstallError("Pinned model manifest has no LFS checksum metadata.")

    total_bytes = sum(item.size or 0 for item in remote)

    def fetch(staging: Path) -> None:
        progress(0.02, "Downloading verified model files")
        if total_bytes <= 0:
            snapshot_download(
                spec.repository,
                revision=spec.revision,
                token=token,
                local_dir=staging,
                allow_patterns=list(spec.allowed_files),
                max_workers=4,
            )
        else:
            tracker = _ByteProgress(total_bytes, progress)

            class DownloadProgress(tqdm):
                """Forward Hugging Face's concurrent byte bars to the job."""

                def __init__(self, *args, **kwargs):
                    self._tracked_n = 0
                    self._track = str(kwargs.get("desc", "")).startswith("Reconstructing")
                    super().__init__(*args, **kwargs)
                    if self._track:
                        tracker.register(self, self.total)

                def update(self, n=1):
                    self._tracked_n = max(0, self._tracked_n + int(n or 0))
                    result = super().update(n)
                    if self._track:
                        tracker.update(self, self._tracked_n)
                    return result

                def close(self):
                    if self._track:
                        tracker.update(self, self._tracked_n)
                        tracker.unregister(self)
                    return super().close()

            snapshot_download(
                spec.repository,
                revision=spec.revision,
                token=token,
                local_dir=staging,
                allow_patterns=list(spec.allowed_files),
                max_workers=4,
                tqdm_class=DownloadProgress,
            )
        if cancelled(): raise InterruptedError("model download cancelled")

    progress(0.02, "Downloading verified model files")
    result = install_snapshot(paths, spec, remote, fetch)
    progress(0.95, "Verifying model files")
    progress(1.0, "Verified model installed")
    return result


class _ByteProgress:
    """Aggregate concurrent per-file tqdm bars into one bounded job fraction."""

    def __init__(self, total_bytes: int, progress: Callable[[float, str], None]):
        self.total_bytes = total_bytes
        self.progress = progress
        self._lock = threading.Lock()
        self._downloaded: dict[int, int] = {}

    def register(self, bar: tqdm, total: int | None) -> None:
        with self._lock:
            self._downloaded[id(bar)] = 0

    def update(self, bar: tqdm, downloaded: int) -> None:
        with self._lock:
            self._downloaded[id(bar)] = max(0, downloaded)
            transferred = sum(self._downloaded.values())
        fraction = min(1.0, transferred / self.total_bytes)
        self.progress(0.02 + (fraction * 0.90), f"Downloading model files · {round(fraction * 100)}%")

    def unregister(self, bar: tqdm) -> None:
        with self._lock:
            self._downloaded.pop(id(bar), None)
