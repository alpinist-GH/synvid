"""Fetch, MLX-convert, and verify the Wan2.2 TI2V-5B checkpoint.

Unlike model_install.install_snapshot (which downloads exactly a spec's
allowed_files directly from its own repository and stores them as-is), this
model's app-owned snapshot is not the upstream repository's own files: it is
produced by a local MLX conversion (worker/vendor/mlx_video_wan2). Raw .pth
weights are fetched to an ephemeral, non-app-owned temporary directory,
verified against upstream Hugging Face LFS checksums, converted to
safetensors, and discarded. model_security.py forbids persisting
pickle-based files (.pth/.pt/.bin/.pkl) anywhere in the app-owned tree, so
the raw checkpoint must never reach paths.models.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
from typing import Callable

from huggingface_hub import HfApi, snapshot_download

from .model_install import ModelInstallError, _local_checksums, _write_json_atomically
from .model_security import ModelSecurityError, validate_download_request, verify_tree
from .models import ModelSpec
from .paths import AppPaths

# Exact upstream files this conversion needs. Deliberately literal filenames,
# not glob patterns: this is a smaller, hand-reviewed subset of the upstream
# repository, not "everything Diffusers-shaped" like _DIFFUSERS_FILES.
_UPSTREAM_RAW_FILES = (
    "config.json",
    "diffusion_pytorch_model-00001-of-00003.safetensors",
    "diffusion_pytorch_model-00002-of-00003.safetensors",
    "diffusion_pytorch_model-00003-of-00003.safetensors",
    "diffusion_pytorch_model.safetensors.index.json",
    "Wan2.2_VAE.pth",
    "models_t5_umt5-xxl-enc-bf16.pth",
    "google/umt5-xxl/special_tokens_map.json",
    "google/umt5-xxl/spiece.model",
    "google/umt5-xxl/tokenizer.json",
    "google/umt5-xxl/tokenizer_config.json",
)

_TOKENIZER_FILES = (
    "special_tokens_map.json", "spiece.model", "tokenizer.json", "tokenizer_config.json",
)

# Raw download (~32 GiB, ephemeral) and converted output (~23 GiB, app-owned)
# briefly coexist during conversion. Real measured peak, not spec.expected_size_gib
# (which describes only the final installed snapshot).
PEAK_INSTALL_BYTES = 58 * 1024**3


class WanMlxInstallError(RuntimeError):
    pass


def install_wan_mlx_snapshot(
    paths: AppPaths, spec: ModelSpec, progress: Callable[[float, str], None], cancelled: Callable[[], bool]
) -> dict[str, object]:
    validate_download_request(spec, spec.repository, spec.revision, False)
    paths.create()
    install_root = paths.models / spec.model_id
    snapshot = install_root / "snapshot"
    staging = install_root / ".staging"
    if snapshot.exists():
        raise WanMlxInstallError("a verified model snapshot is already installed; delete it explicitly before replacing it")
    install_root.mkdir(parents=True, exist_ok=True)

    progress(0.0, "Checking pinned model manifest")
    api = HfApi(token=None)
    remote_checksums: dict[str, str] = {}
    for item in api.list_repo_tree(spec.repository, revision=spec.revision, recursive=True, expand=True):
        if cancelled():
            raise InterruptedError("model download cancelled")
        name = getattr(item, "path", None)
        if not isinstance(name, str) or name not in _UPSTREAM_RAW_FILES:
            continue
        lfs = getattr(item, "lfs", None) or {}
        digest = lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)
        if isinstance(digest, str) and len(digest) == 64:
            remote_checksums[name] = digest.lower()
    if not remote_checksums:
        raise WanMlxInstallError("Pinned model manifest has no LFS checksum metadata.")

    try:
        with tempfile.TemporaryDirectory(prefix="synvid-wan-mlx-raw-") as raw_dir_name:
            raw_dir = Path(raw_dir_name)
            progress(0.02, "Downloading source weights (converted locally, never stored)")
            snapshot_download(
                spec.repository, revision=spec.revision, token=None, local_dir=raw_dir,
                allow_patterns=list(_UPSTREAM_RAW_FILES), max_workers=4,
            )
            if cancelled():
                raise InterruptedError("model download cancelled")

            progress(0.5, "Verifying source weights")
            for relative, expected in remote_checksums.items():
                path = raw_dir / relative
                if not path.is_file():
                    raise ModelSecurityError(f"expected source file missing: {relative}")
                if _sha256(path) != expected:
                    raise ModelSecurityError(f"checksum mismatch: {relative}")
            if cancelled():
                raise InterruptedError("model download cancelled")

            staging.mkdir(parents=True, exist_ok=True)
            progress(0.55, "Converting to MLX (bfloat16)")
            _convert(raw_dir, staging)
            if cancelled():
                raise InterruptedError("model download cancelled")

            progress(0.85, "Copying tokenizer")
            tokenizer_dir = staging / "tokenizer"
            tokenizer_dir.mkdir(parents=True, exist_ok=True)
            for name in _TOKENIZER_FILES:
                source = raw_dir / "google" / "umt5-xxl" / name
                if not source.is_file():
                    raise WanMlxInstallError(f"expected tokenizer file missing: {name}")
                (tokenizer_dir / name).write_bytes(source.read_bytes())
            # raw_dir (and every .pth/.pt/.bin it contains) is discarded here,
            # on exit of this `with` block, before verify_tree ever runs.

        if cancelled():
            raise InterruptedError("model download cancelled")
        progress(0.92, "Verifying converted model")
        checksums = _local_checksums(staging)
        verify_tree(staging, spec, checksums)
        installed_bytes = sum(path.stat().st_size for path in staging.rglob("*") if path.is_file())
        staging.rename(snapshot)
        manifest = install_root / f"{spec.model_id}.sha256.json"
        _write_json_atomically(manifest, checksums)
        progress(1.0, "Verified model installed")
        return {
            "model_id": spec.model_id,
            "revision": spec.revision,
            "files": len(checksums),
            "installed_bytes": installed_bytes,
        }
    except BaseException:
        # Retain a hidden staging directory for inspection/resume; it cannot
        # be loaded because only snapshot/ plus its manifest is trusted.
        raise


def _convert(checkpoint_dir: Path, output_dir: Path) -> None:
    from .vendor.mlx_video_wan2.wan_2 import convert as convert_module

    original_print = convert_module.__dict__.get("print")
    convert_module.print = lambda *args, **kwargs: None  # this worker's stdout is the JSON-lines protocol
    try:
        convert_module.convert_wan_checkpoint(
            str(checkpoint_dir), str(output_dir), dtype="bfloat16", model_version="2.2",
        )
    except Exception as error:
        raise WanMlxInstallError("MLX conversion failed") from error
    finally:
        if original_print is None:
            del convert_module.print
        else:
            convert_module.print = original_print


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
