"""Pinned model facts and profile-aware capability resolution.

This registry is metadata only.  It deliberately has no "download this URL"
entry point: a later explicit user confirmation is required before a downloader
is introduced.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .providers.base import Capability


IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    capabilities: frozenset[Capability]
    profile: str
    repository: str
    revision: str
    license_name: str
    expected_size_gib: float
    requires_access_confirmation: bool
    allowed_files: tuple[str, ...]
    checksum_source: str

    def __post_init__(self) -> None:
        if not IMMUTABLE_REVISION.fullmatch(self.revision):
            raise ValueError("model revision must be a 40-character immutable commit")
        if not self.allowed_files:
            raise ValueError("model must declare allowed files")


# Revisions and estimated cache sizes were recorded from the official Hugging
# Face model metadata on 2026-08-07.  The source exposes LFS SHA-256 values for
# weight files; the future download flow must obtain the complete file manifest
# at *this exact commit*, review it, and pass it to model_security.verify_tree.
# Allowed patterns intentionally contain only inert Diffusers data/config files.
_DIFFUSERS_FILES = (
    "model_index.json", "scheduler/*.json", "text_encoder/*.json",
    "text_encoder/*.safetensors", "text_encoder_2/*.json",
    "text_encoder_2/*.safetensors", "tokenizer/*.json", "tokenizer/*.txt",
    "tokenizer/*.model", "tokenizer_2/*.json", "tokenizer_2/*.txt",
    "tokenizer_2/*.model", "transformer/*.json", "transformer/*.safetensors",
    "vae/*.json", "vae/*.safetensors", "ae.safetensors",
)

_HF_LFS = "Hugging Face LFS SHA-256 metadata at the pinned commit"

REGISTRY = {
    "ltx-video": ModelSpec("ltx-video", frozenset({Capability.VIDEO_GENERATION, Capability.VIDEO_EDITING}), "shareable", "Lightricks/LTX-Video", "8984fa25007f376c1a299016d0957a37a2f797bb", "LTX-Video Open Weights License", 24.0, True, _DIFFUSERS_FILES + ("ltxv-2b-0.9.8-distilled.safetensors",), _HF_LFS),
    "flux-schnell": ModelSpec("flux-schnell", frozenset({Capability.IMAGE_GENERATION}), "shareable", "black-forest-labs/FLUX.1-schnell", "741f7c3ce8b383c54771c7003378a50191e9efe9", "Apache-2.0", 54.0, True, _DIFFUSERS_FILES + ("flux1-schnell.safetensors",), _HF_LFS),
    "flux-dev": ModelSpec("flux-dev", frozenset({Capability.IMAGE_GENERATION}), "personal-research", "black-forest-labs/FLUX.1-dev", "3de623fc3c33e44ffbe2bad470d0f45bccf2eb21", "FLUX.1-dev non-commercial", 54.0, True, _DIFFUSERS_FILES + ("LICENSE.md", "flux1-dev.safetensors"), _HF_LFS),
    "flux-kontext-dev": ModelSpec("flux-kontext-dev", frozenset({Capability.IMAGE_GENERATION}), "personal-research", "black-forest-labs/FLUX.1-Kontext-dev", "24e9dedc4ef646698dc8eb4e18ae2cec3c9fea0d", "FLUX.1-dev non-commercial", 54.0, True, _DIFFUSERS_FILES + ("LICENSE.md", "flux1-kontext-dev.safetensors"), _HF_LFS),
    "wan2.1-1.3b": ModelSpec("wan2.1-1.3b", frozenset({Capability.VIDEO_GENERATION}), "shareable", "Wan-AI/Wan2.1-T2V-1.3B-Diffusers", "0fad780a534b6463e45facd96134c9f345acfa5b", "Apache-2.0", 29.0, False, _DIFFUSERS_FILES, _HF_LFS),
    "wan2.1-14b": ModelSpec("wan2.1-14b", frozenset({Capability.VIDEO_GENERATION, Capability.VIDEO_EDITING}), "shareable", "Wan-AI/Wan2.1-T2V-14B-Diffusers", "38ec498cb3208fb688890f8cc7e94ede2cbd7f68", "Apache-2.0", 78.0, False, _DIFFUSERS_FILES, _HF_LFS),
}


def resolve(capability: Capability, profile: str) -> tuple[ModelSpec, ...]:
    return tuple(spec for spec in REGISTRY.values() if capability in spec.capabilities and spec.profile == profile)
