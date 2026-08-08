#!/usr/bin/env python3
"""Explicit, reviewable installation of the sole Stage 1 LTX checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

# Scripts run from this directory, while worker is a sibling of scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from huggingface_hub import HfApi, snapshot_download

from worker.model_security import ModelSecurityError, verify_tree
from worker.models import REGISTRY
from worker.paths import AppPaths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-support", type=Path, required=True)
    parser.add_argument("--confirm", action="store_true", help="confirm the displayed model/license/download facts")
    args = parser.parse_args()
    spec = REGISTRY["ltx-video"]
    if not args.confirm:
        print(json.dumps({
            "repository": spec.repository, "revision": spec.revision,
            "expected_size_gib": spec.expected_size_gib, "license": spec.license_name,
            "requires_access_confirmation": spec.requires_access_confirmation,
        }, sort_keys=True), file=sys.stderr)
        print("refusing download without --confirm", file=sys.stderr)
        return 2

    paths = AppPaths.under(args.app_support)
    paths.create()
    install_root = paths.models / "ltx-video"
    snapshot = install_root / "snapshot"
    staging = install_root / ".staging"
    if snapshot.exists():
        raise RuntimeError("a verified LTX snapshot is already installed; delete it explicitly in the app before replacing it")
    # Keep an interrupted app-owned staging tree so Hugging Face can resume
    # verified blob transfers rather than re-downloading them.
    staging.mkdir(parents=True, exist_ok=True)
    try:
        info = HfApi().model_info(spec.repository, revision=spec.revision, files_metadata=True)
        lfs_checksums = {
            sibling.rfilename: sibling.lfs.sha256
            for sibling in info.siblings
            if sibling.lfs is not None and sibling.lfs.sha256 is not None
        }
        snapshot_download(
            repo_id=spec.repository,
            revision=spec.revision,
            local_dir=staging,
            allow_patterns=list(spec.allowed_files),
        )
        # local_dir uses this bookkeeping directory for resumable transfers.
        # It is not model data and must never be promoted into a trusted model
        # snapshot or considered by the strict reviewed-file allow-list.
        shutil.rmtree(staging / ".cache", ignore_errors=True)
        # Hugging Face supplies SHA-256 for LFS blobs. Small configuration files
        # are ordinary Git objects, so record their post-download SHA-256 only
        # after every available upstream LFS checksum has matched.
        for relative, expected in lfs_checksums.items():
            path = staging / relative
            if path.exists():
                import hashlib
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual != expected.lower():
                    raise ModelSecurityError(f"checksum mismatch: {relative}")
        checksums = {}
        import hashlib
        for path in staging.rglob("*"):
            if path.is_file() and not path.is_symlink():
                checksums[path.relative_to(staging).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        verify_tree(staging, spec, checksums)
        staging.rename(snapshot)
        (install_root / "ltx-video.sha256.json").write_text(json.dumps(checksums, sort_keys=True, indent=2))
    except BaseException:
        # Never promote a partial snapshot.  Retaining it only in the hidden
        # app-owned staging directory permits resumable, checksum-verified use.
        raise
    print(json.dumps({"installed": str(snapshot), "revision": spec.revision, "files": len(checksums)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
