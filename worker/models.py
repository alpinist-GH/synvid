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
    display_name: str
    reason: str
    capabilities: frozenset[Capability]
    profile: str
    repository: str
    revision: str
    license_name: str
    expected_size_gib: float
    requires_access_confirmation: bool
    allowed_files: tuple[str, ...]
    checksum_source: str
    supported_modes: frozenset[str] = frozenset({"text", "image"})

    def __post_init__(self) -> None:
        if not IMMUTABLE_REVISION.fullmatch(self.revision):
            raise ValueError("model revision must be a 40-character immutable commit")
        if not self.allowed_files:
            raise ValueError("model must declare allowed files")
        if not self.supported_modes.issubset({"text", "image"}):
            raise ValueError("model modes must be text and/or image")


# Revisions and estimated cache sizes were recorded from the official Hugging
# Face model metadata on 2026-08-07.  The source exposes LFS SHA-256 values for
# weight files; the future download flow must obtain the complete file manifest
# at *this exact commit*, review it, and pass it to model_security.verify_tree.
# Allowed patterns intentionally contain only inert Diffusers data/config files.
_DIFFUSERS_FILES = (
    "model_index.json", "scheduler/*.json", "text_encoder/*.json",
    "text_encoder/*.safetensors", "text_encoder_2/*.json",
    "text_encoder_2/*.safetensors", "tokenizer/*.json", "tokenizer/*.txt",
    "tokenizer/*.model", "tokenizer/merges.txt", "tokenizer/vocab.json",
    "tokenizer_2/*.json", "tokenizer_2/*.txt", "tokenizer_2/*.model",
    "tokenizer_2/merges.txt", "tokenizer_2/vocab.json",
    "transformer/*.json", "transformer/*.safetensors",
    "vae/*.json", "vae/*.safetensors", "ae.safetensors",
)

_HF_LFS = "Hugging Face LFS SHA-256 metadata at the pinned commit"

_TRANSFORMERS_FILES = (
    "config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json",
    "merges.txt", "vocab.json", "model.safetensors", "LICENSE", "README.md",
)

_HUNYUAN_DIFFUSERS_FILES = (
    "model_index.json", "guider/*.json", "scheduler/*.json",
    "feature_extractor/*.json", "image_encoder/*.json", "image_encoder/*.safetensors",
    "text_encoder/*.json", "text_encoder/*.safetensors",
    "text_encoder_2/*.json", "text_encoder_2/*.safetensors",
    "tokenizer/*.json", "tokenizer/*.txt", "tokenizer/*.model",
    "tokenizer/merges.txt", "tokenizer/vocab.json",
    "tokenizer_2/*.json", "tokenizer_2/*.txt", "tokenizer_2/*.model",
    "tokenizer_2/merges.txt", "tokenizer_2/vocab.json",
    "transformer/*.json", "transformer/*.safetensors",
    "vae/*.json", "vae/*.safetensors",
)

_HUNYUAN_LICENSE = "Tencent Hunyuan Community License Agreement (territory-restricted)"

REGISTRY = {
    "ltx-video": ModelSpec("ltx-video", "LTX Video", "Creates local videos from text and edits completed LTX videos.", frozenset({Capability.VIDEO_GENERATION, Capability.VIDEO_EDITING}), "shareable", "Lightricks/LTX-Video", "8984fa25007f376c1a299016d0957a37a2f797bb", "LTX-Video Open Weights License", 24.0, True, _DIFFUSERS_FILES + ("ltxv-2b-0.9.8-distilled.safetensors",), _HF_LFS),
    "flux-schnell": ModelSpec("flux-schnell", "FLUX.1-schnell", "Creates still images for prompts and Story Mode storyboards.", frozenset({Capability.IMAGE_GENERATION}), "shareable", "black-forest-labs/FLUX.1-schnell", "741f7c3ce8b383c54771c7003378a50191e9efe9", "Apache-2.0", 54.0, True, _DIFFUSERS_FILES + ("flux1-schnell.safetensors",), _HF_LFS),
    "flux-dev": ModelSpec("flux-dev", "FLUX.1-dev", "Optional higher-capacity still-image model for personal research only.", frozenset({Capability.IMAGE_GENERATION}), "personal-research", "black-forest-labs/FLUX.1-dev", "3de623fc3c33e44ffbe2bad470d0f45bccf2eb21", "FLUX.1-dev non-commercial", 54.0, True, _DIFFUSERS_FILES + ("LICENSE.md", "flux1-dev.safetensors"), _HF_LFS),
    "flux-kontext-dev": ModelSpec("flux-kontext-dev", "FLUX.1-Kontext-dev", "Optional instruction-based image editing for personal research only.", frozenset({Capability.IMAGE_GENERATION}), "personal-research", "black-forest-labs/FLUX.1-Kontext-dev", "24e9dedc4ef646698dc8eb4e18ae2cec3c9fea0d", "FLUX.1-dev non-commercial", 54.0, True, _DIFFUSERS_FILES + ("LICENSE.md", "flux1-kontext-dev.safetensors"), _HF_LFS),
    # The shareable image-editing choice.  This is intentionally not exposed
    # until a measured MPS profile exists; its sizeable checkpoint must not be
    # presented as a supported feature merely because it is permissively licensed.
    "qwen-image-edit": ModelSpec("qwen-image-edit", "Qwen Image Edit", "Edits a completed image from a text instruction.", frozenset({Capability.IMAGE_EDITING}), "shareable", "Qwen/Qwen-Image-Edit", "ac7f9318f633fc4b5778c59367c8128225f1e3de", "Apache-2.0", 57.7, True, _DIFFUSERS_FILES + ("processor/*.json",), _HF_LFS),
    "qwen-story-planner": ModelSpec("qwen-story-planner", "Qwen Story Planner", "Optional local draft-scene assistant; currently unavailable because its structured-output quality gate did not pass.", frozenset(), "shareable", "Qwen/Qwen2.5-1.5B-Instruct", "989aa7980e4cf806f80c7fef2b1adb7bc71aa306", "Apache-2.0", 2.9, False, _TRANSFORMERS_FILES, _HF_LFS),
    "wan2.1-1.3b": ModelSpec("wan2.1-1.3b", "Wan 2.1 1.3B", "Experimental text-to-video candidate; not exposed because its measured MPS output was not watchable.", frozenset({Capability.VIDEO_GENERATION}), "shareable", "Wan-AI/Wan2.1-T2V-1.3B-Diffusers", "0fad780a534b6463e45facd96134c9f345acfa5b", "Apache-2.0", 29.0, False, _DIFFUSERS_FILES, _HF_LFS),
    "wan2.1-14b": ModelSpec("wan2.1-14b", "Wan 2.1 14B", "Experimental text/image-to-video candidate; requires a separate measured memory strategy before use.", frozenset({Capability.VIDEO_GENERATION, Capability.VIDEO_EDITING}), "shareable", "Wan-AI/Wan2.1-T2V-14B-Diffusers", "38ec498cb3208fb688890f8cc7e94ede2cbd7f68", "Apache-2.0", 78.0, False, _DIFFUSERS_FILES, _HF_LFS),
    "wan2.2-ti2v-5b": ModelSpec("wan2.2-ti2v-5b", "Wan 2.2 TI2V 5B", "Experimental text-to-video test profile; MPS runtime passed, but the measured Diffusers output is blurry/overexposed and is not a quality-approved profile.", frozenset({Capability.VIDEO_GENERATION}), "shareable", "Wan-AI/Wan2.2-TI2V-5B-Diffusers", "bfbd0086538bbf9b0f7c1f1939879d65e1f872ce", "Apache-2.0", 34.2, False, _DIFFUSERS_FILES, _HF_LFS),
    "hunyuan15-480p-t2v": ModelSpec("hunyuan15-480p-t2v", "HunyuanVideo 1.5 480p T2V", "Experimental 8.3B text-to-video provider; requires a real MPS memory, cancellation, and watchability gate. The Tencent license excludes the EU, UK, and South Korea.", frozenset({Capability.VIDEO_GENERATION}), "personal-research", "hunyuanvideo-community/HunyuanVideo-1.5-Diffusers-480p_t2v", "286be7ce72277246578a3e3cc2487e95ddae5bcf", _HUNYUAN_LICENSE, 53.4, True, _HUNYUAN_DIFFUSERS_FILES, _HF_LFS, frozenset({"text"})),
    "hunyuan15-480p-i2v": ModelSpec("hunyuan15-480p-i2v", "HunyuanVideo 1.5 480p I2V", "Experimental 8.3B image-to-video provider; requires a real MPS memory, cancellation, and watchability gate. The Tencent license excludes the EU, UK, and South Korea.", frozenset({Capability.VIDEO_GENERATION}), "personal-research", "hunyuanvideo-community/HunyuanVideo-1.5-Diffusers-480p_i2v", "5a700ee883ff4c1b3d887ec4188755a7a5e2f698", _HUNYUAN_LICENSE, 54.2, True, _HUNYUAN_DIFFUSERS_FILES, _HF_LFS, frozenset({"image"})),
}


def resolve(capability: Capability, profile: str) -> tuple[ModelSpec, ...]:
    return tuple(spec for spec in REGISTRY.values() if capability in spec.capabilities and spec.profile == profile)
