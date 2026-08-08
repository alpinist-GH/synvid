#!/usr/bin/env python3
"""Explicit installation of one reviewed model from the SynVid registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from huggingface_hub import HfApi, snapshot_download

from worker.model_install import RemoteFile, install_snapshot
from worker.models import REGISTRY
from worker.paths import AppPaths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-support", type=Path, required=True)
    parser.add_argument("--model", choices=sorted(REGISTRY), required=True)
    parser.add_argument("--confirm", action="store_true", help="confirm the displayed model/license/download facts")
    args = parser.parse_args()
    spec = REGISTRY[args.model]
    facts = {
        "repository": spec.repository,
        "revision": spec.revision,
        "expected_size_gib": spec.expected_size_gib,
        "license": spec.license_name,
        "requires_access_confirmation": spec.requires_access_confirmation,
    }
    if not args.confirm:
        print(json.dumps(facts, sort_keys=True), file=sys.stderr)
        print("refusing download without --confirm", file=sys.stderr)
        return 2

    info = HfApi().model_info(spec.repository, revision=spec.revision, files_metadata=True)
    files = [
        RemoteFile(sibling.rfilename, sibling.lfs.sha256 if sibling.lfs is not None else None)
        for sibling in info.siblings
    ]

    def download(staging: Path) -> None:
        snapshot_download(
            repo_id=spec.repository,
            revision=spec.revision,
            local_dir=staging,
            allow_patterns=list(spec.allowed_files),
        )

    result = install_snapshot(AppPaths.under(args.app_support), spec, files, download)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
